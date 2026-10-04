"""Copying this player to an SD card in a USB reader (clone.py and the System tab card).

The disks are simulated: partitions are files and folders in a temporary directory, and
mount, sfdisk and mkfs are recorded instead of run.

Part of https://github.com/lotech/mp4museum (added 2026). Licensed under the GNU GPL v3, see LICENSE.
"""
import json
import os
import shutil
import sys
import threading
import time

import pytest

import clone
import system
import webservice

SOURCE_ID = '18512e38'
CARD = {'device': '/dev/sda', 'size': 32 * 1024 ** 3, 'model': 'SD_Transcend'}
READER = {'name': 'sda', 'size': str(CARD['size']), 'type': 'disk', 'tran': 'usb', 'rm': True,
          'model': 'SD_Transcend   '}


def table(disk_id):
    return json.dumps({'partitiontable': {'label': 'dos', 'id': '0x' + disk_id, 'partitions': [
        {'node': '/dev/mmcblk0p1', 'start': 8192, 'size': 524288, 'type': 'c'},
        {'node': '/dev/mmcblk0p3', 'start': 532480, 'size': 18548736, 'type': '7'},
        {'node': '/dev/mmcblk0p2', 'start': 19081216, 'size': 41504768, 'type': '83'}]}})


class Disks:
    """The source card and the card in the reader: each partition is a file (copied block by
    block) and a folder (what mounting it shows)."""

    def __init__(self, pi, tmp_path, monkeypatch, card_id=SOURCE_ID):
        self.pi = pi
        self.dev = tmp_path / 'dev'
        self.dev.mkdir()
        self.card_id = card_id
        self.sfdisk_input = None
        self.automount_while_partitioning = None
        self.copies = []
        self.fail = None
        boot = os.urandom(3 * 1024 ** 2 + 5)
        (self.dev / 'mmcblk0p1').write_bytes(boot)
        (self.dev / 'sda1').write_bytes(b'\0' * len(boot))
        root = self.folder('mmcblk0p2')
        (root / 'etc').mkdir()
        (root / 'etc' / 'fstab').write_text(
            'proc /proc proc defaults 0 0\n'
            'PARTUUID=18512e38-01  /boot  vfat  defaults,ro  0  2\n'
            'PARTUUID=18512e38-02  /  ext4  defaults,noatime  0  1\n'
            'PARTUUID=18512e38-03  /media/internal  exfat  ro  0  0\n')
        (root / 'var' / 'lib' / 'dhcpcd5').mkdir(parents=True)
        (root / 'var' / 'lib' / 'dhcpcd5' / 'eth0.lease').write_text('lease')
        (root / 'var' / 'lib' / 'dhcpcd5' / 'duid').write_text('duid')
        (root / 'home' / 'pi').mkdir(parents=True)
        (root / 'home' / 'pi' / '.bashrc').write_text('python3 /boot/mp4museum.py\n')
        # what the card's boot partition shows once the source's has been copied to it
        card_boot = self.folder('sda1')
        (card_boot / 'cmdline.txt').write_text(
            'console=tty1 root=PARTUUID=18512e38-02 rootfstype=ext4 boot=overlay quiet\n')
        (card_boot / 'hostname.txt').write_text('gallery-1\n')
        self.folder('sda2')
        self.folder('sda3')
        (pi.media / 'video.mp4').write_bytes(b'video')
        (pi.media / '.upload-1234').write_bytes(b'half')
        pi.disks.append(READER)
        with open(clone.PROC_MOUNTS, 'a') as f:
            f.write('/dev/sda1 /media/usb0 vfat ro 0 0\n/dev/sda3 /media/usb\\0401 exfat ro 0 0\n')
        monkeypatch.setattr(clone, 'run', self.run)
        monkeypatch.setattr(clone, 'partition', self.partition)
        monkeypatch.setattr(clone, '_copy_tree', self.copy_tree)
        monkeypatch.setattr(clone, '_player_leaves', lambda mount_points: None)

    def folder(self, name):
        path = self.dev / (name + '.d')
        path.mkdir()
        return path

    def partition(self, disk, number):
        name = os.path.basename(disk)
        return str(self.dev / ('%s%s%d' % (name, 'p' if name[-1].isdigit() else '', number)))

    def run(self, cmd, input=None):
        self.pi.commands.append(list(cmd))
        if self.fail and cmd[0] == self.fail and '-J' not in cmd:
            raise clone.CloneError(cmd[0] + ': failed')
        if cmd[0] == 'sfdisk' and cmd[1] == '-J':
            return table(SOURCE_ID if cmd[2] == clone.SOURCE_DISK else self.card_id)
        if cmd[0] == 'sfdisk':
            self.sfdisk_input = input
            with open(clone.USBMOUNT_CONF) as f:
                self.automount_while_partitioning = f.read()
            with open(clone.MARKER) as f:
                self.marker_while_partitioning = f.read()
        if cmd[0] == 'mount':
            device, path = cmd[-2:]
            os.rmdir(path)
            os.symlink(device + '.d', path)
        if cmd[0] == 'umount' and os.path.islink(cmd[-1]):
            os.unlink(cmd[-1])
            os.mkdir(cmd[-1])
        return self.pi.clone_run(cmd, input) if cmd[0] == 'lsblk' else ''

    def copy_tree(self, cmd, target, total, progress):
        self.copies.append(cmd)
        excluded = [cmd[i + 1].rstrip('*') for i, arg in enumerate(cmd) if arg == '--exclude']
        source = cmd[-2]
        for folder, dirs, files in os.walk(source):
            relative = os.path.relpath(folder, source)
            os.makedirs(os.path.join(cmd[-1], relative), exist_ok=True)
            for name in files:
                if not any(name.startswith(prefix) for prefix in excluded):
                    shutil.copyfile(os.path.join(folder, name), os.path.join(cmd[-1], relative, name))

    def mounted(self):
        return [p for p in self.dev.parent.rglob('*') if p.is_symlink()]


