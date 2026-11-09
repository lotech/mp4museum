#!/bin/bash
# Turn a fresh Raspberry Pi OS Lite (Legacy, Buster) 2023-05-03 into an MP4MUSEUM player,
# without the MP4MUSEUM v7 beta image: makes the media partition, installs what the v7 image
# has (VLC, omxplayer, Flask, usbmount, exFAT), runs install.sh and turns the overlay on.
# Added 2026 in https://github.com/lotech/mp4museum, a fork of MP4MUSEUM by Julius Schmiedel.
# Licensed under the GNU GPL v3, see LICENSE.
#
# Before the Pi first starts, take " init=/usr/lib/raspi-config/init_resize.sh" out of
# cmdline.txt on the card's boot partition, so the system doesn't fill the card. Then, on the
# Pi, from the v7-beta folder of the repository (needs an internet connection):
#   sudo ./setup.sh
# It can be run again if it stops part way.
set -e

cd "$(dirname "$0")"

DISK=/dev/mmcblk0
ROOT_PART=${DISK}p2
MEDIA_PART=${DISK}p3
# The system partition is made this big (the rest of the card is for media)
ROOT_SECTORS=$((4 * 1024 * 1024 * 2))     # 4 GiB in 512-byte sectors
MIN_MEDIA_SECTORS=$((1024 * 1024 * 2))    # at least 1 GiB of media space
ALIGN=8192                                # 4 MiB, as the image's partitions

fail() {
    echo
    echo "$@" >&2
    exit 1
}

# --- checks, before anything is changed

[ "$(id -u)" -eq 0 ] || fail "Please run with sudo: sudo ./setup.sh"

if grep -q 'boot=overlay' /proc/cmdline; then
    fail "The overlay file system is on: this Pi is already set up. To update it, use
sudo mp4m-update, or install.sh with the overlay off (see the README)."
fi

. /etc/os-release
[ "$VERSION_CODENAME" = buster ] || fail "This needs Raspberry Pi OS (Legacy) Lite, Buster
(2023-05-03), not $PRETTY_NAME. See the README for where to download it."

id pi >/dev/null 2>&1 || fail "There is no user 'pi'. The player runs as pi: write the card
again and, in Raspberry Pi Imager's settings, keep the username pi."

[ "$(findmnt -n -o SOURCE /)" = "$ROOT_PART" ] || fail "The system isn't on $ROOT_PART
(the SD card). Start the Pi from its SD card."

# --- the media partition

# One line of "sfdisk --dump" for partition $1: start and size in sectors
part_start() { sfdisk --dump "$DISK" | sed -n "s|^${DISK}p$1 *: *start= *\([0-9]*\).*|\1|p"; }
part_size() { sfdisk --dump "$DISK" | sed -n "s|^${DISK}p$1 *:.*size= *\([0-9]*\).*|\1|p"; }
disk_id() { blkid -p -s PTUUID -o value "$DISK"; }
# The disk ID the system starts with (root=PARTUUID=<disk ID>-02 in cmdline.txt, also in fstab)
BOOT_DISK_ID=$(sed -n 's/.*root=PARTUUID=\([0-9a-f]\{8\}\)-02.*/\1/p' /boot/cmdline.txt 2>/dev/null || true)
# Buster's sfdisk can replace a disk ID of 0x80000000 or more by a random one: put it back
keep_disk_id() {
    local id=${BOOT_DISK_ID:-$1}
    [ "$(disk_id)" = "$id" ] && return
    echo "  (putting the disk ID $id back)"
    # (stored with its last byte first)
    printf '%b' "\\x${id:6:2}\\x${id:4:2}\\x${id:2:2}\\x${id:0:2}" |
        dd of="$DISK" bs=1 seek=440 count=4 conv=notrunc status=none
    sync
    [ "$(disk_id)" = "$id" ] || fail "The disk ID changed and couldn't be put back.
Don't restart the Pi: it would not start. Write the card again."
}

