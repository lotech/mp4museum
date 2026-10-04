"""Clone a player's card image (make_source.sh) onto another image with the real tools: sfdisk,
mkfs.vfat/ext4/exfat, rsync, mount (vfat and exFAT through FUSE: fusefat, exfat-fuse). Not run
by pytest: it needs root, loop devices and those tools. In a scratch folder:

    sudo bash /path/to/tests/manual/make_source.sh
    sudo python3 /path/to/tests/manual/clone_real_tools.py with|without [card size in MB]

It prints the new partition table, cmdline.txt, fstab and what's on each partition. Linux here
can't re-read loop partitions, so the two steps that check that (_reread_partitions,
_check_partitions) are left out; tests/test_clone.py covers them.

Part of https://github.com/lotech/mp4museum (added 2026). Licensed under the GNU GPL v3, see LICENSE.
"""
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'v7-beta', 'boot', 'mp4m-web'))
import clone  # noqa: E402
import system  # noqa: E402

HERE = os.getcwd()
with_media = sys.argv[1] == 'with'
target_size = int(sys.argv[2]) * 1024 ** 2 if len(sys.argv) > 2 else 600 * 1024 ** 2
if os.path.exists('target.img'):
    os.remove('target.img')
with open('target.img', 'wb') as f:
    f.truncate(target_size)
# what a used card had on it: wiped
subprocess.run(['sh', '-c', "printf 'label: dos\\nlabel-id: 0x18512e38\\nstart=2048, type=83\\n' | sfdisk -q target.img"],
               check=True)

loops = {}


def part(disk, number):
    """A loop device on a partition of an image (only these loop devices are detached at the end)."""
    table = json.loads(subprocess.run(['sfdisk', '-J', disk], stdout=subprocess.PIPE, check=True).stdout)['partitiontable']
    p = [x for x in table['partitions'] if x['node'].endswith(str(number))][0]
    key = (disk, number, p['start'], p['size'])
    if key not in loops:
        loops[key] = subprocess.run(['losetup', '-f', '--show', '-o', str(p['start'] * 512), '--sizelimit',
                                     str(p['size'] * 512), disk],
                                    stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout.strip()
    return loops[key]


real_run = clone.run


def run(cmd, input=None):
    if cmd[0] in ('partprobe', 'udevadm'):
        return ''
    if cmd[0] == 'mount':
        device, path = cmd[-2], cmd[-1]
        kind = subprocess.run(['blkid', '-o', 'value', '-s', 'TYPE', device], stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()
        if kind == 'vfat':
            return real_run(['fusefat', '-o', 'ro' if 'ro' in cmd else 'rw+', device, path])
        if kind == 'exfat':
            return real_run(['mount.exfat-fuse'] + (['-o', 'ro'] if 'ro' in cmd else []) + [device, path])
    if cmd[0] == 'umount':
        out = real_run(cmd)
        time.sleep(.3)
        return out
    return real_run(cmd, input)


clone.partition = part
clone.run = run
clone._reread_partitions = lambda device, message: None
clone._check_partitions = lambda device, layout: None
clone.SOURCE_DISK = os.path.join(HERE, 'source.img')
clone.TEMP_DIR = HERE
clone.MARKER = os.path.join(HERE, 'mp4m-clone.json')
clone.ROOT_MIN, clone.ROOT_ROOM = 48 * 1024 ** 2, 16 * 1024 ** 2
clone.USBMOUNT_CONF = os.path.join(HERE, 'usbmount.conf')
with open(clone.USBMOUNT_CONF, 'w') as f:
    f.write('ENABLED=1\nMOUNTPOINTS="/media/usb0"\n')
def unmount(path):
    if os.path.ismount(path):
        subprocess.run(['umount', path])
        time.sleep(.3)


def detach_all():
    for device in loops.values():
        subprocess.run(['losetup', '-d', device])
    loops.clear()


steps = []


def progress(**changes):
    if changes.get('same_id_before'):
        print('same disk ID as the source before: yes')
    if 'step' in changes and (not steps or steps[-1] != changes['step']):
        steps.append(changes['step'])


media = os.path.join(HERE, 'srcmedia')
check = os.path.join(HERE, 'check')
system.LOCK_DIR = os.path.join(HERE, 'locks')
for folder in (media, check, system.LOCK_DIR):
    os.makedirs(folder, exist_ok=True)
system.MEDIA_PATH = media
card = {'device': os.path.join(HERE, 'target.img'), 'size': target_size, 'id': 'x'}
clone.list_cards = lambda: [card]
# (also when the clone fails: nothing left mounted or attached)
try:
    real_run(['mount.exfat-fuse', '-o', 'ro', part(clone.SOURCE_DISK, 3), media])
    clone.clone(card, with_media, progress=progress)
    print('steps:', steps)
finally:
    unmount(media)
    detach_all()

print(subprocess.run(['sfdisk', '-d', 'target.img'], stdout=subprocess.PIPE, universal_newlines=True).stdout)
try:
    real_run(['fusefat', '-o', 'ro', part('target.img', 1), check])
    with open(os.path.join(check, 'cmdline.txt')) as f:
        print('boot:', sorted(os.listdir(check)), '|', f.read().strip())
    unmount(check)
    real_run(['mount', '-o', 'ro', part('target.img', 2), check])
    with open(os.path.join(check, 'etc', 'fstab')) as f:
        print('fstab:', f.read().replace('\n', ' | '))
    print('dhcpcd5:', sorted(os.listdir(os.path.join(check, 'var', 'lib', 'dhcpcd5'))))
    unmount(check)
    real_run(['mount.exfat-fuse', '-o', 'ro', part('target.img', 3), check])
    print('media:', sorted(os.listdir(check)), shutil.disk_usage(check).total // 1024 ** 2, 'MB')
finally:
    unmount(check)
    detach_all()
print('work folders left:', [name for name in os.listdir(HERE) if name.startswith(clone.WORK_PREFIX)])