@pytest.fixture
def disks(pi, tmp_path, monkeypatch):
    return Disks(pi, tmp_path, monkeypatch)


# ----- What there is ----- #
def test_lists_cards_in_usb_readers_only(pi):
    pi.disks += [READER, dict(READER, name='sdc', tran='sata', model='Disk')]
    assert clone.list_cards() == [CARD]


def test_never_lists_the_card_the_pi_runs_from(pi):
    pi.disks = [dict(READER, name='mmcblk0')]
    assert clone.list_cards() == []


def test_never_lists_a_disk_the_system_uses(pi):
    # e.g. the Pi started with a card of the same image in the reader, and mounted its /boot from
    # it; or a disk without partitions mounted somewhere
    pi.disks += [READER, dict(READER, name='sdb', size='8000000000'), dict(READER, name='sdc')]
    with open(clone.PROC_MOUNTS, 'a') as f:
        f.write('/dev/sda1 /media/usb0 vfat ro 0 0\n/dev/sdb1 /boot vfat ro 0 0\n/dev/sdc /mnt ext4 rw 0 0\n')
    assert [card['device'] for card in clone.list_cards()] == ['/dev/sda']


def test_a_disk_without_partitions_is_unmounted_too(pi):
    with open(clone.PROC_MOUNTS, 'a') as f:
        f.write('/dev/sda /media/usb0 exfat ro 0 0\n/dev/sdab1 /media/usb1 vfat ro 0 0\n')
    clone._unmount_card('/dev/sda')
    assert [cmd for cmd in pi.commands if cmd[0] == 'umount'] == [['umount', '/media/usb0']]


def test_no_cards_when_lsblk_fails(pi, monkeypatch):
    def fail(cmd, input=None):
        raise clone.CloneError('lsblk: not found')
    monkeypatch.setattr(clone, 'run', fail)
    assert clone.list_cards() == []


def test_partition_names():
    assert clone.partition('/dev/sda', 1) == '/dev/sda1'
    assert clone.partition('/dev/mmcblk0', 2) == '/dev/mmcblk0p2'


def test_plan_puts_the_media_partition_after_a_system_partition_with_room():
    gib = 1024 ** 3
    layout = clone.plan(32 * gib, int(2.1 * gib), 5 * gib, False, 524288)
    assert layout['boot'] == (8192, 524288)
    start, size = layout['root']
    assert start == 532480 and start % clone.ALIGN == 0 and size % clone.ALIGN == 0
    assert size * 512 >= 2.1 * gib * 1.2 + gib
    assert layout['media'] == (start + size, None)


