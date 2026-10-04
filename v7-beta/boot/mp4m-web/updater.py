#!/usr/bin/env python3
"""Update the MP4MUSEUM software from GitHub.

Part of https://github.com/lotech/mp4museum, a fork of MP4MUSEUM by
Julius Schmiedel (http://mp4museum.org). Licensed under the GNU GPL v3, see LICENSE.

The web interface lives on the boot partition (/boot/mp4m-web), because the
root filesystem is a read-only RAM overlay. Updating swaps that folder for the
one in the latest commit, and updates the player script (/boot/mp4museum.py)
unless it has been edited. Files outside /boot can only be changed by running
install.sh with the overlay turned off; the update says when that is needed.

Run on the Pi:
    sudo mp4m-update            check for an update, install it, offer to reboot
    sudo mp4m-update --check    only check
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request

import system

UPDATE_CONFIG_FILE = os.path.join(system.BOOT_PATH, 'mp4m-update.txt')
DEFAULT_REPO = 'lotech/mp4museum'
DEFAULT_BRANCH = 'master'

APP_DIR = os.path.join(system.BOOT_PATH, 'mp4m-web')
MANIFEST_NAME = 'installed.json'

# Where things are in the repository
APP_SOURCE = 'v7-beta/boot/mp4m-web'
PLAYER_SOURCE = 'v7-beta/boot/mp4museum.py'
# Outside /boot, so only install.sh can update these
SYSTEM_FILES = {
    'v7-beta/home/pi/.bashrc': '/home/pi/.bashrc',
    'v7-beta/home/pi/mp4museum-boot.mp4': '/home/pi/mp4museum-boot.mp4',
    'v7-beta/home/pi/mp4m-v7beta.jpg': '/home/pi/mp4m-v7beta.jpg',
    'v7-beta/etc/systemd/system/mp4m-webservice.service': '/etc/systemd/system/mp4m-webservice.service',
    'v7-beta/usr/local/bin/mp4m-update': '/usr/local/bin/mp4m-update',
}

# Player scripts that count as not edited: the one on the v7 beta image, and
# versions from this repository from before the updater recorded what it installed
KNOWN_PLAYER_HASHES = {
    'cf9b58ab99c86b14ddf6662cf0faeebe1088718d3fcb176343d86af1edb93d39',
    '09969f538de286b09f055d6ff95f043926648603e582565ab5d1ba587c7978dc',
    'fddec5a69429197a53d9470f34d694622055d06c53a1fc87804c1f0525f548fa',
}

DOWNLOAD_LIMIT = 50 * 1024 * 1024
TIMEOUT = 30
USER_AGENT = 'mp4museum-updater'


class UpdateError(Exception):
    """An update failed before anything was changed."""


# One install at a time in this process; writable() keeps other processes out of /boot
_install_lock = threading.Lock()


# ----- Settings and installed version ----- #
def read_config():
    """Repository and branch to update from, from /boot/mp4m-update.txt (repo=..., branch=...)."""
    config = {'repo': DEFAULT_REPO, 'branch': DEFAULT_BRANCH}
    try:
        with open(UPDATE_CONFIG_FILE, 'r') as f:
            for line in f:
                key, sep, value = line.partition('=')
                if sep and key.strip() in config and value.strip():
                    config[key.strip()] = value.strip()
    except OSError:
        pass
    return config

def installed_version():
    """What is installed: {'commit', 'date', 'repo', 'branch', 'player_sha256'}, or {}."""
    try:
        with open(os.path.join(APP_DIR, MANIFEST_NAME), 'r') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ----- GitHub ----- #
def _open(url, accept=None):
    headers = {'User-Agent': USER_AGENT}
    if accept:
        headers['Accept'] = accept
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (404, 422):
            raise UpdateError(f"GitHub doesn't know that repository or branch; check {UPDATE_CONFIG_FILE}.")
        if e.code == 429 or (e.code == 403 and e.headers.get('X-RateLimit-Remaining') == '0'):
            raise UpdateError("GitHub is limiting requests from this network (60 an hour). Try again later.")
        raise UpdateError(f"GitHub answered with an error ({e.code}).")
    except OSError as e:
        raise UpdateError(f"Couldn't reach GitHub ({e}). Updating needs an internet connection.")

def latest_commit(repo, branch):
    """The newest commit on the branch: {'commit', 'date', 'message'}."""
    try:
        with _open(f'https://api.github.com/repos/{repo}/commits/{branch}', 'application/vnd.github+json') as response:
            data = json.load(response)
        return {
            'commit': data['sha'],
            'date': data['commit']['committer']['date'],
            'message': data['commit']['message'].split('\n')[0],
        }
    except OSError as e:
        raise UpdateError(f"The connection to GitHub broke off ({e}).")
    except (ValueError, KeyError, TypeError):
        raise UpdateError("GitHub sent an unexpected answer.")

def download(repo, commit):
    """The repository at this commit, as a .tar.gz."""
    try:
        with _open(f'https://github.com/{repo}/archive/{commit}.tar.gz') as response:
            data = response.read(DOWNLOAD_LIMIT + 1)
    except OSError as e:
        raise UpdateError(f"The download from GitHub broke off ({e}).")
    if len(data) > DOWNLOAD_LIMIT:
        raise UpdateError("The download is unexpectedly large.")
    return data

def extract(archive, destination):
    """Unpack a GitHub archive safely and return the repository folder inside it."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            members = []
            for member in tar.getmembers():
                parts = member.name.split('/')
                # Only plain files and folders inside the archive's single top folder
                if member.name.startswith('/') or '..' in parts or not (member.isfile() or member.isdir()):
                    continue
                members.append(member)
            tops = {m.name.split('/')[0] for m in members}
            if len(tops) != 1:
                raise UpdateError("The download doesn't look like a GitHub archive.")
            tar.extractall(destination, members=members)
            return os.path.join(destination, tops.pop())
    except (tarfile.TarError, OSError) as e:
        raise UpdateError(f"Couldn't unpack the download: {e}")


