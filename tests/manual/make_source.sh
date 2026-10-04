#!/bin/bash
# A 400 MB disk image laid out like a player's SD card (boot vfat, system ext4, media exFAT,
# disk ID 18512e38), for clone_real_tools.py. Run as root in a scratch folder.
# Part of https://github.com/lotech/mp4museum (added 2026). Licensed under the GNU GPL v3, see LICENSE.
set -e
rm -f source.img target.img
truncate -s 400M source.img
printf 'label: dos\nlabel-id: 0x18512e38\nunit: sectors\n/dev/x1 : start=8192, size=131072, type=c\n/dev/x2 : start=401408, type=83\n/dev/x3 : start=139264, size=262144, type=7\n' | sfdisk -q source.img
part() { # image, number -> a loop device on that partition
  read start size <<<$(sfdisk -J "$1" | python3 -c "import json,sys; p=[x for x in json.load(sys.stdin)['partitiontable']['partitions'] if x['node'].endswith('$2')][0]; print(p['start'], p['size'])")
  losetup -f --show -o $((start*512)) --sizelimit $((size*512)) "$1"
}
P1=; P2=; P3=
cleanup() { # also when a step fails: nothing left mounted or attached
  mountpoint -q m 2>/dev/null && umount m
  for p in "$P1" "$P2" "$P3"; do [ -n "$p" ] && losetup -d "$p" 2>/dev/null; done
  return 0
}
trap cleanup EXIT
P1=$(part source.img 1); P2=$(part source.img 2); P3=$(part source.img 3)
mkfs.vfat -n boot "$P1" >/dev/null; mkfs.ext4 -q -F -L rootfs "$P2"; mkfs.exfat -L Media "$P3" >/dev/null
mkdir -p m
fusefat -o rw+ "$P1" m
printf 'console=tty1 root=PARTUUID=18512e38-02 rootfstype=ext4 boot=overlay\n' > m/cmdline.txt
echo gpu_mem=256 > m/config.txt; echo mp4museum-24 > m/hostname.txt; echo 'hash' > m/mp4m-password.txt
umount m; sleep 0.5
mount "$P2" m; mkdir -p m/etc m/var/lib/dhcpcd5 m/home/pi m/usr/bin
printf 'PARTUUID=18512e38-01  /boot vfat defaults,ro 0 2\nPARTUUID=18512e38-02 / ext4 defaults,noatime 0 1\nPARTUUID=18512e38-03 /media/internal exfat defaults,ro,nofail 0 0\n' > m/etc/fstab
echo lease > m/var/lib/dhcpcd5/eth0.lease; echo duid > m/var/lib/dhcpcd5/duid
head -c 20M /dev/urandom > m/usr/bin/big; ln -s big m/usr/bin/link; echo hi > m/home/pi/.bashrc
umount m
mount.exfat-fuse "$P3" m
head -c 30M /dev/urandom > m/clip-loop.mp4; head -c 5M /dev/urandom > 'm/02 Artist statement.mp3'
mkdir -p m/.Spotlight-V100; echo mac > m/.Spotlight-V100/store; touch m/.upload-abc
umount m; sleep 0.5
echo ok