if [ -z "$(part_start 3)" ]; then
    disk_sectors=$(cat /sys/block/${DISK##*/}/size)
    root_start=$(part_start 2)
    root_size=$(part_size 2)
    root_end=$((root_start + root_size))
    [ $((root_start % ALIGN)) -eq 0 ] || fail "The system partition starts at an unexpected place
($root_start). Is this the Raspberry Pi OS image?"
    new_root_size=$((root_size > ROOT_SECTORS ? root_size : ROOT_SECTORS))
    media_start=$(( (root_start + new_root_size + ALIGN - 1) / ALIGN * ALIGN ))
    if [ $((disk_sectors - media_start)) -lt $MIN_MEDIA_SECTORS ]; then
        if [ $((disk_sectors - root_end)) -lt $ALIGN ]; then
            fail "The system already fills the SD card, so there's no room for the media partition.
Write the card again, and before the Pi first starts, take this out of cmdline.txt on its boot
partition:  init=/usr/lib/raspi-config/init_resize.sh"
        fi
        fail "The SD card is too small: it needs 8 GB or more."
    fi

    echo "Making the system partition $((new_root_size / 2048)) MB and a media partition of the rest"
    id_before=$(disk_id)
    keep_disk_id "$id_before"
    if [ "$new_root_size" -ne "$root_size" ]; then
        # Like growpart: change the table without re-reading it (the system is on it),
        # then tell Linux the partition's new size
        echo ",$new_root_size" | sfdisk --quiet --no-reread --no-tell-kernel -N 2 "$DISK"
        keep_disk_id "$id_before"
    fi
    echo "start=$media_start, type=7" | sfdisk --quiet --no-reread --no-tell-kernel --append "$DISK"
    keep_disk_id "$id_before"
    partx -u -n 2 "$DISK" || true
    partx -a -n 3 "$DISK" || true
    udevadm settle || true
    if [ "$(cat /sys/class/block/${ROOT_PART##*/}/size)" -ne "$new_root_size" ] ||
       [ ! -e "/sys/class/block/${MEDIA_PART##*/}" ]; then
        echo
        echo "The new partitions are written, but Linux needs a restart to use them."
        echo "Run  sudo reboot  and then this script again."
        exit 0
    fi
fi

# (also after a restart in the middle: grows the file system to the partition, or does nothing)
resize2fs "$ROOT_PART" >/dev/null 2>&1 || resize2fs "$ROOT_PART"

# --- software

echo "Installing VLC, omxplayer, usbmount and exFAT support"
# Buster's packages have moved to legacy.raspbian.org
sed -i 's|http://raspbian.raspberrypi.org/raspbian/|http://legacy.raspbian.org/raspbian/|' \
    /etc/apt/sources.list
# Raspberry Pi's VLC decodes video in hardware; Debian's newer version (a security update)
# doesn't, and would win on its version number
cat > /etc/apt/preferences.d/mp4m-vlc <<'EOF'
# MP4MUSEUM: Raspberry Pi's VLC, which decodes video in hardware
Package: vlc* libvlc*
Pin: origin archive.raspberrypi.org
Pin-Priority: 1001
EOF
export DEBIAN_FRONTEND=noninteractive
# (the archive's Release files say "oldoldstable" now: --allow-releaseinfo-change)
apt-get update --allow-releaseinfo-change -qq
apt-get install -y -qq --no-install-recommends \
    vlc-bin vlc-plugin-base vlc-plugin-video-output omxplayer \
    usbmount exfat-fuse exfat-utils python3-pip python3-rpi.gpio rsync

echo "Installing Flask and python-vlc (the versions on the v7 image)"
pip3 install --quiet --no-cache-dir \
    flask==2.2.5 werkzeug==2.2.3 jinja2==3.1.6 markupsafe==2.1.5 itsdangerous==2.1.2 \
    click==8.1.7 importlib-metadata==6.7.0 zipp==3.15.0 typing-extensions==4.7.1 \
    python-vlc==3.0.18122

# --- the media partition's file system, mounted read-only at /media/internal

if [ -z "$(blkid -p -s TYPE -o value "$MEDIA_PART" 2>/dev/null)" ]; then
    echo "Formatting the media partition (exFAT)"
    mkfs.exfat -n Media "$MEDIA_PART" >/dev/null
fi
mkdir -p /media/internal
if ! grep -q '[[:space:]]/media/internal[[:space:]]' /etc/fstab; then
    echo "PARTUUID=${BOOT_DISK_ID:-$(disk_id)}-03  /media/internal exfat defaults,ro,nofail 0 0" >> /etc/fstab
fi

# --- system settings as on the v7 image

echo "Setting up USB sticks, auto-login, the screen and the console"
# USB sticks mounted read-only at /media/usb0..7
if [ ! -e /etc/usbmount/usbmount.conf.mp4m-orig ]; then
    cp -p /etc/usbmount/usbmount.conf /etc/usbmount/usbmount.conf.mp4m-orig
fi
cp etc/usbmount/usbmount.conf /etc/usbmount/usbmount.conf
# Buster's udev mounts in a namespace of its own, so usbmount's mounts were invisible to the rest
mkdir -p /etc/systemd/system/systemd-udevd.service.d
printf '%s\n' "# MP4MUSEUM: make usbmount's mounts of USB sticks visible" "[Service]" "PrivateMounts=no" \
    > /etc/systemd/system/systemd-udevd.service.d/mp4m-usbmount.conf

# Log in user pi on the screen (tty1), where .bashrc starts the player
mkdir -p /etc/systemd/system/getty@tty1.service.d
cp etc/systemd/system/getty@tty1.service.d/autologin.conf /etc/systemd/system/getty@tty1.service.d/
systemctl set-default multi-user.target >/dev/null
systemctl enable ssh >/dev/null 2>&1 || true
systemctl daemon-reload

# config.txt (video settings, changed later on the web interface's Video tab) as on the image
if [ ! -e /boot/config.txt.mp4m-orig ]; then
    cp /boot/config.txt /boot/config.txt.mp4m-orig
fi
cp boot/config.txt /boot/config.txt
# A quiet console, no screen blanking, and no growing of the system partition
cmdline=$(head -n 1 /boot/cmdline.txt | sed 's| init=/usr/lib/raspi-config/init_resize.sh||')
for option in logo.nologo consoleblank=0 loglevel=1 quiet; do
    case " $cmdline " in
        *" ${option%%=*}="*|*" $option "*) ;;
        *) cmdline="$cmdline $option" ;;
    esac
done
echo "$cmdline" > /boot/cmdline.txt.new
mv /boot/cmdline.txt.new /boot/cmdline.txt
sync

# --- the player and web interface

MP4M_SETUP=1 ./install.sh

# --- read-only from now on

echo
echo "Turning the overlay file system on, and the boot partition read-only"
raspi-config nonint do_overlayfs 0
grep -q 'boot=overlay' /boot/cmdline.txt || fail "Turning the overlay on didn't work: run
sudo raspi-config -> Performance Options -> Overlay File System -> Yes, then reboot."

echo
echo "Done. The player starts after a reboot: sudo reboot"
echo "The web interface is at the address the logo screen shows (http://mp4museum-xxxx.local),"
echo "password mp4museum."
