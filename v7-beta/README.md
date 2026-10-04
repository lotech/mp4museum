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
| `boot/mp4museum.py` | Player: plays everything in `/media/*/` in order, GPIO pause (pin 11) and next/previous/back to the start (pin 13), sync mode with omxplayer-sync |
| `boot/mp4m-web/` | Web interface (Flask, port 80, runs as root): `webservice.py` (routes), `system.py` (partitions, config.txt, network name, password), `updater.py` (software update), `templates/`, `static/`. On the boot partition so it can be updated without turning off the overlay |
| `etc/systemd/system/mp4m-webservice.service` | Starts the web interface at boot |
| `usr/local/bin/mp4m-update` | The `sudo mp4m-update` command |
| `home/pi/.bashrc` | Autostart on tty1: runs `/boot/mp4museum.py`, and again if it stops by itself |
| `boot/mp4m-web/static/logo.jpg` | Logo screen shown after the boot video (a frame from it, crediting MP4MUSEUM by Julius Schmiedel); in the web interface folder so updates bring it |
| `home/pi/mp4m-v7beta.jpg` | The v7 image's logo screen, shown if the one above isn't there |
| `home/pi/mp4museum-boot.mp4` | Boot video (the original from the v7 image) |
| `boot/config.txt` | Video/audio config (video lines set by the web UI video presets) |
| `boot/cmdline.txt` | `boot=overlay` – root filesystem is read-only via overlayfs |
| `etc/fstab` | `/boot` mounted ro; third partition (exFAT) mounted ro at `/media/internal` for media |
| `etc/usbmount/usbmount.conf` | USB sticks auto-mounted read-only at `/media/usb0..7` |
| `etc/systemd/system/getty@tty1.service.d/autologin.conf` | Auto-login of user `pi` on tty1 |
| `etc/initramfs-tools/scripts/overlay` | Read-only root with tmpfs overlay (standard raspi-config overlay script) |

## Player

The player (`/boot/mp4museum.py`, started from `.bashrc`) plays the boot video, the
MP4MUSEUM logo, then every video, image and sound file in `/media/*/` (the internal media
partition and USB sticks) in alphabetical order, over and over. Other files are left out (an SD
card in a USB reader has a Pi's boot files on it).

- **Boot video:** the original MP4MUSEUM one (`/home/pi/mp4museum-boot.mp4`). To use your own,
  put it on the SD card's boot partition as `mp4museum-boot.mp4` (it shows up as a drive on
  a computer); delete it to go back to the original. It plays once; System → Start-up can
  make that twice (as in the original, a warm-up for the video output) or not at all.
- **Switching files off:** the eye button next to a file in the playlist leaves it out without
  changing or moving it (also on USB sticks); press it again to switch it back on. The list is
  `/boot/mp4m-disabled.txt`. Renaming a switched-off file in the web interface keeps it off.
  Switching off the file that is playing moves on to the next one. Files on USB sticks are
  remembered by where the stick is mounted (`usb0` for the first one plugged in).
- **Logo screen:** shows the player's address (`http://<name>.local` and its IP address) in the
  corner for its 10 seconds, so it's easy to find the web interface. It can be turned off in
  System → Start-up.

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
- **Buttons:** GPIO pin 11 pauses and resumes. The button on pin 13 (wired to 3.3 V, e.g. pin 1):
  pressed once, the next file (or play, when paused or held at the first frame); twice within
  0.4 s, the previous file; held for 1 s, back to the start of the file, held there until it is
  pressed again. A single press acts 0.4 s after it, once it's clear no second one follows.
  The web interface
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

- **Media:** the player (what is playing and with which program, VLC or omxplayer; Pause/Resume,
  Next, and Rewind: the file starts again and holds its first frame until play is pressed),
  playback settings (changing the loop player starts the loop video playing now again), and the
  playlist: every file the player plays, from the media partition and USB sticks, in order.
  Start any file from there (its play button or its name); upload files (several at once, with progress),
  rename them (files play in alphabetical order; add `-loop` to repeat a video), download or delete them.
  Files on a USB stick can be copied to the player, so they play without the stick. The copy runs
  in the background (reboots and updates wait for it); on a Pi 3 it may slow down playback meanwhile.
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
- **Graphics memory** (Video tab): `gpu_mem` in `/boot/config.txt`, 128 MB on the v7 image.
  256 MB is recommended on a Pi 3 (1 GB), 512 MB on a Pi 4 with 2 GB or more. Needs a reboot.
  Boards with 512 MB or less are only offered values Linux can still start with, and
  `gpu_mem_256`/`_512`/`_1024` lines (which win over `gpu_mem`) are taken out, and `gpu_mem`
  lines in model sections such as `[pi4]` get the same value.
- **Device** (System tab): the Pi's model, memory, graphics memory in use, temperature, whether
  the power supply has been too weak since it started, free space and OS version.

Files the web interface may create in `/boot`: `mp4m-password.txt`, `hostname.txt`, `alsa.txt`,
`mp4m-player.txt`, `mp4m-disabled.txt`, `mp4museum.py.new`. `mp4m-update.txt` is only read.

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
   logo and `.bashrc` to `/home/pi`, the `mp4m-webservice` service and the `mp4m-update` command,
   and exfat-utils if it can (for copying to an SD card; it needs an internet connection).
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
  `sudo mp4m-update --branch <name>` installs a branch to try it. Updates are still looked for
  on the usual branch, and the update bar says that installing one leaves the branch.

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

## Cloning a player to another SD card

To set up another Pi the same way, put an SD card in an external USB card reader and plug it
into the player. System → Clone to another device lists the card; choose whether to copy the
media files, then Copy. It takes about 2–3 minutes, plus about a minute per GB of media. When
it says the card is ready, take it out; the next card put in shows up in the list.

- **The card is erased**, also if it already has MP4MUSEUM on it.
- **It gets this player:** the boot partition as it is (settings, password, `config.txt`, the
  player and web interface), and the system as installed (not what is only in RAM now).
- **Its own network name:** `hostname.txt` isn't copied, so the new player makes its name from
  its own serial number. Set a name in the web interface once it's running.
- **Media files:** copied, or left out (an empty media partition).
- **Partitions:** the system partition is the size the system needs plus 1 GB, and the media
  partition fills the rest of the card. Use a card of 8 GB or more, bigger with the media files;
  the page says if it's too small.
- **Partition IDs:** the card gets new ones, so it can't be mixed up with this player's card.
  If it had the same ones before (a card made from the same image), reboot this player once
  afterwards.

It needs `mkfs.exfat` from exfat-utils, which isn't on the v7 image: `install.sh` installs it
when the Pi has an internet connection (run it again later if it didn't). It only works with the
overlay file system on, on a player started from its own SD card. While a card is being made,
the web interface doesn't reboot or install updates, and neither does `mp4m-update`.
Every card made this way, like every card made from the v7 image, shares this player's SSH host
keys and machine ID.

## License

GNU General Public License v3, like the original MP4MUSEUM (see `LICENSE` in the repository root).
The original files are © Julius Schmiedel; changed files say so at the top. The web interface
was not published with a license of its own by its author and is included as part of the
GPL-licensed MP4MUSEUM project. The icons are from [Lucide](https://lucide.dev) (ISC licence,
included in `boot/mp4m-web/static/icons.svg`).
