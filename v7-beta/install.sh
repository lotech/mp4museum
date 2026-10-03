#!/bin/bash
# Install this version of the MP4MUSEUM web interface and player files
# onto a Raspberry Pi running the MP4MUSEUM v7 beta image.
# Afterwards, "sudo mp4m-update" (or the web interface) updates from GitHub
# without this script, unless files outside /boot change.
# Part of https://github.com/lotech/mp4museum, a fork of MP4MUSEUM by Julius Schmiedel.
# Licensed under the GNU GPL v3, see LICENSE.
#
# Run it on the Pi from the v7-beta folder of the repository:
#   sudo ./install.sh
set -e

cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo: sudo ./install.sh"
    exit 1
fi

if grep -q 'boot=overlay' /proc/cmdline; then
    echo "The overlay file system is on, so anything installed now would be lost at the next reboot."
    echo "Turn it off first: sudo raspi-config -> Overlay File System -> No (leave the boot partition"
    echo "read-only if it says so), then reboot and run this again."
    exit 1
fi

echo "Installing web interface to /boot/mp4m-web and the player to /boot/mp4museum.py"
# The updater keeps a player script that was edited on this Pi, and saves the new one next to it
python3 -B boot/mp4m-web/updater.py --from-dir ..

echo "Installing boot video, logo and .bashrc to /home/pi"
cp home/pi/mp4museum-boot.mp4 home/pi/mp4m-v7beta.jpg /home/pi/
if [ -f /home/pi/.bashrc ] && ! cmp -s home/pi/.bashrc /home/pi/.bashrc; then
    cp /home/pi/.bashrc /home/pi/.bashrc.bak
    echo "  (previous .bashrc saved as .bashrc.bak)"
fi
cp home/pi/.bashrc /home/pi/.bashrc
chown pi:pi /home/pi/mp4museum-boot.mp4 /home/pi/mp4m-v7beta.jpg /home/pi/.bashrc

# Earlier versions of the web interface lived in /home/pi
rm -rf /home/pi/mp4m-webservice.py /home/pi/mp4m-web

echo "Installing the mp4m-update command"
cp usr/local/bin/mp4m-update /usr/local/bin/mp4m-update
chmod 755 /usr/local/bin/mp4m-update

echo "Installing mp4m-webservice service"
cp etc/systemd/system/mp4m-webservice.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable mp4m-webservice.service

echo
echo "Done. Later updates: sudo mp4m-update, or System -> Software Update in the web interface."
echo "Now turn the overlay file system back on:"
echo "  sudo raspi-config -> Overlay File System -> Yes, and Yes to write-protecting the boot"
echo "  partition if it asks (if it says the boot partition is already read-only, that's fine)"
echo "then reboot."