# ----- Installing ----- #
def check_source(source_root):
    """Make sure a downloaded copy has a working web interface before installing it."""
    app = os.path.join(source_root, APP_SOURCE)
    if not os.path.isfile(os.path.join(source_root, PLAYER_SOURCE)):
        raise UpdateError(f"That version has no player script ({PLAYER_SOURCE}).")
    for name in ('webservice.py', 'system.py', 'updater.py'):
        if not os.path.isfile(os.path.join(app, name)):
            raise UpdateError(f"That version doesn't have the web interface in {APP_SOURCE} "
                              f"(missing {name}), so it can't be installed this way.")
    for folder, _, files in os.walk(app):
        for name in files:
            if name.endswith('.py'):
                path = os.path.join(folder, name)
                try:
                    with open(path, 'r') as f:
                        compile(f.read(), path, 'exec')
                except (SyntaxError, ValueError, OSError) as e:
                    raise UpdateError(f"The new version has an error in {name}: {e}")

def copy_tree(source, destination):
    """Copy a folder. shutil.copytree also copies permissions, which the FAT boot partition refuses."""
    for folder, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        target = os.path.join(destination, os.path.relpath(folder, source))
        os.makedirs(target, exist_ok=True)
        for name in files:
            if not name.endswith('.pyc'):
                shutil.copyfile(os.path.join(folder, name), os.path.join(target, name))

def changed_system_files(source_root):
    """Files outside /boot that differ from the new version."""
    changed = []
    for source, target in SYSTEM_FILES.items():
        source_path = os.path.join(source_root, source)
        if not os.path.isfile(source_path):
            continue
        if not os.path.isfile(target) or file_sha256(source_path) != file_sha256(target):
            changed.append(target)
    return changed

def install_from(source_root, version, check_system_files=True):
    """Install the web interface and player from a copy of the repository.

    version: {'commit', 'date', 'repo', 'branch'} describing that copy.
    Returns a summary: {'version', 'player': 'updated'|'unchanged'|'kept', 'system_files': [...]}.
    Raises UpdateError if the copy is unusable (nothing changed); other errors mean
    the update stopped part way.
    """
    check_source(source_root)
    if not _install_lock.acquire(blocking=False):
        raise UpdateError("Another update is already running.")
    try:
        player = _install(source_root, version)
    finally:
        _install_lock.release()
    return {'version': version, 'player': player,
            'system_files': changed_system_files(source_root) if check_system_files else []}

def official_player_hashes(manifest):
    """Player scripts earlier updates installed; a player matching one of them hasn't been edited."""
    hashes = list(manifest.get('official_player_hashes') or [])
    if manifest.get('player_sha256') and manifest['player_sha256'] not in hashes:
        hashes.insert(0, manifest['player_sha256'])
    return hashes

