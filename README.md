# MP4MUSEUM (fork)

This is a fork of **[MP4MUSEUM](https://github.com/JuliusCode/MP4MUSEUM) by Julius Schmiedel**.
MP4MUSEUM, the player, the bootable images and the website [mp4museum.org](https://mp4museum.org)
are his work, and all credit for them goes to him.

For the official player, ready-made images and help, go to [mp4museum.org](https://mp4museum.org)
or the [original repository](https://github.com/JuliusCode/MP4MUSEUM). This fork is not
affiliated with or supported by the original project. I have used this for many of my own projects from art installations to film sets. 
As I added the odd feature I realised I should be sharing the code and so this fork shares those additional features. 
Without Julius’s original work this fork would not exist - so thanks again.

## What MP4MUSEUM is

A media player for exhibitions on the Raspberry Pi. It boots straight into playing the
videos and images on a USB stick (or SD card) in a loop and needs no network. Buttons on the
GPIO pins pause, or skip to the next file (pressed twice: the previous one; held: back to the
start), and files with `loop.` in their name repeat.

## What this fork adds

The original repository has the source of version 6. Version 7 is available as a
[beta image](https://mp4museum.org/v7-beta/), but its source has not been published.
This fork copies the v7 beta files off a running install and develops them further:

- **Web interface fixes:** the SD card's partitions go back to read-only after every change,
  video presets no longer overwrite the rest of `config.txt`, uploads go straight to the SD
  card, and file names are checked.
- **Login** with a password (default `mp4museum`), changeable in the web interface.
- **A network name per player** (`mp4museum-xxxx.local`), so several players can share a network.
- **Network settings:** a fixed address or DHCP for Ethernet and Wi-Fi, Wi-Fi networks and the
  Wi-Fi country (safe channels until one is chosen). Changes go back by themselves unless kept.
- **A new web interface design:** a player with Previous, Pause/Resume, Next and Rewind and how
  far the file has got; a playlist that fills the window, to start, rename, download, delete or
  switch off any file; uploads with progress; messages that don't push the page around; and an
  update bar when a new version is available.
- **Player:** plays only video, image and sound files, has a setting for how long images are
  shown, loops videos with omxplayer (it holds the last frame), brings back sync mode from
  version 6, uses one VLC instance for everything, and no longer uses a full CPU core while there
  is nothing to play. If it stops by itself it's started again, skipping a file that keeps
  stopping it. The button on pin 13: once next, twice previous, held back to the start.
- **Switch files off** without deleting them (also on USB sticks), and **copy files from a USB
  stick to the player**, so they play without the stick.
- **Easier to maintain and install:** the web interface is split into modules and templates,
  runs as a system service, and has an install script.
- **Updates from GitHub:** a Software Update button in the web interface, or
  `sudo mp4m-update` over SSH, installs the latest version and offers to reboot.
  `sudo mp4m-update --branch <name>` installs a branch to try it.
- **Your own boot video** if you want one: put `mp4museum-boot.mp4` on the SD card's boot partition.
  The original boot video and the MP4MUSEUM logo screen are kept.
- **The player's address on the logo screen** at start-up (can be turned off).
- **Clone a player to another SD card** in a USB card reader, with or without the media files.

The player is meant to run offline; a network is only needed for the web interface. It is not recommended to expose this to the internet as security is basic and designed to hinder, not stop a determined attacker.
See [`v7-beta/README.md`](v7-beta/README.md) for the files, how to try them on a Pi and how
to install them, and [`TODO.md`](TODO.md) for what's planned.

## Setting up a player from scratch

You need a Raspberry Pi (the player is tested on a Pi 3 B), an SD card of 8 GB or more, and a network with
internet for the setup. Once it's set up, the player needs no network. If you already have a
player, *System → Clone to another device* is quicker (see
[`v7-beta/README.md`](v7-beta/README.md#cloning-a-player-to-another-sd-card)).

The player runs on **Raspberry Pi OS (Legacy) Lite, Buster, 2023-05-03**, the system the
MP4MUSEUM v7 image is built on. Newer versions from Raspberry Pi Imager's list don't work yet.

1. **Download** [`2023-05-03-raspios-buster-armhf-lite.img.xz`](https://downloads.raspberrypi.org/raspios_oldstable_lite_armhf/images/raspios_oldstable_lite_armhf-2023-05-03/2023-05-03-raspios-buster-armhf-lite.img.xz)
   (336 MB) on a computer. Don't unpack it.
2. **Write it to the SD card** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
   - *Choose Device*: your Pi. *Choose OS*: *Use Custom* (at the bottom), and choose the file.
     *Choose Storage*: the SD card.
   - When it asks about OS customisation, choose *Edit Settings*: username **`pi`** (the
     player needs this name) and a password (`mp4museum` is what MP4MUSEUM uses), and on the
     *Services* tab, *Enable SSH* with password authentication. Wi-Fi can be set here too.
     Save, and *Yes* to use the settings.
3. **Keep room for the media partition.** Take the card out and put it back in: its boot
   partition shows up as a drive called `boot`. Open `cmdline.txt` in a text editor and take
   out this part (with the space in front of it), leaving the rest on one line:
   ```
    init=/usr/lib/raspi-config/init_resize.sh
   ```
   Save it, and eject the card. Without this, the system fills the card at its first start.
4. **Start the Pi** with the card, a screen (HDMI) and Ethernet. It restarts once by itself
   while it applies the settings.
5. **Log in over SSH** from a computer on the same network: `ssh pi@raspberrypi.local` (or the
   name you gave it in Imager, or the Pi's IP address from your router).
6. **Download this repository onto the Pi** (no git needed):
   ```bash
   mkdir -p ~/mp4m-src
   curl -fL https://github.com/lotech/mp4museum/archive/refs/heads/master.tar.gz | tar xz -C ~/mp4m-src --strip-components=1
   ```
   If `curl` says a certificate isn't valid yet, the Pi's clock hasn't been set from the
   internet: wait a minute and try again.
7. **Set it up:**
   ```bash
   cd ~/mp4m-src/v7-beta
   sudo ./setup.sh
   ```
   This takes 10 to 20 minutes. It makes the media partition (exFAT, the card's space after
   4 GB for the system), installs VLC, omxplayer, usbmount and the web interface's Python
   packages, sets the video settings, auto-login and USB sticks up as on the v7 image, installs
   the player and web interface (`install.sh`) and makes the system read-only. If it stops
   part way, run it again. If it says to reboot first, do that, then run it again.
8. **Reboot** (`sudo reboot`). The player starts and the logo screen shows its address
   (`http://mp4museum-xxxx.local`). The web interface's password is `mp4museum`: change it on
   the System tab. Upload media on the Media page, or copy it from a USB stick.

**Buttons** (physical pin numbers on the Pi's header, buttons that close when pressed):

| Button | Wire between | Does |
|---|---|---|
| Next | pin 13 (GPIO 27) and pin 1 (3.3 V) | Once: next file. Twice: previous file. Held: back to the start |
| Pause | pin 11 (GPIO 17) and pin 1 or 17 (3.3 V) | Pause and resume |

The Pi's internal pull-down resistors are used, so no resistors are needed. Wire buttons to
3.3 V, never 5 V.

From then on the player updates itself from GitHub (*System → Software Update*, or
`sudo mp4m-update`).

**From the MP4MUSEUM v7 beta image instead:** write
[`mp4m-v7beta.img.zip`](https://mp4museum.org/v7-beta/) (3.9 GB) to the card with Imager (no
settings), log in with `ssh pi@mp4museum.local`, password `mp4museum`, and install this fork on
it with `install.sh` as in [`v7-beta/README.md`](v7-beta/README.md#installing-permanently).

## What's in this repository

| Path | What it is |
|---|---|
| `mp4museum.py`, `mp4m-gpio.py`, `mp4m-keyboard.py`, `mp4museum-randomJPG.py`, `mp4museum DCIM chronologically.py` | The original version 6 player scripts, unchanged from the original repository |
| `v7-beta/` | The v7 beta files, at the paths where they live on the Pi, with this fork's changes |
| `TODO.md` | Planned work and ideas for this fork |
| `CLAUDE.md` | Notes for working on the code: the device, its limits, how updates and tests work |
| `tests/` | Tests (`python -m pytest tests`); `tests/manual/` clones a card image with the real tools (needs root) |
| `ci/tests.yml` | The test workflow for GitHub Actions (not switched on) |
| `LICENSE` | GNU General Public License v3, unchanged from the original repository |

## Using the original version 6 scripts

These instructions are from the original repository.

To run the player script on a fresh
[Raspberry Pi OS Lite](https://www.raspberrypi.com/software/operating-systems/), install:

```bash
sudo apt-get -y install vlc python3-pip
pip3 install python-vlc RPi.GPIO
```

Sync mode needs [omxplayer-sync](https://github.com/turingmachine/omxplayer-sync).

To change the MP4MUSEUM image: log in over SSH as user `pi` at `mp4museum.local`, password
`mp4museum`. On the Pi itself, press Ctrl+C to get to the console. The player is started from
`.bashrc`; change the standard script there or add your own.

## License

MP4MUSEUM is released by Julius Schmiedel under the
[GNU General Public License version 3](LICENSE), and so is this fork. The `LICENSE` file is
unchanged from the original repository.

- The original work is © Julius Schmiedel. Copyright notices in his files are kept.
- Files this fork has changed say so at the top, with the year. The full history of every
  change is in the git log.
- The v7 beta files in `v7-beta/` were copied from the publicly released v7 beta image.
  The v7 player script is based on the GPL version 6 player. The v7 web interface
  (now `v7-beta/boot/mp4m-web/`) was not published with a license of its own; it is
  included here as part of the GPL-licensed MP4MUSEUM project.
- The web interface's icons are from [Lucide](https://lucide.dev), under the ISC licence
  (in `v7-beta/boot/mp4m-web/static/icons.svg`).
