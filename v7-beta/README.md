# MP4MUSEUM v7 beta – fork

Based on the MP4MUSEUM v7 beta image by Julius Schmiedel (https://mp4museum.org/v7-beta/),
the author of MP4MUSEUM (https://github.com/JuliusCode/MP4MUSEUM).
Its source was not published, so these files were copied off a running v7 beta install
and are developed further here. The files exactly as they came off the image are in
commits `eff36bc` and `1ba2c91`.

Paths below mirror where each file lives on the Pi. Only files that differ from a
stock Raspberry Pi OS image are kept.

Base OS: Raspberry Pi OS (Legacy, Buster) 2023-05-03, kernel 5.10.103, Python 3.7.
Extra Python packages: Flask 2.2.5 (+ deps), python-vlc, RPi.GPIO.

The player is meant to run offline. A network is optional and only needed for the web interface.

| Path | Purpose |
|---|---|
| `boot/mp4museum.py` | Player (v6 player with sync mode removed; logo is now `mp4m-v7beta.jpg`) |
| `home/pi/mp4m-webservice.py` | Web interface (Flask, port 80, runs as root) |
| `home/pi/.bashrc` | Autostart on tty1: web service in background, then `/boot/mp4museum.py` |
| `home/pi/mp4m-v7beta.jpg` | Logo screen shown after boot |
| `home/pi/mp4museum-boot.mp4` | Boot video |
| `boot/config.txt` | Video/audio config (video lines set by the web UI video presets) |
| `boot/cmdline.txt` | `boot=overlay` – root filesystem is read-only via overlayfs |
| `etc/fstab` | `/boot` mounted ro; third partition (exFAT) mounted ro at `/media/internal` for media |
| `etc/usbmount/usbmount.conf` | USB sticks auto-mounted read-only at `/media/usb0..7` |
| `etc/systemd/system/getty@tty1.service.d/autologin.conf` | Auto-login of user `pi` on tty1 |
| `etc/initramfs-tools/scripts/overlay` | Read-only root with tmpfs overlay (standard raspi-config overlay script) |

## Web interface

Open `http://<network name>.local` in a browser on the same network.

- **Password:** `mp4museum` by default, can be changed on the System tab. It is stored
  hashed in `/boot/mp4m-password.txt`; delete that file to reset to the default.
- **Network name:** each player calls itself `mp4museum-xxxx.local`, where `xxxx` is made
  from the Pi's serial number, so several players can share a network. Change it on the
  System tab, or put the name in `/boot/hostname.txt` from a computer.
  The name is logged in `/tmp/mp4m-webservice.log` at startup.
- **Read-only storage:** `/boot` and `/media/internal` stay read-only, and are only made
  writable while the web interface saves something.
- **Video presets** only change the video lines in `/boot/config.txt`; other settings are kept.

Files the web interface may create in `/boot`: `mp4m-password.txt`, `hostname.txt`, `alsa.txt`.

## Trying out a change on the Pi

The root filesystem is a RAM overlay, so anything copied to `/home/pi` disappears at the
next reboot. That makes it safe for testing. From your computer:

```bash
scp v7-beta/home/pi/mp4m-webservice.py pi@mp4museum.local:
ssh pi@mp4museum.local
sudo pkill -f 'mp4m-webservice[.]py'
sudo python3 ~/mp4m-webservice.py
```

The new version changes the player's network name straight away; the terminal shows it,
e.g. `Network name: mp4museum-1a2b.local`. Use that name (or the IP address) from then on.
It stops when you close the SSH session; reboot to go back to the installed version.

## Installing permanently

1. `ssh pi@<name>.local`, run `sudo raspi-config`, open **Overlay File System**
   (under Performance Options or Advanced Options) and disable it. Reboot.
2. Copy the changed files into place, e.g.
   `scp v7-beta/home/pi/mp4m-webservice.py v7-beta/home/pi/mp4museum-boot.mp4 pi@<name>.local:`
3. Run `sudo raspi-config` again, enable the overlay file system, and answer **yes** to
   write-protecting the boot partition. Reboot.

## License

GNU General Public License v3, like the original MP4MUSEUM (see `LICENSE` in the repository root).
The original files are © Julius Schmiedel; changed files say so at the top. The web interface
was not published with a license of its own by its author and is included as part of the
GPL-licensed MP4MUSEUM project.
