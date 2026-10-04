"""Shared test fixtures: a simulated Pi for the web interface and the updater.

Every test gets its own temporary /boot and media folders, with mount, reboot and
hostname changes stubbed out, so nothing touches the computer running the tests.
Updater tests get a fake GitHub that serves a temporary copy of this repository.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import io
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / 'v7-beta' / 'boot' / 'mp4m-web'
sys.path.insert(0, str(APP))

import clone  # noqa: E402
import network  # noqa: E402
import system  # noqa: E402
import updater  # noqa: E402
import webservice  # noqa: E402


class FakePi:
    """The simulated Pi: what the code did, and switches the tests can flip."""

    def __init__(self, root):
        self.root = root
        self.boot = root / 'boot'
        self.media = root / 'media' / 'internal'
        self.commands = []
        self.hostnames = []
        self.read_only = True
        # network changes waiting to run: (seconds, function); run them with pi.run_later()
        self.later = []
        # the card the Pi runs from, and a reader without a card
        self.disks = [{'name': 'mmcblk0', 'size': '31914983424', 'type': 'disk', 'tran': None, 'rm': False, 'model': None},
                      {'name': 'sdb', 'size': '0', 'type': 'disk', 'tran': 'usb', 'rm': True, 'model': 'Reader'}]

    def run_command(self, cmd, timeout=None):
        self.commands.append(list(cmd))
        if cmd[0] == 'aplay':
            return True, 'card 0: Headphones\ncard 1: vc4hdmi'
        return True, ''

    def clone_run(self, cmd, input=None):
        """clone.run: lsblk lists self.disks (cards in USB readers); the rest is recorded."""
        self.commands.append(list(cmd))
        if cmd[0] == 'lsblk':
            return json.dumps({'blockdevices': self.disks})
        return ''

    def schedule(self, seconds, function):
        entry = [seconds, function]
        self.later.append(entry)

        class Timer:
            def cancel(timer):
                if entry in self.later:
                    self.later.remove(entry)
        return Timer()

    def run_later(self, seconds=None):
        """Run what network.change() left for later: everything due within seconds (all if None)."""
        for entry in sorted(self.later, key=lambda e: e[0]):
            if (seconds is None or entry[0] <= seconds) and entry in self.later:
                self.later.remove(entry)
                entry[1]()

    def mounts(self):
        return [cmd for cmd in self.commands if cmd[0] == 'mount']

    def path(self, *parts):
        return os.path.join(str(self.boot), *parts)


@pytest.fixture
def pi(tmp_path, monkeypatch):
    p = FakePi(tmp_path)
    p.boot.mkdir()
    p.media.mkdir(parents=True)
    (tmp_path / 'locks').mkdir()
    shutil.copy(REPO / 'v7-beta' / 'boot' / 'config.txt', p.boot)
    shutil.copy(REPO / 'v7-beta' / 'boot' / 'mp4museum.py', p.boot)

    # a Pi 3, the player this is mostly used on (it can't show large images)
    (tmp_path / 'model').write_text('Raspberry Pi 3 Model B Rev 1.2\0')
    (tmp_path / 'meminfo').write_text('MemTotal:         882624 kB\nMemFree:          500000 kB\n')
    (tmp_path / 'cpuinfo').write_text('Hardware\t: BCM2835\nRevision\t: a02082\n')   # Pi 3 B, 1 GB
    for name, value in {
        'MODEL_FILE': str(tmp_path / 'model'),
        'MEMINFO_FILE': str(tmp_path / 'meminfo'),
        'CPUINFO_FILE': str(tmp_path / 'cpuinfo'),
        'MEDIA_PATH': str(p.media),
        'BOOT_PATH': str(p.boot),
        'ALSA_FILE': p.path('alsa.txt'),
        'CONFIG_FILE': p.path('config.txt'),
        'SCRIPT_FILE': p.path('mp4museum.py'),
        'PASSWORD_FILE': p.path('mp4m-password.txt'),
        'HOSTNAME_FILE': p.path('hostname.txt'),
        'PLAYER_SETTINGS_FILE': p.path('mp4m-player.txt'),
        'PLAYER_STATUS_FILE': str(tmp_path / 'mp4museum-status.json'),
        'PLAY_REQUEST_FILE': str(tmp_path / 'mp4museum-play.json'),
        'PLAYER_LOG_FILE': str(tmp_path / 'mp4museum.log'),
        'PLAYER_SKIPPED_FILE': str(tmp_path / 'mp4museum-skipped.json'),
        'DISABLED_FILE': p.path('mp4m-disabled.txt'),
        'LOCK_DIR': str(tmp_path / 'locks'),
    }.items():
        monkeypatch.setattr(system, name, value)
    # a path added to system.py and not here would be the real one: tests must not touch it
    real = [name for name, value in vars(system).items()
            if isinstance(value, str) and value.startswith(('/boot', '/media', '/run'))]
    assert not real, 'add these to the pi fixture: %s' % real
    monkeypatch.setattr(system, 'run_command', p.run_command)
    monkeypatch.setattr(system, 'is_read_only', lambda mount_point: p.read_only)
    monkeypatch.setattr(system, 'media_available', lambda: True)
    monkeypatch.setattr(system, 'apply_hostname', lambda name: (p.hostnames.append(name), (True, ''))[1])
    monkeypatch.setattr(system, '_mount_states', {})

    (tmp_path / 'usbmount.conf').write_text('ENABLED=1\nMOUNTPOINTS="/media/usb0"\n')
    # started from its SD card, with the overlay on
    (tmp_path / 'mounts').write_text(f'overlay / overlay rw 0 0\n/dev/mmcblk0p2 /lower ext4 ro 0 0\n'
                                     f'/dev/mmcblk0p1 {p.boot} vfat ro 0 0\n/dev/mmcblk0p3 {p.media} exfat ro 0 0\n')
    (tmp_path / 'cmdline').write_text('console=tty1 root=PARTUUID=18512e38-02 rootfstype=ext4 boot=overlay quiet\n')
    for name, value in {
        'USBMOUNT_CONF': str(tmp_path / 'usbmount.conf'),
        'PROC_MOUNTS': str(tmp_path / 'mounts'),
        'PROC_CMDLINE': str(tmp_path / 'cmdline'),
        'SYS_BLOCK': str(tmp_path / 'sys-block'),
        'MARKER': str(tmp_path / 'mp4m-clone.json'),
        'TEMP_DIR': str(tmp_path),
        'partition': lambda disk, number: str(tmp_path / 'dev' / ('%s-%d' % (os.path.basename(disk), number))),
        'run': p.clone_run,
        'exfat_tool': lambda: 'mkfs.exfat',
        'write_disk_id': lambda device, disk_id: p.commands.append(['write_disk_id', device, disk_id]),
        'exfat_usage': lambda tool: 'Usage: mkexfatfs [-i volume-id] [-n label] ...',     # exfat-utils
        'state': dict(clone.state, running=False, done=False, error=None),
    }.items():
        monkeypatch.setattr(clone, name, value)
    real = [name for name, value in vars(clone).items()
            if isinstance(value, str) and value.startswith(('/etc', '/proc', '/sys', '/run', '/tmp', '/boot', '/media'))
            and not value.startswith(str(tmp_path))]
    assert not real, 'add these to the pi fixture: %s' % real

    # network settings: /etc as on the image, no Wi-Fi interfaces unless a test adds them
    etc = tmp_path / 'etc'
    (etc / 'wpa_supplicant').mkdir(parents=True)
    (etc / 'dhcpcd.conf').write_text('hostname\nclientid\npersistent\nslaac private\n')
    (etc / 'wpa_supplicant' / 'wpa_supplicant.conf').write_text(network.DEFAULT_WPA_CONF)
    (etc / 'resolv.conf').write_text('nameserver 192.168.1.1\n')
    (tmp_path / 'iso3166.tab').write_text('# ISO 3166 alpha-2 country codes\nDE\tGermany\nGB\tBritain (UK)\nUS\tUnited States\n')
    (tmp_path / 'net').mkdir()
    monkeypatch.setattr(system, 'NET_PATH', str(tmp_path / 'net'))
    for name, value in {
        'SETTINGS_FILE': p.path('mp4m-network.json'),
        'DHCPCD_CONF': str(etc / 'dhcpcd.conf'),
        'WPA_CONF_DIR': str(etc / 'wpa_supplicant'),
        'RESOLV_CONF': str(etc / 'resolv.conf'),
        'ISO3166_FILE': str(tmp_path / 'iso3166.tab'),
        'RFKILL_PATH': str(tmp_path / 'rfkill'),
        '_later': p.schedule,
        '_pending': {},
        '_in_use': {'settings': None},
        '_next_start': {},
    }.items():
        monkeypatch.setattr(network, name, value)
    real = [name for name, value in vars(network).items()
            if isinstance(value, str) and value.startswith(('/etc', '/usr', '/proc', '/sys', '/run', '/tmp', '/boot', '/media'))
            and not value.startswith(str(tmp_path))]
    assert not real, 'add these to the pi fixture: %s' % real

    monkeypatch.setattr(updater, 'APP_DIR', p.path('mp4m-web'))
    monkeypatch.setattr(updater, 'UPDATE_CONFIG_FILE', p.path('mp4m-update.txt'))
    monkeypatch.setattr(updater, 'SYSTEM_FILES', {})
    monkeypatch.setattr(updater, '_differs_from_local_copy', {})

    monkeypatch.setattr(webservice, 'RUNNING_VERSION', {})
    monkeypatch.setattr(webservice, '_reboot_lock', {'fd': None})
    monkeypatch.setattr(webservice, '_auto_check', {'time': None, 'ok': False, 'latest': None})
    monkeypatch.setattr(webservice.app, 'secret_key', 'test')
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    webservice.app.config['TESTING'] = True
    return p


@pytest.fixture
def client(pi):
    """A browser that is logged in with the default password."""
    c = webservice.app.test_client()
    assert c.post('/login', data={'password': 'mp4museum'}).status_code == 302
    return c


def make_archive(source_root, top='mp4museum-abc1234', extra=None):
    """A GitHub-style .tar.gz of a copy of the repository."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        tar.add(str(Path(source_root) / 'v7-beta'), arcname=top + '/v7-beta',
                filter=lambda info: None if '__pycache__' in info.name else info)
        for name, data in (extra or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeGitHub:
    """Stands in for GitHub: serves `source` (a copy of this repository) as the latest commit."""

    def __init__(self, source):
        self.source = source
        self.api_calls = 0
        self.downloads = []
        self.release('abc1234')

    def release(self, commit, date='2026-10-03T02:00:00Z', message='New version'):
        self.latest = {'commit': commit + '0' * (40 - len(commit)), 'date': date, 'message': message}
        return self.latest

    def latest_commit(self, repo, ref):
        self.api_calls += 1
        return dict(self.latest)

    def download(self, repo, commit):
        self.downloads.append(commit)
        return make_archive(self.source)

    @property
    def player(self):
        return Path(self.source) / 'v7-beta' / 'boot' / 'mp4museum.py'


@pytest.fixture
def github(pi, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    shutil.copytree(str(REPO / 'v7-beta'), str(source / 'v7-beta'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    fake = FakeGitHub(source)
    monkeypatch.setattr(updater, 'latest_commit', fake.latest_commit)
    monkeypatch.setattr(updater, 'download', fake.download)
    return fake