def _install(source_root, version):
    new_player = os.path.join(source_root, PLAYER_SOURCE)
    new_player_hash = file_sha256(new_player)

    new_dir = APP_DIR + '.new'
    old_dir = APP_DIR + '.old'
    # writable() holds the /boot lock, so no other process installs while this one reads what is installed
    with system.writable(system.BOOT_PATH):
        previous_players = official_player_hashes(installed_version())
        for leftover in (new_dir, old_dir):
            shutil.rmtree(leftover, ignore_errors=True)
        copy_tree(os.path.join(source_root, APP_SOURCE), new_dir)
        # The previous players stay listed, so if the player can't be written below (or the power
        # goes off first), the next update still sees the old player as unedited
        manifest = dict(version, player_sha256=new_player_hash,
                        official_player_hashes=([new_player_hash] + [h for h in previous_players if h != new_player_hash])[:20])
        # Written last: a .new folder with a manifest is complete (mp4m-update --recover relies on this)
        with open(os.path.join(new_dir, MANIFEST_NAME), 'w') as f:
            json.dump(manifest, f, indent=2)

        # On the SD card before the swap, so a power cut can't leave half-written files in place
        os.sync()
        # Swap the folders; the running web interface keeps its loaded code until it restarts
        if os.path.exists(APP_DIR):
            os.rename(APP_DIR, old_dir)
        try:
            os.rename(new_dir, APP_DIR)
        except OSError:
            if os.path.exists(old_dir) and not os.path.exists(APP_DIR):
                os.rename(old_dir, APP_DIR)
            raise
        # Keep the old copy until the new one is safely on the card
        os.sync()
        shutil.rmtree(old_dir, ignore_errors=True)

        # Decided and written under the player lock, so a script saved in the web interface
        # at the same moment is never overwritten
        with system.player_lock:
            current_player_hash = file_sha256(system.SCRIPT_FILE) if os.path.isfile(system.SCRIPT_FILE) else None
            if current_player_hash == new_player_hash:
                return 'unchanged'
            with open(new_player, 'r') as f:
                new_player_text = f.read()
            if (current_player_hash is None or current_player_hash in previous_players
                    or current_player_hash in KNOWN_PLAYER_HASHES):
                system.write_file(system.SCRIPT_FILE, new_player_text)
                return 'updated'
            # Edited on this player: keep it, and put the new version next to it
            system.write_file(system.SCRIPT_FILE + '.new', new_player_text)
            return 'kept'

def _tree_hashes(root):
    """{relative path: sha256} of a web interface folder, without the manifest and cached bytecode."""
    hashes = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in files:
            if name.endswith('.pyc') or (folder == root and name == MANIFEST_NAME):
                continue
            path = os.path.join(folder, name)
            hashes[os.path.relpath(path, root)] = file_sha256(path)
    return hashes

def matches_installed(source_root):
    """True if this copy is exactly what is installed: web interface, player, and the files
    outside /boot (otherwise the update is offered, and says which files need install.sh)."""
    return (os.path.isdir(APP_DIR)
            and _tree_hashes(os.path.join(source_root, APP_SOURCE)) == _tree_hashes(APP_DIR)
            and os.path.isfile(system.SCRIPT_FILE)
            and file_sha256(system.SCRIPT_FILE) == file_sha256(os.path.join(source_root, PLAYER_SOURCE))
            and not changed_system_files(source_root))

# Commits already compared with the installed files and found different, with what was
# installed at the time (no need to download them again unless that changed)
_differs_from_local_copy = {}

def _installed_fingerprint():
    player = file_sha256(system.SCRIPT_FILE) if os.path.isfile(system.SCRIPT_FILE) else ''
    tree = _tree_hashes(APP_DIR) if os.path.isdir(APP_DIR) else {}
    return hashlib.sha256(json.dumps([tree, player], sort_keys=True).encode()).hexdigest()

def identify_local_copy(latest, config=None, record=True):
    """install.sh can't tell which commit it installed ("local copy"). If the installed files
    are exactly this commit, record it (unless record=False), so the update check doesn't offer
    the same version. Returns True if they match."""
    config = config or read_config()
    fingerprint = _installed_fingerprint()
    if _differs_from_local_copy.get(latest['commit']) == fingerprint:
        return False
    archive = download(config['repo'], latest['commit'])
    with tempfile.TemporaryDirectory(prefix='mp4m-update-') as temp:
        source_root = extract(archive, temp)
        # Compare first: reading doesn't need /boot writable
        if not matches_installed(source_root):
            _differs_from_local_copy[latest['commit']] = fingerprint
            return False
        if not record:
            return True
        if not _install_lock.acquire(blocking=False):
            raise UpdateError("An update is already running.")
        try:
            with system.writable(system.BOOT_PATH):
                # Again under the lock, in case something changed in the meantime
                previous = installed_version()
                if previous.get('commit') != 'local' or not matches_installed(source_root):
                    return False
                player_hash = file_sha256(system.SCRIPT_FILE)
                manifest = dict(latest, repo=config['repo'], branch=config['branch'], player_sha256=player_hash,
                                official_player_hashes=([player_hash] + [h for h in official_player_hashes(previous)
                                                                         if h != player_hash])[:20])
                system.write_file(os.path.join(APP_DIR, MANIFEST_NAME), json.dumps(manifest, indent=2))
        finally:
            _install_lock.release()
    return True

