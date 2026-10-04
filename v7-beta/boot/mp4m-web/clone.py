"""Copy this player onto an SD card in a USB card reader (System tab), so another Pi can be
set up the same way.

The card keeps its boot partition (copied as it is), gets a system partition the size of what
the system uses plus room (the files copied from the original, read-only system partition,
not the overlay in RAM), and a media partition filling the rest of the card (empty, or with the
media files). Its disk ID is new, so it never has the same partition IDs as the card the Pi runs
from (cmdline.txt and fstab on it are changed to match), and its network name, set here, is left
for it to make from its own serial number.

Part of https://github.com/lotech/mp4museum (added 2026), a fork of MP4MUSEUM by Julius
Schmiedel. Licensed under the GNU GPL v3, see LICENSE.
"""
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

import system

# the card the Pi runs from, and its partitions (fstab: PARTUUID=<disk id>-01 /boot, -02 /,
# -03 /media/internal)
SOURCE_DISK = '/dev/mmcblk0'
USBMOUNT_CONF = '/etc/usbmount/usbmount.conf'
PROC_MOUNTS = '/proc/mounts'
PROC_CMDLINE = '/proc/cmdline'
SYS_BLOCK = '/sys/block'
TEMP_DIR = '/tmp'
# there while a card is being made, with usbmount's settings to put back if the web interface
# stops part way (it's in RAM: a reboot clears it). (mp4m-update waits through system.try_busy_lock)
MARKER = '/run/mp4m-clone.json'
WORK_PREFIX = 'mp4m-clone-'
SECTOR = 512
ALIGN = 8192                    # partitions start on 4 MB boundaries, as on the image
ROOT_ROOM = 1024 ** 3           # free space on the clone's system partition
ROOT_MIN = 3 * 1024 ** 3
MEDIA_MIN = 256 * 1024 ** 2     # media partition without content
MKFS_EXFAT = ('mkfs.exfat', 'mkexfatfs')


class CloneError(Exception):
    pass


def run(cmd, input=None):
    """Run a command; its output, or CloneError with what it said."""
    try:
        done = subprocess.run(cmd, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, check=True)
        return done.stdout
    except subprocess.CalledProcessError as e:
        raise CloneError(f"{' '.join(cmd)}: {(e.stderr or e.stdout).strip()}")
    except OSError as e:
        raise CloneError(f"{cmd[0]}: {e}")


def partition(disk, number):
    """/dev/sda, 1 -> /dev/sda1; /dev/mmcblk0, 1 -> /dev/mmcblk0p1."""
    return f"{disk}{'p' if disk[-1].isdigit() else ''}{number}"


def exfat_tool():
    return next((name for name in MKFS_EXFAT if shutil.which(name)), None)


