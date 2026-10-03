"""Read-only partitions, safe file writes and file name checks.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import fcntl
import os

import pytest

import system


def test_nested_writes_remount_once(pi):
    with system.writable('/x'):
        with system.writable('/x'):
            pass
        assert pi.mounts() == [['mount', '-o', 'remount,rw', '/x']]
    assert pi.mounts() == [['mount', '-o', 'remount,rw', '/x'], ['mount', '-o', 'remount,ro', '/x']]


def test_partition_that_was_writable_is_left_alone(pi):
    pi.read_only = False
    with system.writable('/y'):
        pass
    assert pi.mounts() == []


def test_failed_read_only_remount_is_retried_and_remembered(pi, monkeypatch):
    failures = {'left': 3}

    def flaky(cmd):
        pi.commands.append(cmd)
        if cmd[-2:] == ['remount,ro', '/z'] and failures['left']:
            failures['left'] -= 1
            return False, 'busy'
        return True, ''
    monkeypatch.setattr(system, 'run_command', flaky)
    monkeypatch.setattr(system.time, 'sleep', lambda s: None)
    with system.writable('/z'):
        pass
    assert pi.mounts().count(['mount', '-o', 'remount,ro', '/z']) == 3

    # the failed remount left it writable; the next write still makes it read-only
    pi.read_only = False
    pi.commands.clear()
    with system.writable('/z'):
        pass
    assert pi.mounts() == [['mount', '-o', 'remount,ro', '/z']]

    pi.commands.clear()
    with system.writable('/z'):
        pass
    assert pi.mounts() == []


def test_lock_keeps_other_processes_out(pi):
    fd = system._lock_mount('/q')
    other = os.open(os.path.join(system.LOCK_DIR, 'mp4m-q.lock'), os.O_RDWR)
    try:
        with pytest.raises(OSError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        system._unlock_mount(fd)
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(other)


def test_startup_makes_partitions_read_only_again(pi, monkeypatch):
    monkeypatch.setattr(system, 'mounted_read_only_in_fstab', lambda mp: mp == system.BOOT_PATH)
    monkeypatch.setattr(system.os.path, 'ismount', lambda p: True)
    pi.read_only = False
    system.restore_read_only_mounts()
    assert pi.mounts() == [['mount', '-o', 'remount,ro', system.BOOT_PATH]]


def test_write_file_leaves_no_temp_files(pi):
    system.write_file(system.ALSA_FILE, '2')
    assert open(system.ALSA_FILE).read() == '2'
    assert [f for f in os.listdir(str(pi.boot)) if f.startswith('.')] == []


@pytest.mark.parametrize('name, valid', [
    ('clip.mp4', True),
    ('Café clip 01.mp4', True),
    ('Screen Recording 10.00.00 AM.mov', True),
    ('映' * 100 + '.mp4', True),      # 100 Japanese characters: fine for exFAT
    ('映' * 300, False),             # more than 255 UTF-16 characters
    ('.hidden.mp4', False),
    ('../boot/config.txt', False),
    ('a:b.mp4', False),
    ('a?.mp4', False),
    ('a\nb.mp4', False),
    ('', False),
])
def test_file_names(name, valid):
    assert system.is_valid_filename(name) is valid
