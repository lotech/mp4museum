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
| `boot/mp4museum.py` | Player: plays everything in `/media/*/` in order, GPIO pause (pin 11) and next (pin 13), sync mode with omxplayer-sync |
| `boot/mp4m-web/` | Web interface (Flask, port 80, runs as root): `webservice.py` (routes), `system.py` (partitions, config.txt, network name, password), `updater.py` (software update), `templates/`, `static/`. On the boot partition so it can be updated without turning off the overlay |
| `etc/systemd/system/mp4m-webservice.service` | Starts the web interface at boot |
| `usr/local/bin/mp4m-update` | The `sudo mp4m-update` command |
| `home/pi/.bashrc` | Autostart on tty1: runs `/boot/mp4museum.py`, and again if it stops by itself |
| `home/pi/mp4m-v7beta.jpg` | Logo screen shown after boot |
| `home/pi/mp4museum-boot.mp4` | Boot video (the original from the v7 image) |
| `boot/config.txt` | Video/audio config (video lines set by the web UI video presets) |
| `boot/cmdline.txt` | `boot=overlay` – root filesystem is read-only via overlayfs |
| `etc/fstab` | `/boot` mounted ro; third partition (exFAT) mounted ro at `/media/internal` for media |
| `etc/usbmount/usbmount.conf` | USB sticks auto-mounted read-only at `/media/usb0..7` |
| `etc/systemd/system/getty@tty1.service.d/autologin.conf` | Auto-login of user `pi` on tty1 |
| `etc/initramfs-tools/scripts/overlay` | Read-only root with tmpfs overlay (standard raspi-config overlay script) |

## Player

The player (`/boot/mp4museum.py`, started from `.bashrc`) plays the boot video, the
MP4MUSEUM logo, then every file in `/media/*/` (the internal media partition and USB sticks)
in alphabetical order, over and over.

- **Boot video:** the original MP4MUSEUM one (`/home/pi/mp4museum-boot.mp4`). To use your own,
  put it on the SD card's boot partition as `mp4museum-boot.mp4` (it shows up as a drive on
  a computer); delete it to go back to the original.

- **Loops:** a file with `loop.` in its name (e.g. `intro-loop.mp4`) plays again and again
  until Next is pressed; then the playlist carries on. Loop videos are played with
  omxplayer, which holds the last frame with a short pause at each loop. VLC, which plays
  everything else, shows a black frame each time it starts a video again on the Pi. omxplayer
  is no longer developed and isn't on newer Raspberry Pi OS versions, so VLC is used when it
  isn't installed, or for videos it can't decode on every Pi (only H.264 and MPEG-4 go to
  omxplayer; HEVC, for example, plays in VLC). VLC can also be chosen in the web interface (Media).
- **Images** are shown for 10 seconds, or as set in the web interface (`/boot/mp4m-player.txt`,
  `image_duration=<seconds>`). A new setting applies from the next image.
- A file that hasn't started playing after 20 seconds (broken file, stalled USB stick) is skipped.
- **If the player stops** by itself (an error, or out of memory, e.g. on an image far bigger
  than the screen), `.bashrc` starts it again, and the file it was playing is skipped until it
  is replaced or chosen in the web interface (a custom boot video: the original is played instead). Its output is in `/tmp/mp4museum.log` (also on
  the System tab). Ctrl-C on the console stops it for good, as before.
- **Images** more than 2048 pixels wide or high come out scrambled on a Pi 3 (after a long
  wait), so a Pi 3 or older skips them. The web interface marks them "too large" and warns
  when one is uploaded. Resize them to the screen's size, e.g. 1920×1080.
- **Buttons:** GPIO pin 11 pauses and resumes, pin 13 skips to the next file. The web interface
  has the same buttons, shows what is playing and how far it has got (the player writes
  `/tmp/mp4museum-status.json`), and can start any file in the playlist (it writes
  `/tmp/mp4museum-play.json` and sends the player the same signal as Next).