def update(latest=None, config=None):
    """Download a commit (default: the newest on the configured branch) and install it.

    latest: the {'commit', 'date', 'message'} from latest_commit(), if already fetched.
    """
    config = config or read_config()
    latest = latest or latest_commit(config['repo'], config['branch'])
    archive = download(config['repo'], latest['commit'])
    with tempfile.TemporaryDirectory(prefix='mp4m-update-') as temp:
        source_root = extract(archive, temp)
        return install_from(source_root, dict(latest, repo=config['repo'], branch=config['branch']))

def describe(summary):
    """Lines describing what an update did, for the terminal and the web interface."""
    version = summary['version']
    if version['commit'] == 'local':
        lines = ["Installed from a local copy of the repository."]
    else:
        lines = [f"Installed version {version['commit'][:7]} ({version.get('date', '')[:10]})."]
    if summary['player'] == 'updated':
        lines.append(f"The player script {system.SCRIPT_FILE} was updated.")
    elif summary['player'] == 'kept':
        lines.append(f"The player script {system.SCRIPT_FILE} has been edited on this player, so it was kept. "
                     f"The new version is in {system.SCRIPT_FILE}.new.")
    if summary['system_files']:
        lines.append("These files outside /boot also changed and need install.sh (with the overlay off): "
                     + ', '.join(summary['system_files']) + '.')
    return lines


# ----- Command line ----- #
def ask(question):
    try:
        return input(question + ' [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False

def main():
    parser = argparse.ArgumentParser(description="Update the MP4MUSEUM software from GitHub.")
    parser.add_argument('--check', action='store_true', help="only check whether an update is available")
    parser.add_argument('--branch', help="update from this branch instead of the configured one")
    parser.add_argument('--force', action='store_true', help="install even if this version is already installed")
    parser.add_argument('--from-dir', metavar='REPO', help="install from a copy of the repository on this Pi (used by install.sh)")
    parser.add_argument('--yes', action='store_true', help="don't ask before installing")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Please run with sudo.")

    try:
        if args.from_dir:
            # install.sh installs the files outside /boot itself
            summary = install_from(os.path.abspath(args.from_dir),
                                   {'commit': 'local', 'date': '', 'repo': '', 'branch': ''},
                                   check_system_files=False)
            print('\n'.join(describe(summary)))
            return

        config = read_config()
        if args.branch:
            config['branch'] = args.branch
        installed = installed_version()
        print(f"Installed: {installed.get('commit', 'unknown')[:7]} {installed.get('date', '')[:10]}"
              + (f"  (branch {installed['branch']})" if installed.get('branch') else ''))
        print(f"Checking {config['repo']} branch {config['branch']}...")
        latest = latest_commit(config['repo'], config['branch'])
        print(f"Latest:    {latest['commit'][:7]} {latest['date'][:10]}  {latest['message']}")

        if latest['commit'] != installed.get('commit') and installed.get('commit') == 'local' and not args.force:
            print("Comparing the local copy with the latest version...")
            try:
                if identify_local_copy(latest, config, record=not args.check):
                    print("Already up to date (the local copy is this version).")
                    return
            except Exception as e:
                print(f"Couldn't compare the local copy: {e}")
        if latest['commit'] == installed.get('commit') and not args.force:
            print("Already up to date.")
            return
        if args.check:
            print("An update is available. Run 'sudo mp4m-update' to install it.")
            return
        if not args.yes and not ask("Install this update?"):
            return

        # held until the web interface has restarted (which would stop making a card half way)
        busy = system.try_busy_lock()
        if busy is None:
            sys.exit("A card is being made in the web interface: update when it's done.")
        summary = update(latest, config)
    except UpdateError as e:
        sys.exit(f"{e} Nothing was changed.")
    except Exception as e:
        sys.exit(f"The update stopped part way: {e}. Run 'sudo mp4m-update --force' to try again.")

    print('\n'.join(describe(summary)))
    # Start the new web interface now; the player picks up changes when it restarts
    subprocess.run(['systemctl', 'restart', 'mp4m-webservice'], check=False)
    if ask("Reboot now so the player uses the new version?"):
        subprocess.run(['reboot'], check=False)


if __name__ == '__main__':
    main()