def exfat_usage(tool):
    """What the exFAT tool says about its options."""
    try:
        return subprocess.run([tool, '--help'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _mkfs_exfat(device, label):
    """The command for an exFAT file system: the label is -n with exfat-utils (Buster's), -L
    with exfatprogs (newer systems), which both have a mkfs.exfat."""
    tool = exfat_tool()
    return [tool, '-L' if '--volume-label' in exfat_usage(tool) else '-n', label, device]


def used_bytes(path):
    stat = os.statvfs(path)
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize


# ----- What there is ----- #
def card_identity(disk):
    """What tells this card apart from another put in the same reader (lsblk's entry): its size,
    the reader, and the IDs of its partition table and file systems; and on kernels that count
    them (5.15 and later, not Buster's), which card it is since the Pi started (diskseq: new for
    every card put in). (Two blank cards of the same size, or two written from the same image,
    can't be told apart without it.)"""
    ids = [disk.get('size'), disk.get('model'), disk.get('serial'), disk.get('ptuuid')]
    ids += sorted(str(child.get('uuid')) for child in disk.get('children') or [])
    try:
        with open(os.path.join(SYS_BLOCK, str(disk.get('name')), 'diskseq'), 'r') as f:
            ids.append(f.read().strip())
    except OSError:
        pass
    return hashlib.sha1(json.dumps([str(value) for value in ids]).encode()).hexdigest()[:16]


def list_cards():
    """SD cards (or other disks) in USB readers: [{'device', 'size', 'model', 'id'}]. Never the
    card the Pi runs from."""
    try:
        data = json.loads(run(['lsblk', '-J', '-b', '-o', 'NAME,SIZE,TYPE,TRAN,RM,MODEL,SERIAL,PTUUID,UUID']))
    except (CloneError, ValueError):
        return []
    cards = []
    for disk in data.get('blockdevices', []):
        device = '/dev/' + disk.get('name', '')
        try:
            size = int(disk.get('size') or 0)
        except ValueError:
            size = 0
        # (a reader without a card has size 0)
        if (disk.get('type') == 'disk' and disk.get('tran') == 'usb' and size > 0 and device != SOURCE_DISK
                and not _in_use_by_system(device)):
            cards.append({'device': device, 'size': size, 'model': (disk.get('model') or 'USB disk').strip(),
                          'id': card_identity(disk)})
    return cards


def _mounts():
    """[(device, mount point)] from /proc/mounts."""
    try:
        with open(PROC_MOUNTS, 'r') as f:
            return [(fields[0], fields[1].replace('\\040', ' ')) for fields in (line.split() for line in f)
                    if len(fields) > 1]
    except OSError:
        return []


def _partition_of(device, disk):
    """Whether device is the disk or one of its partitions."""
    return re.match(re.escape(disk) + r'(p?\d+)?$', device) is not None


def _in_use_by_system(disk):
    """True if a partition of this disk is mounted anywhere but /media/usbN (where usbmount puts
    a stick or card plugged in): e.g. the Pi started from it."""
    return any(_partition_of(device, disk) and not re.match(r'/media/usb\d*$', mount_point)
               for device, mount_point in _mounts())


def unavailable():
    """Why a card can't be made now, or None."""
    if not exfat_tool():
        return ("Making the card's media partition needs exfat-utils: run install.sh once, with an "
                "internet connection.")
    boot = next((device for device, mount_point in _mounts() if mount_point == system.BOOT_PATH), '')
    if not _partition_of(boot, SOURCE_DISK):
        return "This player doesn't start from its SD card, so it can't be copied from here."
    try:
        with open(PROC_CMDLINE, 'r') as f:
            overlay = 'boot=overlay' in f.read().split()
    except OSError:
        overlay = False
    if not overlay:
        # (the system partition is then mounted read-write: it can't be copied while it changes)
        return "Turn the overlay file system back on and reboot first (raspi-config)."
    return None


def source_partitions():
    """The source card's disk ID (8 hex digits) and partitions [(start, size) in sectors]."""
    try:
        table = json.loads(run(['sfdisk', '-J', SOURCE_DISK]))['partitiontable']
        parts = sorted(table['partitions'], key=lambda p: p['node'])
        return '%08x' % int(table['id'], 16), [(int(p['start']), int(p['size'])) for p in parts]
    except (ValueError, KeyError, TypeError) as e:
        raise CloneError(f"Couldn't read this card's partitions: {e}")


def plan(card_size, root_used, media_used, with_media, boot_sectors):
    """Where the partitions go on the card (sectors): {'boot': (start, size), 'root': ...,
    'media': (start, None: to the end)}. CloneError if the card is too small."""
    def align(sector):
        return (sector + ALIGN - 1) // ALIGN * ALIGN
    root_bytes = max(ROOT_MIN, int(root_used * 1.2) + ROOT_ROOM)
    boot = (ALIGN, boot_sectors)
    root = (align(ALIGN + boot_sectors), align(root_bytes // SECTOR))
    media_start = root[0] + root[1]
    # with the files: room for them on exFAT, plus a little
    media_needed = int(media_used * 1.05) + 64 * 1024 ** 2 if with_media else MEDIA_MIN
    needed = media_start * SECTOR + media_needed
    if card_size < needed:
        raise CloneError(f"The card is too small: it needs {system.format_size(needed)}, "
                         f"it has {system.format_size(card_size)}.")
    return {'boot': boot, 'root': root, 'media': (media_start, None), 'needed': needed}


def sizes():
    """What a clone needs to know before it starts: the system partition's used space and the
    media files' (both in bytes)."""
    mount_point = tempfile.mkdtemp(prefix=WORK_PREFIX, dir=TEMP_DIR)
    try:
        run(['mount', '-o', 'ro', partition(SOURCE_DISK, 2), mount_point])
        try:
            root_used = used_bytes(mount_point)
        finally:
            run(['umount', mount_point])
    finally:
        os.rmdir(mount_point)
    return root_used, used_bytes(system.MEDIA_PATH)


# ----- Cloning (one at a time, in the background) ----- #
_lock = threading.Lock()
state = {'running': False, 'step': '', 'percent': None, 'done': False, 'error': None, 'card': None,
         'same_id_before': False, 'finished': None}
RESULT_SHOWN = 30 * 60      # how long the page says how the last copy went


def get_state():
    with _lock:
        current = dict(state)
    finished = current.pop('finished')
    current['recent'] = finished is not None and time.monotonic() - finished < RESULT_SHOWN
    return current


def is_running():
    with _lock:
        return state['running']


def _set(**changes):
    with _lock:
        state.update(changes)


def start(device, with_media, identity=None):
    """Start cloning onto device (one of list_cards(); identity: its id when it was chosen).
    CloneError if it can't start."""
    reason = unavailable()
    if reason:
        raise CloneError(reason)
    card = next((c for c in list_cards() if c['device'] == device), None)
    if not card:
        raise CloneError("That card isn't in a USB reader any more.")
    if identity is not None and card['id'] != identity:
        # something else has been plugged in since the page was loaded
        raise CloneError(f"{device} isn't the card that was chosen any more. Choose it again.")
    if is_running():
        raise CloneError("A card is being made already.")
    if system.copies_running():
        # it may be reading from this card, which is about to be erased
        raise CloneError("A file is being copied to this player: make the card when it's done.")
    # too small: said now, not after it has started
    root_used, media_used = sizes()
    plan(card['size'], root_used, media_used, with_media, source_partitions()[1][0][1])
    busy = system.try_busy_lock()
    if busy is None:
        raise CloneError("An update is being installed or a file copied: make the card when it's done.")
    try:
        with _lock:
            if state['running']:
                raise CloneError("A card is being made already.")
            state.update(running=True, step='Starting', percent=None, done=False, error=None, card=card,
                         same_id_before=False, finished=None)
        threading.Thread(target=_clone, args=(card, with_media, busy), daemon=True).start()
    except BaseException:
        os.close(busy)
        raise


def _clone(card, with_media, busy):
    try:
        clone(card, with_media)
        _set(running=False, done=True, step='Done', percent=100, finished=time.monotonic())
    except Exception as e:
        _set(running=False, error=str(e), step='Stopped', finished=time.monotonic())
    finally:
        # updates can be installed again
        os.close(busy)


def clone(card, with_media, progress=_set):
    device = card['device']
    progress(step='Measuring this player', percent=None)
    root_used, media_used = sizes()
    disk_id, parts = source_partitions()
    layout = plan(card['size'], root_used, media_used, with_media, parts[0][1])
    new_id = disk_id
    while new_id == disk_id:
        new_id = '%08x' % random.randrange(1, 2 ** 32)
    work = tempfile.mkdtemp(prefix=WORK_PREFIX, dir=TEMP_DIR)
    usbmount = _stop_automount()
    mounted = []

    def mount(dev, name, options=None):
        path = os.path.join(work, name)
        os.makedirs(path, exist_ok=True)
        run(['mount'] + (['-o', options] if options else []) + [dev, path])
        mounted.append(path)
        return path

    def unmount(path):
        run(['umount', path])
        mounted.remove(path)

    try:
        _unmount_card(device)
        # a card with this player's image on it had the same partition IDs as this one: while it
        # was plugged in the Pi couldn't tell its own partitions from the card's
        try:
            old_id = '%08x' % int(json.loads(run(['sfdisk', '-J', device]))['partitiontable']['id'], 16)
            progress(same_id_before=old_id == disk_id)
        except (CloneError, ValueError, KeyError):
            pass

        # the card chosen still there (not another one put in meanwhile)? Checked last thing
        # before it's erased
        current = next((c for c in list_cards() if c['device'] == device), None)
        if not current or current['id'] != card['id']:
            raise CloneError("The card was changed, so nothing was erased. Choose it again.")

        progress(step='Making the partitions', percent=None)
        # nothing may hold the card open (e.g. exFAT's helper, a moment after it's unmounted), or
        # Linux keeps using its old partitions and the new ones are written over the wrong places
        _reread_partitions(device, "The card is still in use, so it wasn't changed. Take it out, put it "
                                   "back in and try again.")
        boot, root, media = layout['boot'], layout['root'], layout['media']
        script = (f"label: dos\nlabel-id: 0x{new_id}\nunit: sectors\n\n"
                  f"start={boot[0]}, size={boot[1]}, type=c\n"
                  f"start={root[0]}, size={root[1]}, type=83\n"
                  f"start={media[0]}, type=7\n")
        try:
            run(['sfdisk', '--wipe', 'always', '--wipe-partitions', 'always', device], input=script)
        except CloneError as e:
            # e.g. a file on it still open: sfdisk leaves the card as it was
            raise CloneError(f"The card is in use, so it wasn't changed. Take it out, put it back in and "
                             f"try again. ({e})")
        _reread_partitions(device, "The Pi didn't take in the card's new partitions, so nothing was copied "
                                   "to it. Take it out, put it back in and try again.")
        run(['udevadm', 'settle'])
        _unmount_card(device)
        _check_partitions(device, layout)

        progress(step='Copying the boot partition', percent=0)
        # (settings aren't saved meanwhile, so the copy isn't half old, half new)
        lock = system._lock_mount(system.BOOT_PATH)
        try:
            _copy_device(partition(SOURCE_DISK, 1), partition(device, 1), boot[1] * SECTOR, progress)
        finally:
            system._unlock_mount(lock)

        progress(step='Copying the system', percent=0)
        run(['mkfs.ext4', '-F', '-q', '-L', 'rootfs', partition(device, 2)])
        source_root = mount(partition(SOURCE_DISK, 2), 'source-root', 'ro')
        card_root = mount(partition(device, 2), 'card-root')
        _copy_tree(['rsync', '-aHAXx', '--numeric-ids', source_root + '/', card_root + '/'],
                   card_root, root_used, progress)
        # its own partition IDs; the source Pi's DHCP lease left behind
        _new_disk_id(os.path.join(card_root, 'etc', 'fstab'), disk_id, new_id)
        for lease in _leases(card_root):
            os.remove(lease)
        unmount(card_root)
        unmount(source_root)

        card_boot = mount(partition(device, 1), 'card-boot')
        _new_disk_id(os.path.join(card_boot, 'cmdline.txt'), disk_id, new_id)
        # the network name set here: the clone makes one from its own serial number
        hostname_file = os.path.join(card_boot, os.path.basename(system.HOSTNAME_FILE))
        if os.path.exists(hostname_file):
            os.remove(hostname_file)
        unmount(card_boot)

        progress(step='Making the media partition', percent=None)
        run(_mkfs_exfat(partition(device, 3), 'Media'))
        if with_media and media_used:
            progress(step='Copying the media files', percent=0)
            card_media = mount(partition(device, 3), 'card-media')
            _copy_tree(['rsync', '-rt', '--exclude', system.UPLOAD_PREFIX + '*',
                        system.MEDIA_PATH + '/', card_media + '/'], card_media, media_used, progress)
            unmount(card_media)
        run(['sync'])
    finally:
        for path in reversed(mounted):
            try:
                run(['umount', path])
            except CloneError:
                pass
        _remove_work(work)
        _restore_automount(usbmount)


def _remove_work(work):
    """Remove the work folder's mount points, never what's in one still mounted."""
    for name in os.listdir(work):
        path = os.path.join(work, name)
        if not os.path.ismount(path):
            try:
                os.rmdir(path)
            except OSError:
                pass
    try:
        os.rmdir(work)
    except OSError:
        pass


def recover():
    """After the web interface stopped while making a card (restart, crash): usbmount back on,
    the copy's folders unmounted. Called when it starts."""
    try:
        with open(MARKER, 'r') as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    work = os.path.join(TEMP_DIR, WORK_PREFIX)
    for _, mount_point in reversed(_mounts()):
        if mount_point.startswith(work):
            try:
                run(['umount', '-l', mount_point])
            except CloneError:
                pass
    for name in os.listdir(TEMP_DIR):
        if name.startswith(WORK_PREFIX):
            _remove_work(os.path.join(TEMP_DIR, name))
    _restore_automount(saved.get('usbmount'))


def _reread_partitions(device, message, tries=10):
    """Have Linux read the card's partition table again. It only can once nothing has the card
    open, so this also shows it's free; tried for a while, then CloneError(message)."""
    for attempt in range(tries):
        try:
            run(['blockdev', '--rereadpt', device])
            return
        except CloneError:
            if attempt == tries - 1:
                raise CloneError(message)
            time.sleep(1)


def _check_partitions(device, layout):
    """Whether Linux sees the partitions just made (start and size, in sectors), so nothing is
    formatted where the old ones were. CloneError if not."""
    disk = os.path.join(SYS_BLOCK, os.path.basename(device))

    def sectors(*path):
        with open(os.path.join(disk, *path), 'r') as f:
            return int(f.read())
    try:
        end = sectors('size')
        seen = [(sectors(os.path.basename(partition(device, n)), 'start'),
                 sectors(os.path.basename(partition(device, n)), 'size')) for n in (1, 2, 3)]
    except (OSError, ValueError):
        seen = None
    expected = [layout['boot'], layout['root'], (layout['media'][0], None)]
    if (not seen or any(start != want[0] or (want[1] is not None and size != want[1])
                        for (start, size), want in zip(seen, expected))
            or seen[2][0] + seen[2][1] > end):
        raise CloneError("The Pi didn't take in the card's new partitions, so nothing was copied to it. "
                         "Take it out, put it back in and try again.")


def _new_disk_id(path, old_id, new_id):
    with open(path, 'r') as f:
        text = f.read()
    changed = re.sub(r'PARTUUID=%s-' % old_id, 'PARTUUID=%s-' % new_id, text, flags=re.IGNORECASE)
    if changed == text:
        # it wouldn't find its partitions: stop rather than make a card that doesn't start
        raise CloneError(f"{os.path.basename(path)} doesn't name the partitions by PARTUUID={old_id}-")
    with open(path, 'w') as f:
        f.write(changed)


def _leases(root):
    folder = os.path.join(root, 'var', 'lib', 'dhcpcd5')
    try:
        return [os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(('.lease', '.lease6'))]
    except OSError:
        return []


def _copy_device(source, target, length, progress):
    """Copy a partition block by block, with progress."""
    copied = 0
    with open(source, 'rb') as src, open(target, 'r+b') as dst:
        while copied < length:
            block = src.read(min(4 * 1024 ** 2, length - copied))
            if not block:
                break
            dst.write(block)
            copied += len(block)
            progress(percent=int(copied * 100 / length))
        dst.flush()
        os.fsync(dst.fileno())


def _copy_tree(cmd, target, total, progress):
    """Run a copy, with progress from how much the target holds."""
    start = used_bytes(target)
    # (what it says goes to a file: a pipe nobody reads meanwhile could fill up and stop it)
    with tempfile.TemporaryFile(mode='w+', dir=TEMP_DIR) as errors:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errors, universal_newlines=True)
        while process.poll() is None:
            time.sleep(1)
            if total:
                progress(percent=min(99, int((used_bytes(target) - start) * 100 / total)))
        if process.returncode not in (0, 24):     # 24: files vanished while copying (uploads)
            errors.seek(0)
            raise CloneError(f"{cmd[0]}: {errors.read().strip()[-500:]}")


def _unmount_card(device):
    """Unmount the card's partitions (mounted when it was plugged in)."""
    mounts = [mount_point for dev, mount_point in _mounts() if _partition_of(dev, device)]
    busy = []
    for mount_point in mounts:
        try:
            run(['umount', mount_point])
        except CloneError:
            # in use: the player plays the media files in /media/*/, so it may be playing one from
            # the card. Then it's taken out of the folder now (-l), and let go when the player
            # moves on. Anything else using it: the card isn't touched
            if not _player_on([mount_point]):
                raise CloneError("Another program is using the card, so it wasn't changed. Take it out, "
                                 "put it back in and try again.")
            run(['umount', '-l', mount_point])
            busy.append(mount_point)
    if busy:
        _player_leaves(busy)


def _player_on(mount_points):
    """Whether the player is playing a file from one of these folders."""
    status = system.get_player_status()
    playing = str((status or {}).get('file') or '')
    return any(playing.startswith(mount_point + '/') for mount_point in mount_points)


def _player_leaves(mount_points, wait=10):
    """Make the player move on if it plays a file from one of these folders, and wait until it
    has (its next file can't be from there: they're unmounted)."""
    def on_card():
        return _player_on(mount_points)
    if on_card():
        system.signal_player(signal.SIGUSR1)
    deadline = time.monotonic() + wait
    while on_card() and time.monotonic() < deadline:
        time.sleep(0.5)
    if on_card():
        # the card would be changed while the player has a file on it open
        raise CloneError("The player didn't let go of the card, so it wasn't changed. Try again.")
    time.sleep(1)


def _stop_automount():
    """Turn off usbmount while cloning, so the new partitions aren't mounted (and played); the
    old setting, to put back (also kept in MARKER). (/etc is in RAM: a reboot puts it back too.)"""
    try:
        with open(USBMOUNT_CONF, 'r') as f:
            text = f.read()
    except OSError:
        text = None
    system.write_file(MARKER, json.dumps({'usbmount': text}))
    if text is not None:
        with open(USBMOUNT_CONF, 'w') as f:
            f.write(re.sub(r'^ENABLED=.*$', 'ENABLED=0', text, flags=re.M))
    return text


def _restore_automount(text):
    if text is not None:
        with open(USBMOUNT_CONF, 'w') as f:
            f.write(text)
    try:
        os.remove(MARKER)
    except OSError:
        pass
