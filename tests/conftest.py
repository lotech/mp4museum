"""Shared test fixtures: a simulated Pi for the web interface and the updater.

Every test gets its own temporary /boot and media folders, with mount, reboot and
hostname changes stubbed out, so nothing touches the computer running the tests.
Updater tests get a fake GitHub that serves a temporary copy of this repository.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import io
import os
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / 'v7-beta' / 'boot' / 'mp4m-web'
sys.path.insert(0, str(APP))

import system  # noqa: E402
import updater  # noqa: E402
import webservice  # noqa: E402


class FakePi:
    """The simulated Pi: what the code did, and switches the tests can flip."""

    def __init__(self, root):
        self.root = root
        self.boot = root / 'boot'
        self.media = root / 'media'
        self.commands = []
        self.hostnames = []
        self.read_only = True

    def run_command(self, cmd):
        self.commands.append(list(cmd))
        if cmd[0] == 'aplay':
            return True, 'card 0: Headphones\ncard 1: vc4hdmi'
        return True, ''

    def mounts(self):
        return [cmd for cmd in self.commands if cmd[0] == 'mount']

    def path(self, *parts):
        return os.path.join(str(self.boot), *parts)


@pytest.fixture
def pi(tmp_path, monkeypatch):
    p = FakePi(tmp_path)
    p.boot.mkdir()
    p.media.mkdir()
    (tmp_path / 'locks').mkdir()
    shutil.copy(REPO / 'v7-beta' / 'boot' / 'config.txt', p.boot)
    shutil.copy(REPO / 'v7-beta' / 'boot' / 'mp4museum.py', p.boot)

    for name, value in {
        'MEDIA_PATH': str(p.media),
        'BOOT_PATH': str(p.boot),
        'ALSA_FILE': p.path('alsa.txt'),
        'CONFIG_FILE': p.path('config.txt'),
        'SCRIPT_FILE': p.path('mp4museum.py'),
        'PASSWORD_FILE': p.path('mp4m-password.txt'),
        'HOSTNAME_FILE': p.path('hostname.txt'),
        'PLAYER_SETTINGS_FILE': p.path('mp4m-player.txt'),
        'PLAYER_STATUS_FILE': str(tmp_path / 'mp4museum-status.json'),
        'LOCK_DIR': str(tmp_path / 'locks'),
    }.items():
        monkeypatch.setattr(system, name, value)
    monkeypatch.setattr(system, 'run_command', p.run_command)
    monkeypatch.setattr(system, 'is_read_only', lambda mount_point: p.read_only)
    monkeypatch.setattr(system, 'media_available', lambda: True)
    monkeypatch.setattr(system, 'apply_hostname', lambda name: (p.hostnames.append(name), (True, ''))[1])
    monkeypatch.setattr(system, '_mount_states', {})

    monkeypatch.setattr(updater, 'APP_DIR', p.path('mp4m-web'))
    monkeypatch.setattr(updater, 'UPDATE_CONFIG_FILE', p.path('mp4m-update.txt'))
    monkeypatch.setattr(updater, 'SYSTEM_FILES', {})
    monkeypatch.setattr(updater, '_differs_from_local_copy', {})

    monkeypatch.setattr(webservice, 'RUNNING_VERSION', {})
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
