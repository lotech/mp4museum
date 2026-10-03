# MP4MUSEUM v7 beta – files extracted from the released image

These files were copied off a running v7 beta image (https://mp4museum.org/v7-beta/).
Paths below mirror where each file lives on the Pi. Only files that differ from a
stock Raspberry Pi OS image are kept.

Base OS: Raspberry Pi OS (Legacy, Buster) 2023-05-03, kernel 5.10.103, Python 3.7.
Extra Python packages: Flask 2.2.5 (+ deps), python-vlc, RPi.GPIO.

| Path | Purpose |
|---|---|
| `boot/mp4museum.py` | Player (v6 player with sync mode removed; logo is now `mp4m-v7beta.jpg`) |
| `home/pi/mp4m-webservice.py` | Web interface (Flask, port 80, runs as root) |
| `home/pi/.bashrc` | Autostart on tty1: web service in background, then `/boot/mp4museum.py` |
| `home/pi/mp4m-v7beta.jpg` | Logo screen shown after boot |
| `home/pi/mp4museum-boot.mp4` | Boot video |
| `boot/config.txt` | Video/audio config (rewritten by the web UI video presets) |
| `boot/cmdline.txt` | `boot=overlay` – root filesystem is read-only via overlayfs |
| `etc/fstab` | `/boot` mounted ro; third partition (exFAT) mounted ro at `/media/internal` for media |
| `etc/usbmount/usbmount.conf` | USB sticks auto-mounted read-only at `/media/usb0..7` |
| `etc/systemd/system/getty@tty1.service.d/autologin.conf` | Auto-login of user `pi` on tty1 |
| `etc/initramfs-tools/scripts/overlay` | Read-only root with tmpfs overlay (standard raspi-config overlay script) |