def test_plan_says_when_the_card_is_too_small():
    gib = 1024 ** 3
    assert clone.plan(8 * gib, 2 * gib, 3 * gib, False, 524288)
    with pytest.raises(clone.CloneError, match='The card is too small: it needs .*, it has 8.0 GB'):
        clone.plan(8 * gib, 2 * gib, 6 * gib, True, 524288)


# ----- Copying ----- #
def test_copies_this_player_to_the_card(pi, disks):
    progress = []
    clone.clone(CARD, with_media=True, progress=lambda **changes: progress.append(changes))

    # the partitions it had were unmounted, usbmount was off meanwhile and is back on
    assert ['umount', '/media/usb0'] in pi.commands and ['umount', '/media/usb 1'] in pi.commands
    assert 'ENABLED=0' in disks.automount_while_partitioning
    assert open(clone.USBMOUNT_CONF).read().startswith('ENABLED=1\n')
    # a new disk ID, with the same boot partition and a system partition after it
    script = disks.sfdisk_input
    new_id = script.split('label-id: 0x')[1][:8]
    assert new_id != SOURCE_ID and len(new_id) == 8
    assert 'start=8192, size=524288, type=c\nstart=532480, size=' in script
    assert script.rstrip().endswith('type=7')
    assert {'same_id_before': True} in progress
    # boot partition: copied as it is, then given the new ID and no network name
    assert (disks.dev / 'sda1').read_bytes() == (disks.dev / 'mmcblk0p1').read_bytes()
    assert 'root=PARTUUID=%s-02 ' % new_id in (disks.dev / 'sda1.d' / 'cmdline.txt').read_text()
    assert not (disks.dev / 'sda1.d' / 'hostname.txt').exists()
    # system: a new ext4 partition with the files, its fstab changed, no DHCP lease
    assert ['mkfs.ext4', '-F', '-q', '-L', 'rootfs', disks.partition('/dev/sda', 2)] in pi.commands
    card_root = disks.dev / 'sda2.d'
    fstab = (card_root / 'etc' / 'fstab').read_text()
    assert SOURCE_ID not in fstab and fstab.count('PARTUUID=%s-0' % new_id) == 3
    assert (card_root / 'home' / 'pi' / '.bashrc').exists()
    assert (card_root / 'var' / 'lib' / 'dhcpcd5' / 'duid').exists()
    assert not (card_root / 'var' / 'lib' / 'dhcpcd5' / 'eth0.lease').exists()
    assert ['mount', '-o', 'ro', disks.partition(clone.SOURCE_DISK, 2)] == pi.mounts()[1][:4]
    # media: an exFAT partition with the files, not the uploads half done
    assert ['mkfs.exfat', '-n', 'Media', disks.partition('/dev/sda', 3)] in pi.commands
    assert (disks.dev / 'sda3.d' / 'video.mp4').read_bytes() == b'video'
    assert not (disks.dev / 'sda3.d' / '.upload-1234').exists()
    # everything unmounted again, the work folder gone
    assert disks.mounted() == [] and not list(pi.root.glob('mp4m-clone-*'))
    assert pi.commands[-1] == ['sync']


def test_card_in_use_is_left_as_it_was(pi, disks):
    disks.fail = 'sfdisk'
    with pytest.raises(clone.CloneError, match="The card is in use, so it wasn't changed"):
        clone.clone(CARD, with_media=False)


def test_marker_while_making_a_card(pi, disks):
    clone.clone(CARD, with_media=False)
    assert json.loads(disks.marker_while_partitioning) == {'usbmount': 'ENABLED=1\nMOUNTPOINTS="/media/usb0"\n'}
    assert not os.path.exists(clone.MARKER)


