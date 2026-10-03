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
- [x] New boot video (later back to the original; a custom one can go in `/boot/mp4museum-boot.mp4`)
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

- [x] Bring back sync mode (v6 had it, using omxplayer-sync); only runs when omxplayer-sync is installed
- [x] Use one VLC instance instead of creating one per file
- [ ] Find out on the Pi whether the boot video still needs to play twice ("start twice" workaround, kept for now)
- [x] Setting for how long images are shown
- [x] "Now playing", Pause/Resume and Next in the web interface
- [x] Sound cards 10 and above (player reads all of `alsa.txt`, web interface allows 0–99)
- [x] Update check recognises an install from `install.sh` that already matches the latest version
- [x] Loop files froze on the last frame (VLC's input-repeat); the player now restarts them itself
- [x] VLC is no longer stopped when a file ends (stopping closes its window); on the Pi it still showed a black frame at each loop, hence omxplayer for loops
- [x] Loop videos with `omxplayer --loop` (tested on the Pi: holds the last frame with a short pause; VLC still showed a black frame at each loop). Default when omxplayer is installed; VLC can be chosen in the web interface (Media)
- [ ] Test on the Pi: is there still a black frame between ordinary files in VLC? Does a video's last frame stay up during an audio-only file after it (if so, stop VLC before audio files)? omxplayer: audio device, pause from the GPIO button
- [ ] Seamless loops: omxplayer pauses briefly at each loop, and isn't on newer OS versions. Try mpv (`--loop-file=inf`; not on the image, would need installing), or VLC seeking back to the start just before the end (may freeze like `input-repeat`, and cut the last fraction of a second)
- [ ] Test on the Pi 3B: boot video shows, images use the set duration, loop files, pause/next from the web and GPIO, sync mode (needs omxplayer-sync on the image?)

## 4. Web interface design

- [x] Messages float over the page instead of pushing it down; Reboot and Log out in the header
- [x] Player card: what is playing, how far it has got, Pause/Resume and Next
- [x] Playlist beside the player: every file the player plays (media partition and USB sticks), start any file from it, upload with a progress bar, download, delete
- [x] Update bar when a new version is available (checked by the page at most every few hours, quietly when offline)
- [x] Sound, Video and System tabs in side-by-side cards; sound cards listed with a Use button; Lucide icons
- [ ] Test on the Pi: choosing a file (VLC and omxplayer loops), the position for videos and images, uploading several files, the update bar
- [ ] Test on the Pi: `omxplayer -i` reports the codec as expected (loops of H.264 files go to omxplayer, HEVC ones to VLC)

## 5. Installation

- [ ] Install script to set up v7 on a fresh Raspberry Pi OS, instead of depending on the image (`install.sh` only covers the v7 image so far)

## Development

- [x] `CLAUDE.md` with the constraints and conventions for working on the code
- [x] Commit the test suite to the repository (`tests/`, run with `python -m pytest tests`)
- [ ] Run the tests on GitHub for every pull request: the workflow is written (`ci/tests.yml`) but not enabled, to save Actions minutes; move it to `.github/workflows/` to turn it on

## Ideas

- [ ] Update from a file (USB stick or upload) for players that are never online
- [ ] Updates that change files outside `/boot` (applied at boot, before the overlay is set up)

- [ ] Show the network name on screen (e.g. on the logo screen), since every player now has its own name
- [ ] Announce the web interface over Bonjour/mDNS (`_http._tcp`) so players show up in network browsers
- [ ] Offline setup: create a Wi-Fi hotspot when no network is found, so the web interface can be reached without a router (there is no Wi-Fi setting yet)
- [x] Upload progress bar and multiple files at once (large videos give no feedback while uploading)
- [ ] "Restore default config.txt" button (the old "Auto" video preset used to do this)
- [x] "Save and Reboot" asks for confirmation twice
