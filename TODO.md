# TODO

Work on the v7 beta fork in `v7-beta/`. The player must keep working offline;
a network is optional.

## 1. Web service fixes

- [x] Make `/boot` and `/media/internal` read-only again after every write
- [x] Video presets change only the video lines in `config.txt` (NTSC/PAL were also placed under `[pi4]`, so they did nothing on a Pi 3)
- [x] Delete, reboot and auto-sound are POST requests; filenames can no longer inject script into the page
- [x] Uploads stream to the SD card instead of RAM, with a free-space check
- [x] Filename checks (no hidden files, no characters exFAT can't store)
- [x] Login with password (default `mp4museum`), changeable in the web interface
- [x] Unique network name per player (`mp4museum-xxxx.local`), changeable in the web interface
- [x] Credit MP4MUSEUM and link to the website and source
- [x] New boot video
- [x] Remove duplicated network code
- [x] Test on the Pi 3B, including permanent install and boot video

## 2. Code structure

- [x] Split `mp4m-webservice.py` into a Flask app with separate template and CSS files
- [x] Run the web service as a systemd service instead of from `.bashrc`
- [x] Install script for the web interface (`v7-beta/install.sh`)
- [x] The reboot confirmation page shares the CSS and reboot JavaScript
- [x] Remove the unused Wi-Fi placeholder route
- [x] Update from GitHub: `sudo mp4m-update` and a Software Update button (web interface moved to `/boot/mp4m-web` so it can be updated with the overlay on)
- [ ] Test on the Pi 3B

## 3. Player

- [ ] Bring back sync mode (v6 had it, using omxplayer-sync)
- [ ] Use one VLC instance instead of creating one per file (removes the "start twice" workaround)
- [ ] Setting for how long images are shown
- [ ] "Now playing" / status in the web interface
- [ ] Player reads only the first character of `alsa.txt`, so sound cards 10+ don't work (web UI is limited to 0–9 for now)

## 4. Installation

- [ ] Install script to set up v7 on a fresh Raspberry Pi OS, instead of depending on the image (`install.sh` only covers the v7 image so far)

## Ideas

- [ ] Update from a file (USB stick or upload) for players that are never online
- [ ] Updates that change files outside `/boot` (applied at boot, before the overlay is set up)

- [ ] Show the network name on screen (e.g. on the logo screen), since every player now has its own name
- [ ] Announce the web interface over Bonjour/mDNS (`_http._tcp`) so players show up in network browsers
- [ ] Offline setup: create a Wi-Fi hotspot when no network is found, so the web interface can be reached without a router (there is no Wi-Fi setting yet)
- [ ] Upload progress bar and multiple files at once (large videos give no feedback while uploading)
- [ ] "Restore default config.txt" button (the old "Auto" video preset used to do this)
- [x] "Save and Reboot" asks for confirmation twice