def test_recovers_after_the_web_interface_stopped_part_way(pi, tmp_path):
    # usbmount left off, and the copy's folders mounted
    with open(clone.USBMOUNT_CONF, 'w') as f:
        f.write('ENABLED=0\n')
    with open(clone.MARKER, 'w') as f:
        json.dump({'usbmount': 'ENABLED=1\n'}, f)
    work = tmp_path / 'mp4m-clone-abc'
    (work / 'source-root').mkdir(parents=True)
    (work / 'card-root').mkdir()
    with open(clone.PROC_MOUNTS, 'a') as f:
        f.write(f'/dev/mmcblk0p2 {work}/source-root ext4 ro 0 0\n/dev/sda2 {work}/card-root ext4 rw 0 0\n')
    clone.recover()
    assert ['umount', '-l', f'{work}/card-root'] in pi.commands and ['umount', '-l', f'{work}/source-root'] in pi.commands
    assert open(clone.USBMOUNT_CONF).read() == 'ENABLED=1\n'
    assert not os.path.exists(clone.MARKER) and not work.exists()


def test_recover_does_nothing_normally(pi):
    clone.recover()
    assert pi.commands == [] and open(clone.USBMOUNT_CONF).read().startswith('ENABLED=1')


def test_copies_without_the_media_files(pi, disks):
    clone.clone(CARD, with_media=False)
    assert ['mkfs.exfat', '-n', 'Media', disks.partition('/dev/sda', 3)] in pi.commands
    assert len(disks.copies) == 1 and disks.copies[0][0:2] == ['rsync', '-aHAXx']
    assert list((disks.dev / 'sda3.d').iterdir()) == []


def test_a_card_from_elsewhere_needs_no_reboot(pi, tmp_path, monkeypatch):
    Disks(pi, tmp_path, monkeypatch, card_id='0000abcd')
    progress = []
    clone.clone(CARD, with_media=False, progress=lambda **changes: progress.append(changes))
    assert {'same_id_before': False} in progress


def test_a_failure_leaves_nothing_mounted_and_usbmount_on(pi, disks):
    disks.fail = 'mkfs.exfat'
    with pytest.raises(clone.CloneError, match='mkfs.exfat: failed'):
        clone.clone(CARD, with_media=True)
    assert disks.mounted() == [] and not list(pi.root.glob('mp4m-clone-*'))
    assert open(clone.USBMOUNT_CONF).read().startswith('ENABLED=1\n')


def test_stops_if_the_system_doesnt_name_partitions_by_disk_id(pi, disks):
    (disks.dev / 'mmcblk0p2.d' / 'etc' / 'fstab').write_text('/dev/mmcblk0p2 / ext4 defaults 0 1\n')
    with pytest.raises(clone.CloneError, match="fstab doesn't name the partitions by PARTUUID=18512e38-"):
        clone.clone(CARD, with_media=False)
    assert disks.mounted() == []


def test_copy_tree_accepts_files_that_vanished(pi, tmp_path):
    # rsync says 24 when files were deleted while it copied (uploads)
    clone._copy_tree([sys.executable, '-c', 'import sys; sys.exit(24)'], str(tmp_path), 0, None)
    with pytest.raises(clone.CloneError, match='No space left'):
        clone._copy_tree([sys.executable, '-c', 'import sys; sys.stderr.write("No space left"); sys.exit(11)'],
                         str(tmp_path), 0, None)


def test_a_card_in_use_by_the_player_is_let_go(pi, monkeypatch):
    # the player plays /media/*/*.*: it can be playing a file from the card that is about to be erased
    with open(clone.PROC_MOUNTS, 'a') as f:
        f.write('/dev/sda1 /media/usb0 exfat ro 0 0\n')
    commands = []

    def run(cmd, input=None):
        commands.append(cmd)
        if cmd == ['umount', '/media/usb0']:
            raise clone.CloneError('umount: /media/usb0: target is busy.')
        return ''
    monkeypatch.setattr(clone, 'run', run)
    playing = ['/media/usb0/film.mp4']
    signals = []
    monkeypatch.setattr(system, 'get_player_status', lambda: {'file': playing[0], 'pid': 1})
    monkeypatch.setattr(system, 'signal_player', lambda signum: (signals.append(signum), playing.__setitem__(0, '/media/internal/a.mp4')))
    monkeypatch.setattr(clone.time, 'sleep', lambda seconds: None)
    clone._unmount_card('/dev/sda')
    assert commands == [['umount', '/media/usb0'], ['umount', '-l', '/media/usb0']]
    assert len(signals) == 1