- **Sound:** the card number from `/boot/alsa.txt` (0 if not set), chosen in the web interface.
- **Sync mode** (from version 6): with `sync.mp4` and `sync-leader.txt` or `sync-player.txt`
  on a USB stick or in `/boot`, the player runs `omxplayer-sync` to play `sync.mp4` in sync
  across players. This needs [omxplayer-sync](https://github.com/turingmachine/omxplayer-sync)
  installed; without it the player plays as normal.

## Web interface

Open `http://<network name>.local` in a browser on the same network.

- **Media:** the player (what is playing, Pause/Resume, Next, and Rewind: back to the first
  frame, held until play is pressed), playback settings, and the
  playlist: every file the player plays, from the media partition and USB sticks, in order.
  Start any file from there (its play button or its name); upload files (several at once, with progress) or download and
  delete them.
- **Updates:** when the player has an internet connection, the page checks for a new version
  by itself (at most every few hours) and shows a bar to install it.

- **Password:** `mp4museum` by default, can be changed on the System tab. It is stored
  hashed in `/boot/mp4m-password.txt`; delete that file to reset to the default.
- **Network name:** each player calls itself `mp4museum-xxxx.local`, where `xxxx` is made
  from the Pi's serial number, so several players can share a network. Change it on the
  System tab, or put the name in `/boot/hostname.txt` from a computer.
  The name is logged at startup; see `journalctl -u mp4m-webservice`.
- **Read-only storage:** `/boot` and `/media/internal` stay read-only, and are only made
  writable while the web interface saves something.
- **Video presets** only change the video lines in `/boot/config.txt`; other settings are kept.

Files the web interface may create in `/boot`: `mp4m-password.txt`, `hostname.txt`, `alsa.txt`,
`mp4m-player.txt`, `mp4museum.py.new`. `mp4m-update.txt` is only read.

## Getting the code onto the Pi

The Pi can download the repository itself into `~/mp4m-src` (replace `master` with a
branch name to try a branch):

```bash
BRANCH=master
sudo rm -rf ~/mp4m-src && mkdir ~/mp4m-src
curl -fL https://github.com/lotech/mp4museum/archive/refs/heads/$BRANCH.tar.gz | tar xz -C ~/mp4m-src --strip-components=1
```

Without internet on the Pi, download the same file on a computer, copy it over with `scp`,
and unpack it with `tar xzf <file> -C ~/mp4m-src --strip-components=1`.

## Trying out a change

The root filesystem is a RAM overlay, so anything copied to `/home/pi` disappears at the
next reboot. That makes it safe for testing. After downloading as above:

```bash
sudo systemctl stop mp4m-webservice 2>/dev/null; sudo pkill -f 'mp4m-webservice[.]py'
sudo python3 -B ~/mp4m-src/v7-beta/boot/mp4m-web/webservice.py
```

The terminal shows the player's network name, e.g. `Network name: mp4museum-1a2b.local`.
It stops when you close the SSH session; reboot to go back to the installed version.

## Installing permanently

1. Run `sudo raspi-config` and open **Overlay File System** (under Performance Options or
   Advanced Options). Answer **No** to "Would you like the overlay file system to be enabled?".
   It then says the boot partition is read-only and can't be changed while the overlay is on;
   that's fine, leave it read-only (`install.sh` makes it writable when it needs to). Reboot.
2. Download the code as above, then run the install script:
   ```bash
   cd ~/mp4m-src/v7-beta
   sudo ./install.sh
   ```
   It installs the web interface to `/boot/mp4m-web` and the player to `/boot/mp4museum.py`
   (an edited player is kept; the new one is saved as `mp4museum.py.new`), the boot video,
   logo and `.bashrc` to `/home/pi`, the `mp4m-webservice` service and the `mp4m-update` command.
3. Run `sudo raspi-config` again, open **Overlay File System** and answer **Yes**. If it asks
   "Would you like the boot partition to be write-protected?", answer **Yes**; if it says the boot
   partition is already read-only, nothing more is needed. Reboot.

Check the web interface with `systemctl status mp4m-webservice`, and its log with
`journalctl -u mp4m-webservice`.

## Updating

Once installed, a player can update itself from GitHub, as long as it has an internet connection:

- **Web interface:** the bar at the top when an update is found, or System → Software Update
  → Check for Updates, then Install Update.
  The web interface restarts with the new version and offers to reboot.
- **Over SSH:** `sudo mp4m-update` checks, asks before installing, and offers to reboot.
  `sudo mp4m-update --check` only checks; `--help` lists the other options.

An update replaces the web interface in `/boot/mp4m-web`, and the player script
`/boot/mp4museum.py` unless it has been edited on that player. In that case the edited
script is kept and the new one is saved as `/boot/mp4museum.py.new`. Nothing outside `/boot`
can be updated this way, because the rest of the system is a read-only RAM overlay. When an
update changes files there (`.bashrc`, the boot video, the service), it says so; install
those with `install.sh` as above.

An update is checked before anything is changed, and swaps the web interface folder in one
step. If the power goes off in the middle of that, the web interface is put back when the
player next starts.

Updates come from the `master` branch of `lotech/mp4museum`. To use another repository or
branch, put it in `/boot/mp4m-update.txt`:

```
repo=lotech/mp4museum
branch=master
```

## License

GNU General Public License v3, like the original MP4MUSEUM (see `LICENSE` in the repository root).
The original files are © Julius Schmiedel; changed files say so at the top. The web interface
was not published with a license of its own by its author and is included as part of the
GPL-licensed MP4MUSEUM project. The icons are from [Lucide](https://lucide.dev) (ISC licence,
included in `boot/mp4m-web/static/icons.svg`).
