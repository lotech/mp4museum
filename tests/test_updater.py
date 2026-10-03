"""Software updates: mp4m-update and the web interface's Software Update.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import email.message
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import threading
import time
import urllib.error
from pathlib import Path

import pytest

import system
import updater
import webservice
from conftest import REPO, make_archive

V7_IMAGE_PLAYER = Path(__file__).parent / 'data' / 'v7-image-player.py'
LOCAL = {'commit': 'local', 'date': '', 'repo': '', 'branch': ''}


def installed():
    return updater.installed_version()


def player_text():
    return open(system.SCRIPT_FILE).read()


# ----- Installing ----- #
def test_update_replaces_the_v7_image_player(pi, github):
    shutil.copy(str(V7_IMAGE_PLAYER), system.SCRIPT_FILE)
    summary = updater.update()
    assert summary['player'] == 'updated' and player_text() == github.player.read_text()
    assert os.path.isfile(os.path.join(updater.APP_DIR, 'webservice.py'))
    assert os.path.isdir(os.path.join(updater.APP_DIR, 'templates'))
    assert not os.path.exists(updater.APP_DIR + '.new') and not os.path.exists(updater.APP_DIR + '.old')
    assert installed()['commit'] == github.latest['commit']
    assert installed()['player_sha256'] == updater.file_sha256(system.SCRIPT_FILE)
    assert pi.mounts()[0] == ['mount', '-o', 'remount,rw', str(pi.boot)]
    assert pi.mounts()[-1] == ['mount', '-o', 'remount,ro', str(pi.boot)]


def test_edited_player_is_kept(pi, github):
    updater.update()
    with open(system.SCRIPT_FILE, 'a') as f:
        f.write('\n# my change\n')
    with github.player.open('a') as f:
        f.write('# upstream change\n')
    github.release('def5678')
    summary = updater.update()
    assert summary['player'] == 'kept' and '# my change' in player_text()
    assert '# upstream change' in open(system.SCRIPT_FILE + '.new').read()
    assert any('has been edited' in line for line in updater.describe(summary))

    # once the new player is taken over, the next update counts it as unedited
    shutil.copy(system.SCRIPT_FILE + '.new', system.SCRIPT_FILE)
    with github.player.open('a') as f:
        f.write('# another upstream change\n')
    github.release('aaa0000')
    assert updater.update()['player'] == 'updated'
    hashes = installed()['official_player_hashes']
    assert len(hashes) >= 2 and hashes[0] == installed()['player_sha256']


def test_player_from_an_earlier_version_counts_as_unedited(pi, github):
    # what the merged v7 fork installed before the updater recorded anything
    subprocess.run(['git', '-C', str(REPO), 'show', 'c90e6d6:v7-beta/boot/mp4museum.py'],
                   stdout=open(system.SCRIPT_FILE, 'w'), check=True)
    with github.player.open('a') as f:
        f.write('# newer\n')
    assert updater.update()['player'] == 'updated'


def test_broken_version_is_refused(pi, github):
    updater.update()
    before = open(os.path.join(updater.APP_DIR, 'webservice.py')).read()
    with open(str(Path(github.source) / 'v7-beta/boot/mp4m-web/webservice.py'), 'a') as f:
        f.write('\ndef broken(:\n')
    github.release('bad0000')
    with pytest.raises(updater.UpdateError, match='error in webservice.py'):
        updater.update()
    assert open(os.path.join(updater.APP_DIR, 'webservice.py')).read() == before


def test_version_without_the_web_interface_is_refused(pi, github):
    shutil.rmtree(str(Path(github.source) / 'v7-beta/boot/mp4m-web'))
    with pytest.raises(updater.UpdateError, match="doesn't have the web interface"):
        updater.update()


def test_changed_files_outside_boot_are_reported(pi, github, monkeypatch):
    bashrc = pi.path('fake-bashrc')
    open(bashrc, 'w').write('old')
    monkeypatch.setattr(updater, 'SYSTEM_FILES', {'v7-beta/home/pi/.bashrc': bashrc})
    summary = updater.update()
    assert summary['system_files'] == [bashrc]
    assert any('install.sh' in line for line in updater.describe(summary))


def test_failed_folder_swap_rolls_back(pi, github, monkeypatch):
    updater.update()
    before = installed()
    github.release('eee4444')
    real_rename = os.rename

    def failing_rename(a, b):
        if b == updater.APP_DIR and a.endswith('.new'):
            raise OSError(28, 'No space left on device')
        return real_rename(a, b)
    monkeypatch.setattr(updater.os, 'rename', failing_rename)
    with pytest.raises(OSError):
        updater.update()
    assert installed() == before and os.path.isfile(os.path.join(updater.APP_DIR, 'webservice.py'))


def test_failed_player_write_is_fixed_by_the_next_update(pi, github, monkeypatch):
    updater.update()
    before_player = player_text()
    with github.player.open('a') as f:
        f.write('# disk full test\n')
    github.release('aab0002')
    real_write = system.write_file

    def full_disk(path, content):
        if path == system.SCRIPT_FILE:
            raise OSError(28, 'No space left on device')
        return real_write(path, content)
    monkeypatch.setattr(system, 'write_file', full_disk)
    with pytest.raises(OSError):
        updater.update()
    assert player_text() == before_player and installed()['commit'] == github.latest['commit']
    monkeypatch.setattr(system, 'write_file', real_write)
    assert updater.update()['player'] == 'updated' and '# disk full test' in player_text()


def test_only_one_install_at_a_time(pi, github):
    updater._install_lock.acquire()
    try:
        with pytest.raises(updater.UpdateError, match='already running'):
            updater.update()
    finally:
        updater._install_lock.release()


def test_script_saved_during_an_update_is_kept(pi, github):
    updater.update()
    with github.player.open('a') as f:
        f.write('# concurrent test\n')
    github.release('aab0003')
    result = {}
    system.player_lock.acquire()
    try:
        thread = threading.Thread(target=lambda: result.setdefault('summary', updater.update()))
        thread.start()
        time.sleep(1.0)
        assert thread.is_alive()          # waiting for the player lock
        system.write_file(system.SCRIPT_FILE, '# my own player\n')
    finally:
        system.player_lock.release()
    thread.join(10)
    assert result['summary']['player'] == 'kept' and player_text() == '# my own player\n'


def test_installed_version_read_under_the_lock(pi, github):
    """Another process installing at the same time: its player must count as official."""
    updater.update()
    lock_file = os.path.join(system.LOCK_DIR, 'mp4m' + str(pi.boot).replace('/', '-') + '.lock')
    other = subprocess.Popen([sys.executable, '-c', textwrap.dedent(f"""
        import fcntl, hashlib, json, os, time
        fd = os.open({lock_file!r}, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        print('locked', flush=True)
        time.sleep(1.0)
        manifest_path = {os.path.join(updater.APP_DIR, updater.MANIFEST_NAME)!r}
        m = json.load(open(manifest_path))
        player = '# player from the other process\\n'
        open({system.SCRIPT_FILE!r}, 'w').write(player)
        h = hashlib.sha256(player.encode()).hexdigest()
        m['player_sha256'] = h
        m['official_player_hashes'] = [h] + m.get('official_player_hashes', [])
        json.dump(m, open(manifest_path, 'w'))
    """)], stdout=subprocess.PIPE, universal_newlines=True)
    assert other.stdout.readline().strip() == 'locked'
    github.release('race002')
    summary = updater.update()
    other.wait()
    assert summary['player'] == 'updated'


# ----- Downloads ----- #
def test_unsafe_archive_entries_are_skipped(tmp_path, github):
    archive = make_archive(github.source, extra={'../evil.txt': b'x', '/abs.txt': b'x'})
    root = updater.extract(archive, str(tmp_path / 'x'))
    assert os.path.basename(root) == 'mp4museum-abc1234'
    assert not (tmp_path / 'evil.txt').exists()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        link = tarfile.TarInfo('top/link')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        tar.addfile(link)
        info = tarfile.TarInfo('top/file')
        info.size = 1
        tar.addfile(info, io.BytesIO(b'x'))
    root = updater.extract(buf.getvalue(), str(tmp_path / 'y'))
    assert not os.path.lexists(os.path.join(root, 'link')) and os.path.exists(os.path.join(root, 'file'))


@pytest.mark.parametrize('code, remaining, message', [
    (404, None, "doesn't know"),
    (422, None, "doesn't know"),
    (403, '0', 'limiting'),
    (403, '42', r'error \(403\)'),
    (500, None, r'error \(500\)'),
])
def test_github_error_messages(monkeypatch, code, remaining, message):
    def fake_urlopen(request, timeout=None):
        headers = email.message.Message()
        if remaining is not None:
            headers['X-RateLimit-Remaining'] = remaining
        raise urllib.error.HTTPError(request.full_url, code, 'x', headers, None)
    monkeypatch.setattr(updater.urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(updater.UpdateError, match=message):
        updater._open('https://api.github.com/x')


def test_offline_message(monkeypatch):
    def no_network(request, timeout=None):
        raise urllib.error.URLError('Name or service not known')
    monkeypatch.setattr(updater.urllib.request, 'urlopen', no_network)
    with pytest.raises(updater.UpdateError, match='internet connection'):
        updater._open('https://api.github.com/x')


def test_update_settings_file(pi):
    open(updater.UPDATE_CONFIG_FILE, 'w').write('repo = someone/fork\nbranch=test\nnonsense\n')
    assert updater.read_config() == {'repo': 'someone/fork', 'branch': 'test'}


# ----- Web interface ----- #
def test_web_check_and_install(pi, client, github):
    updater.update()
    webservice.RUNNING_VERSION = installed()
    assert b'up to date' in client.post('/check_update', follow_redirects=True).data

    github.release('bbb1111', '2026-10-04T00:00:00Z', 'Even newer')
    github.api_calls = 0
    r = client.post('/check_update', follow_redirects=True)
    assert b'An update is available: bbb1111' in r.data and b'Install Update' in r.data and b'Even newer' in r.data
    r = client.post('/install_update')
    assert b'Update installed' in r.data and b'Installed version bbb1111' in r.data
    assert installed()['commit'] == github.latest['commit']
    assert github.api_calls == 1                     # the install reuses what the check found
    assert not any(cmd[0] == 'systemd-run' for cmd in pi.commands)   # not run by systemd here
    assert b'Check for updates first' in client.post('/install_update', follow_redirects=True).data


def test_web_install_restarts_the_service(pi, client, github, monkeypatch):
    monkeypatch.setenv('INVOCATION_ID', 'x')
    client.post('/check_update')
    r = client.post('/install_update')
    assert ['systemd-run', '--on-active=2', 'systemctl', 'restart', 'mp4m-webservice'] in pi.commands
    assert b'waitForNewVersion' in r.data
    # one page: it says the web interface is restarting, then offers the reboot itself (not on
    # another page, which asked again), and reboots from here; also if the new version doesn't
    # answer within a minute
    page = r.data.decode()
    assert 'Restarting the web interface with the new version' in page and 'confirm_reboot' not in page
    assert 'id="rebootButtons" class="button-row" hidden' in page and "fetch('/reboot', {method: 'POST'" in page
    # a minute by the clock (a timer of its own), and each check gives up after 5 s
    assert '}, 60000);' in page and page.count('AbortSignal.timeout(5000)') == 2 and 'Later' in page
    # logged out: says so, instead of waiting for a reboot that isn't coming
    assert 'response.status === 401' in page and 'AbortSignal.timeout(5000)' in page

    # if the restart can't be scheduled, the page doesn't pretend it is restarting: the reboot
    # is offered straight away
    monkeypatch.setattr(system, 'run_command',
                        lambda cmd: (False, 'dbus error') if cmd[0] == 'systemd-run' else pi.run_command(cmd))
    github.release('ccc2222')
    client.post('/check_update')
    r = client.post('/install_update')
    page = r.data.decode()
    assert 'Update installed' in page and 'waitForNewVersion' not in page and 'Restarting' not in page
    assert 'id="rebootButtons" class="button-row" >' in page and 'confirm_reboot' not in page
    client.post('/reboot')
    assert ['reboot'] in pi.commands


def test_web_offline(client, monkeypatch):
    def offline(repo, ref):
        raise updater.UpdateError("Couldn't reach GitHub")
    monkeypatch.setattr(updater, 'latest_commit', offline)
    assert b'reach GitHub' in client.post('/check_update', follow_redirects=True).data


def test_web_partial_failure_not_called_nothing_changed(client, github, monkeypatch):
    real_rename = os.rename
    monkeypatch.setattr(updater.os, 'rename',
                        lambda a, b: (_ for _ in ()).throw(OSError(28, 'full')) if a.endswith('.new') else real_rename(a, b))
    client.post('/check_update')
    r = client.post('/install_update', follow_redirects=True)
    assert b'stopped part way' in r.data and b'nothing was changed' not in r.data


def test_web_points_out_branch_switch_and_older_versions(client, github):
    updater.update()
    webservice.RUNNING_VERSION = dict(installed(), branch='dev')
    github.release('fff5555')
    assert b'switches from branch dev to master' in client.post('/check_update', follow_redirects=True).data
    webservice.RUNNING_VERSION = dict(installed(), date='2027-01-01T00:00:00Z')
    assert b'older than the installed version' in client.post('/check_update', follow_redirects=True).data


def test_version_endpoint(pi, client):
    webservice.RUNNING_VERSION = {'commit': 'abc'}
    assert client.get('/version').get_json() == {'commit': 'abc'}
    assert webservice.app.test_client().get('/version').status_code == 302


# ----- Installed with install.sh ("local copy") ----- #
def test_local_copy_identical_to_latest_is_recorded(pi, client, github):
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    webservice.RUNNING_VERSION = installed()
    r = client.post('/check_update', follow_redirects=True)
    assert b'up to date' in r.data and installed()['commit'] == github.latest['commit']
    assert github.latest['commit'][:7].encode() in client.get('/').data


def test_local_copy_with_outdated_files_outside_boot_is_offered(pi, client, github, monkeypatch):
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    webservice.RUNNING_VERSION = installed()
    # e.g. the boot video changed in the latest commit, but /boot is identical
    boot_video = pi.path('installed-boot-video.mp4')
    open(boot_video, 'w').write('older boot video')
    monkeypatch.setattr(updater, 'SYSTEM_FILES', {'v7-beta/home/pi/mp4museum-boot.mp4': boot_video})
    assert b'An update is available' in client.post('/check_update', follow_redirects=True).data
    assert installed()['commit'] == 'local'
    r = client.post('/install_update')
    assert b'install.sh' in r.data and boot_video.encode() in r.data


def test_local_copy_that_differs_is_offered(pi, client, github):
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    webservice.RUNNING_VERSION = installed()
    with open(os.path.join(updater.APP_DIR, 'static', 'style.css'), 'a') as f:
        f.write('/* changed here */\n')
    assert b'An update is available' in client.post('/check_update', follow_redirects=True).data
    assert installed()['commit'] == 'local'


def test_local_copy_comparison_failing_offers_the_update(pi, client, github, monkeypatch):
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    webservice.RUNNING_VERSION = installed()

    def broken_mount(mount_point):
        raise RuntimeError('mount failed')
    monkeypatch.setattr(system, 'writable', broken_mount)
    r = client.post('/check_update', follow_redirects=True)
    assert r.status_code == 200 and b'An update is available' in r.data


# ----- mp4m-update command ----- #
@pytest.fixture
def cli(monkeypatch, capsys, pi):
    """Run mp4m-update with arguments and answers to its questions; returns (exit code, output)."""
    monkeypatch.setattr(updater.subprocess, 'run', lambda cmd, check=False: pi.commands.append(cmd))
    monkeypatch.setattr(updater.os, 'geteuid', lambda: 0)

    def run(argv, answers=()):
        answers = list(answers)
        monkeypatch.setattr('builtins.input', lambda prompt='': (print(prompt), answers.pop(0) if answers else '')[1])
        monkeypatch.setattr(sys, 'argv', ['mp4m-update'] + argv)
        code = 0
        try:
            updater.main()
        except SystemExit as e:
            code = e.code
        return code, capsys.readouterr().out
    return run


def test_cli_check_installs_nothing(cli, github):
    code, out = cli(['--check'])
    assert 'An update is available' in out and installed() == {}


def test_cli_install_and_reboot_question(pi, cli, github):
    code, out = cli([], answers=['y', 'n'])
    assert installed()['commit'] == github.latest['commit']
    assert ['systemctl', 'restart', 'mp4m-webservice'] in pi.commands and ['reboot'] not in pi.commands
    assert 'Already up to date' in cli([])[1]


def test_cli_from_dir_for_install_sh(cli, github):
    code, out = cli(['--from-dir', str(github.source)])
    assert installed()['commit'] == 'local' and 'Installed from a local copy' in out


def test_cli_check_with_local_copy_writes_nothing(pi, cli, github):
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    pi.commands.clear()
    code, out = cli(['--check'])
    assert 'the local copy is this version' in out and installed()['commit'] == 'local' and pi.mounts() == []


def test_cli_differing_local_copy_compared_once_until_it_changes(pi, cli, github):
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    with open(os.path.join(updater.APP_DIR, 'static', 'style.css'), 'a') as f:
        f.write('/* changed here */\n')
    assert 'An update is available' in cli(['--check'])[1]
    downloads = len(github.downloads)
    cli(['--check'])
    assert len(github.downloads) == downloads
    # install.sh run meanwhile: compared again
    updater.install_from(str(github.source), LOCAL, check_system_files=False)
    code, out = cli([])
    assert len(github.downloads) == downloads + 1 and 'Already up to date' in out
    assert installed()['commit'] == github.latest['commit']


def test_cli_unexpected_error_is_explained(cli, github, monkeypatch):
    def failing_update(*args, **kwargs):
        raise RuntimeError('mount failed')
    monkeypatch.setattr(updater, 'update', failing_update)
    code, out = cli(['--yes'])
    assert 'stopped part way: mount failed' in str(code)


def test_page_checks_for_updates_by_itself(pi, client, github):
    updater.update()
    webservice.RUNNING_VERSION = installed()
    fetch = {'X-Requested-With': 'fetch'}
    assert client.post('/check_update', data={'auto': '1'}, headers=fetch).get_json() == {'update': None}
    github.release('bbb1111', '2026-10-04T00:00:00Z', 'Even newer')
    github.api_calls = 0
    # asked again within a few hours: GitHub isn't
    assert client.post('/check_update', data={'auto': '1'}, headers=fetch).get_json() == {'update': None}
    assert github.api_calls == 0
    webservice._auto_check['time'] -= webservice.AUTO_CHECK_INTERVAL
    r = client.post('/check_update', data={'auto': '1'}, headers=fetch).get_json()
    assert r == {'update': {'commit': 'bbb1111', 'date': '2026-10-04', 'message': 'Even newer'}}
    assert github.api_calls == 1
    # the bar's Install button works with what the check found
    assert b'Update available' in client.get('/').data
    r = client.post('/install_update')
    assert b'Update installed' in r.data and github.api_calls == 1


def test_page_check_is_quiet_offline(client, monkeypatch):
    calls = []
    def offline(repo, ref):
        calls.append(repo)
        raise updater.UpdateError("Couldn't reach GitHub")
    monkeypatch.setattr(updater, 'latest_commit', offline)
    fetch = {'X-Requested-With': 'fetch'}
    for _ in range(3):
        assert client.post('/check_update', data={'auto': '1'}, headers=fetch).get_json() == {'update': None}
    assert len(calls) == 1          # not on every page
    webservice._auto_check['time'] -= webservice.AUTO_CHECK_RETRY
    client.post('/check_update', data={'auto': '1'}, headers=fetch)
    assert len(calls) == 2          # but again after an hour
    assert b'reach GitHub' not in client.get('/').data


def test_page_checks_one_at_a_time(client, monkeypatch):
    # pages opened together don't all ask GitHub
    calls = []
    def slow(repo, ref):
        calls.append(repo)
        time.sleep(0.3)
        return {'commit': 'bbb1111', 'date': '2026-10-04T00:00:00Z', 'message': 'New'}
    monkeypatch.setattr(updater, 'latest_commit', slow)
    answers = []
    def page():
        c = webservice.app.test_client()
        c.post('/login', data={'password': 'mp4museum'})
        answers.append(c.post('/check_update', data={'auto': '1'}, headers={'X-Requested-With': 'fetch'}).get_json())
    threads = [threading.Thread(target=page) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # and they all get its answer
    assert len(calls) == 1 and [a['update'] and a['update']['commit'] for a in answers] == ['bbb1111'] * 4