# ----- Starting it (one at a time) ----- #
@pytest.fixture
def ready(pi, monkeypatch):
    """A card in the reader; the copy itself is recorded, not run."""
    pi.disks.append(READER)
    started = []
    monkeypatch.setattr(clone, 'sizes', lambda: (2 * 1024 ** 3, 1024 ** 3))
    monkeypatch.setattr(clone, 'source_partitions', lambda: (SOURCE_ID, [(8192, 524288)]))
    monkeypatch.setattr(clone, '_clone', lambda card, with_media, busy: (started.append((card['device'], with_media)),
                                                                         os.close(busy)))
    return started


def test_start(ready):
    clone.start('/dev/sda', True)
    assert ready == [('/dev/sda', True)]
    assert clone.get_state()['running'] and clone.get_state()['card'] == CARD


@pytest.mark.parametrize('device', ['/dev/mmcblk0', '/dev/sdb', '/dev/sda; reboot', ''])
def test_start_refuses_anything_but_a_card_in_a_reader(ready, device):
    with pytest.raises(clone.CloneError, match="isn't in a USB reader"):
        clone.start(device, False)
    assert ready == [] and not clone.is_running()


def test_start_refuses_a_card_that_has_changed(ready, pi):
    # the page was loaded with a 32 GB card as /dev/sda; now /dev/sda is a 4 GB stick
    pi.disks[-1] = dict(READER, size=str(4 * 1024 ** 3), model='USB stick')
    with pytest.raises(clone.CloneError, match="/dev/sda isn't the card that was chosen any more"):
        clone.start('/dev/sda', False, CARD['size'])
    assert ready == []


def test_start_refuses_when_not_started_from_the_sd_card(ready, pi):
    # a Pi 4 started from a USB disk: /dev/mmcblk0 isn't the system
    with open(clone.PROC_MOUNTS, 'w') as f:
        f.write(f'/dev/sdb1 {pi.boot} vfat ro 0 0\n/dev/mmcblk0p1 /media/usb0 vfat ro 0 0\n')
    with pytest.raises(clone.CloneError, match="doesn't start from its SD card"):
        clone.start('/dev/sda', False)
    assert ready == []


def test_start_refuses_with_the_overlay_off(ready):
    # the system partition is then mounted read-write, and changing
    with open(clone.PROC_CMDLINE, 'w') as f:
        f.write('console=tty1 root=PARTUUID=18512e38-02 rootfstype=ext4 quiet\n')
    with pytest.raises(clone.CloneError, match='Turn the overlay file system back on'):
        clone.start('/dev/sda', False)
    assert ready == []


def test_start_refuses_without_exfat_tools(ready, monkeypatch):
    monkeypatch.setattr(clone, 'exfat_tool', lambda: None)
    with pytest.raises(clone.CloneError, match='needs exfat-utils'):
        clone.start('/dev/sda', False)
    assert ready == []


def test_start_refuses_a_second_copy(ready):
    clone.start('/dev/sda', False)
    with pytest.raises(clone.CloneError, match='being made already'):
        clone.start('/dev/sda', False)
    assert len(ready) == 1


def test_start_refuses_while_a_file_is_copied(ready, monkeypatch):
    # it may be reading from the card that would be erased
    monkeypatch.setattr(system, 'copies_running', lambda: ['/media/usb0/film.mp4'])
    with pytest.raises(clone.CloneError, match='A file is being copied to this player'):
        clone.start('/dev/sda', False)
    assert ready == [] and not clone.is_running()


def test_start_refuses_while_an_update_is_installed(ready):
    # by mp4m-update, which restarts the web interface when it's done
    installing = system.try_busy_lock()
    with pytest.raises(clone.CloneError, match='An update is being installed'):
        clone.start('/dev/sda', False)
    assert ready == [] and not clone.is_running()
    os.close(installing)


def test_updates_wait_while_a_card_is_made(pi, monkeypatch):
    pi.disks.append(READER)
    monkeypatch.setattr(clone, 'sizes', lambda: (2 * 1024 ** 3, 1024 ** 3))
    monkeypatch.setattr(clone, 'source_partitions', lambda: (SOURCE_ID, [(8192, 524288)]))
    go = threading.Event()
    monkeypatch.setattr(clone, 'clone', lambda card, with_media: go.wait(5))
    clone.start('/dev/sda', False)
    assert system.try_busy_lock() is None
    go.set()
    deadline = time.monotonic() + 5
    while clone.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    lock = system.try_busy_lock()
    assert lock is not None
    os.close(lock)


