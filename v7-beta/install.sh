#!/bin/bash
# Install this version of the MP4MUSEUM web interface and player files
# onto a Raspberry Pi running the MP4MUSEUM v7 beta image.
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
    echo "Turn it off first: sudo raspi-config -> Overlay File System -> No, then reboot and run this again."
    exit 1
fi

echo "Installing web interface to /home/pi/mp4m-web"
rm -rf /home/pi/mp4m-web
cp -r home/pi/mp4m-web /home/pi/mp4m-web
find /home/pi/mp4m-web -name '__pycache__' -prune -exec rm -rf {} +

echo "Installing boot video, logo and .bashrc to /home/pi"
cp home/pi/mp4museum-boot.mp4 home/pi/mp4m-v7beta.jpg /home/pi/
if [ -f /home/pi/.bashrc ] && ! cmp -s home/pi/.bashrc /home/pi/.bashrc; then
    cp /home/pi/.bashrc /home/pi/.bashrc.bak
    echo "  (previous .bashrc saved as .bashrc.bak)"
fi
cp home/pi/.bashrc /home/pi/.bashrc
chown -R pi:pi /home/pi/mp4m-web /home/pi/mp4museum-boot.mp4 /home/pi/mp4m-v7beta.jpg /home/pi/.bashrc

# The web interface used to be a single file started from .bashrc
rm -f /home/pi/mp4m-webservice.py

echo "Installing mp4m-webservice service"
cp etc/systemd/system/mp4m-webservice.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable mp4m-webservice.service

echo
echo "Done. Now turn the overlay file system back on:"
echo "  sudo raspi-config -> Overlay File System -> Yes, and write-protect the boot partition"
echo "then reboot."