def test_start_refuses_a_card_too_small(ready, pi):
    pi.disks[-1] = dict(READER, size=str(4 * 1024 ** 3))
    with pytest.raises(clone.CloneError, match='too small'):
        clone.start('/dev/sda', True)
    assert ready == [] and not clone.is_running()


def test_state_when_done_or_failed(pi, monkeypatch):
    monkeypatch.setattr(clone, 'clone', lambda card, with_media: None)
    clone._clone(CARD, False, system.try_busy_lock())
    assert clone.get_state()['done'] and clone.get_state()['recent'] and not clone.get_state()['running']
    # the page stops saying how it went after a while
    finished = clone.state['finished']
    with monkeypatch.context() as m:
        m.setattr(clone.time, 'monotonic', lambda: finished + clone.RESULT_SHOWN)
        assert not clone.get_state()['recent']
    monkeypatch.setattr(clone, 'clone', lambda card, with_media: (_ for _ in ()).throw(clone.CloneError('sfdisk: no')))
    clone._clone(CARD, False, system.try_busy_lock())
    assert clone.get_state()['error'] == 'sfdisk: no' and not clone.get_state()['running']


# ----- Web interface ----- #
def test_system_tab_asks_for_a_card(client):
    page = client.get('/').get_data(as_text=True)
    assert 'Copy to an SD card' in page
    assert 'Plug a USB SD card reader with a card into the Pi' in page


def test_system_tab_offers_the_cards(client, pi):
    pi.disks.append(READER)
    page = client.get('/').get_data(as_text=True)
    assert '<option value="/dev/sda|34359738368" data-name="32.0 GB SD_Transcend">' in page
    # nothing chosen until the user chooses
    assert '<select name="device" id="clone_device" required>\n              <option value="">Choose a card</option>' in page
    assert 'id="cloneForm"' in page and 'Leave them out' in page


def test_system_tab_says_when_exfat_tools_are_missing(client, pi, monkeypatch):
    pi.disks.append(READER)
    monkeypatch.setattr(clone, 'exfat_tool', lambda: None)
    page = client.get('/').get_data(as_text=True)
    assert 'needs exfat-utils' in page and 'id="cloneForm"' not in page


def test_copy_from_the_web_interface(client, ready):
    response = client.post('/clone', data={'device': '/dev/sda|%d' % CARD['size'], 'media': 'without'})
    assert response.status_code == 302
    assert ready == [('/dev/sda', False)]
    assert client.get('/clone/status').get_json()['running'] is True
    assert 'Copying this player to the card' in client.get('/').get_data(as_text=True)


def test_copy_refused_is_said(client, ready):
    client.post('/clone', data={'device': '/dev/sdb', 'media': 'with'}, follow_redirects=False)
    assert "That card isn&#39;t in a USB reader any more." in client.get('/').get_data(as_text=True)


def test_no_file_copies_while_a_card_is_made(client, pi, ready):
    # the copy may read from the card being erased
    clone.start('/dev/sda', False)
    usb = pi.media.parent / 'usb0'
    usb.mkdir()
    (usb / 'film.mp4').write_bytes(b'f')
    page = client.post('/copy_to_player', data={'file': str(usb / 'film.mp4')}, follow_redirects=True).data.decode()
    assert 'A card is being made: copy files when it&#39;s done.' in page
    assert system.copies_running() == [] and os.listdir(pi.media) == []


def test_no_reboot_or_update_while_a_card_is_made(client, pi, ready):
    clone.start('/dev/sda', False)
    response = client.post('/reboot', headers={'X-Requested-With': 'fetch'})
    assert response.status_code == 409
    client.post('/reboot')
    client.post('/install_update')
    page = client.get('/').get_data(as_text=True)
    assert "A card is being made: reboot when it&#39;s done." in page
    assert "A card is being made: install the update when it&#39;s done." in page
    assert ['reboot'] not in pi.commands


def test_clone_status_needs_login(pi):
    response = webservice.app.test_client().get('/clone/status')
    assert response.status_code in (302, 401)
