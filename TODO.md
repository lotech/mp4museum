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
- [x] Tested on a Pi 3 B+: `install.sh`, Software Update in the web interface, `sudo mp4m-update --branch` and the update bar's note when switching back to `master`

## 3. Player

- [x] Bring back sync mode (v6 had it, using omxplayer-sync); only runs when omxplayer-sync is installed
- [x] Use one VLC instance instead of creating one per file
- [x] Find out on the Pi whether the boot video still needs to play twice ("start twice" workaround). Setting for it in System → Start-up (`boot_video_plays`: 2, 1, 0). Pi 3 B+: with no boot video, a reboot and 4 cold starts (unplugged) all showed the logo with the address, then the first file properly. Sound not checked (no clip with sound, nothing plugged in). The boot video now plays once by default (System → Start-up: once, twice, not at all)
- [x] One button (pin 13) does three things: pressed once next (or play when paused), twice previous, held back to the start
- [x] Switch a file off without removing it (eye button in the playlist; `/boot/mp4m-disabled.txt`, so USB sticks work too and files aren't changed)
- [x] Tested on the Pi in the exhibition case: once, twice and held work as expected with 0.4 s and 1 s
- [x] New logo screen: a frame from the boot video with the credits (`boot/mp4m-web/static/logo.jpg`, so updates bring it); the address text scaled to the picture
- [ ] Show the logo instead of the console text while the Pi starts. No Plymouth on the image (it would need internet to install), so: a systemd service early in boot that draws the image on the framebuffer (e.g. a raw dump in the framebuffer's format, `/dev/fb0`, 1920x1080 from `cmdline.txt`), and keep the console text off the screen (`console=tty3` or `vt.global_cursor_default=0` in `cmdline.txt`; tty1 still runs the player). Outside `/boot`, so it comes with `install.sh`. Test on the Pi
- [x] Previous file button; back to the start (rewind) moved apart from the other buttons, with its own icon
- [x] Setting for how long images are shown
- [x] "Now playing", Pause/Resume and Next in the web interface
- [x] Sound cards 10 and above (player reads all of `alsa.txt`, web interface allows 0–99)
- [x] Update check recognises an install from `install.sh` that already matches the latest version
- [x] Loop files froze on the last frame (VLC's input-repeat); the player now restarts them itself
- [x] VLC is no longer stopped when a file ends (stopping closes its window); on the Pi it still showed a black frame at each loop, hence omxplayer for loops
- [x] Loop videos with `omxplayer --loop` (tested on the Pi: holds the last frame with a short pause; VLC still showed a black frame at each loop). Default when omxplayer is installed; VLC can be chosen in the web interface (Media)
- [ ] Test on the Pi: is there still a black frame between ordinary files in VLC? Does a video's last frame stay up during an audio-only file after it (if so, stop VLC before audio files)? omxplayer: audio device, pause from the GPIO button
- [ ] Seamless loops: omxplayer pauses briefly at each loop, and isn't on newer OS versions. Try mpv (`--loop-file=inf`; not on the image, would need installing), or VLC seeking back to the start just before the end (may freeze like `input-repeat`, and cut the last fraction of a second). mpv on Buster wasn't better (see below); try it again on a newer Raspberry Pi OS or a Pi 4 (KMS and hardware decoding are the default there, and omxplayer doesn't exist): seamless loops, no black frames between files, images, control over its IPC socket
- [ ] Test on the Pi 3B: boot video shows, images use the set duration, loop files, pause/next from the web and GPIO, sync mode (needs omxplayer-sync on the image?)
- [x] The player is started again if it stops by itself (`.bashrc`, needs `install.sh`), skipping the file that stopped it; its log on the System tab
- [x] Images over 2048 pixels wide or high are skipped on a Pi 3 or older (3300 x 2550 and a 4K PNG came out scrambled after a long wait)
- [ ] Check the image limit on the Pi: does 2048 wide show, and 2560? And what a Pi 4 can show
- [ ] Find out what stopped the player the first time (the 12 MB PNG: out of memory?)
- [x] Images kept VLC busy (one core at 100 % on a Pi 3, video ~0 %): it converted the still 10 times a second. `:image-chroma=I420` converts it once (`image-fps` made a paused image move on). To check on the Pi: CPU while an image shows, and whether large images show now
- [ ] Test on the Pi: with 256 MB, does a 3300 x 2550 image show? (Then the 2048 pixel limit could go up)
- [x] Heat: a Pi 3 B+ (heatsink, no case) ran at 89 °C and slowed itself down. Images were part of it (fixed: 100 % CPU -> 5 %), but with the CPU idle, looping a video, that board still reached 79.5 °C in 10 minutes; another Pi 3 B+ with the same SD card, file and place stayed at 44 °C. That board (or its heatsink pad) was faulty
- [x] Tried mpv on the Pi 3B (Buster): it loops with a pause like omxplayer's, so no gain there. It also needs the KMS driver (`dtoverlay=vc4-fkms-v3d`, only set for a Pi 4 in `config.txt`; without it mpv has no video output: the Debian build has no `--vo=rpi`), installing from `legacy.raspbian.org` (Buster's packages moved there), `--hwdec=mmal-copy` (frames copied by the CPU, it dropped frames) and the picture was the wrong size. Worked: `mpv --fs --vo=gpu --gpu-context=drm --hwdec=mmal-copy --loop-file=inf <file>`
- [x] Show the network name on screen: `http://<name>.local` and the IP address on the logo screen (VLC marquee), setting `show_address`
- [x] Tested on a Pi 3 B+: the address and IP show on the logo screen, then the playlist starts without them
- [ ] Test on the Pi: does the IP address appear when the network comes up during the logo (e.g. boot video off, cold start)?
- [x] Only video, image and sound files are played and listed (`MEDIA_TYPES`, the same list in the player and the web interface); the media partition's other files are listed as "not played", other files on USB sticks not at all, hidden files never

## 4. Web interface design

- [x] Messages float over the page instead of pushing it down; Reboot and Log out in the header
- [x] Player card: what is playing, how far it has got, Pause/Resume and Next
- [x] Playlist beside the player: every file the player plays (media partition and USB sticks), start any file from it, upload with a progress bar, download, delete
- [x] Update bar when a new version is available (checked by the page at most every few hours, quietly when offline)
- [x] Sound, Video and System tabs in side-by-side cards; sound cards listed with a Use button; Lucide icons
- [ ] Test on the Pi: choosing a file (VLC and omxplayer loops), the position for videos and images, uploading several files, the update bar
- [ ] Test on the Pi: `omxplayer -i` reports the codec as expected (loops of H.264 files go to omxplayer, HEVC ones to VLC)
- [x] Click a file's name to play it; play buttons stay on when the player isn't running and say why
- [x] The player icon flickered every few seconds (it was set again on every status check); the reboot spinner turns around its centre
- [x] Very large images (bigger than a 4K screen) marked in the playlist, with a warning when uploaded
- [x] Rewind button: back to the first frame, held until play (omxplayer loops show it in VLC, then loop in omxplayer again)
- [x] Rewind tested on the Pi: omxplayer loops went black, then took a second to play; VLC held the frame but was slow to play on, with a black frame. Now the file starts again and pauses at its first picture (no jump back), and omxplayer starts in front of the frame VLC holds
- [ ] Test rewind again on the Pi: is the first frame shown for omxplayer loops? Does VLC play on straight away? No black between the held frame and omxplayer?
- [x] Badge on the player card: which program shows the file (VLC, omxplayer)
- [x] Changing the loop player starts the loop video playing now again with it
- [x] Rename files in the web interface (order, `-loop`)
- [x] Graphics memory setting (Video tab): `gpu_mem`, recommended 256 MB on a Pi 3, 512 MB on a Pi 4
- [x] Device info on the System tab: model, memory, graphics memory, temperature, power (under-voltage), media space, OS
- [x] Upload progress bar and multiple files at once (large videos give no feedback while uploading)
- [x] "Save and Reboot" asks once (it asked twice)
- [x] Page titles start with the player's network name, to tell players apart in a browser's tabs
- [x] The playlist fills the window and scrolls inside its card, with a scrollbar that shows on the dark background; the file playing is kept in view
- [x] The update bar says when installing an update switches back from a branch (`sudo mp4m-update --branch`); no message on top of it when an update is found
- [ ] Warn about videos the Pi can't decode in hardware, like large images: H.265/HEVC (phones and editing software often export it), VP9, AV1. A Pi 3 decodes H.264 and MPEG-4 Part 2 in hardware (an H.264 1080p25 MP4 played at 0.4 % CPU); others are decoded in software, stutter and heat the Pi. Mark them in the playlist and warn on upload, saying to export as H.264. The codec can be read with `omxplayer -i` (the player's `omx_can_play` does it for loops), or from the MP4 header without omxplayer (newer OS)

## 5. Installation

- [x] Clone to another device (System tab): copy the player to an SD card in a USB reader, with or without the media files; the card gets new partition IDs and makes its own network name. Needs exfat-utils (`install.sh`)
- [x] Copy a file from a USB stick to the player (button on the stick's rows in the playlist)
- [ ] Test on the Pi: copying a large file from a USB stick (time, the page while it copies)
- [x] Tested on a Pi 3 B+: exfat-utils installed by `install.sh` from `legacy.raspbian.org`; a card that had MP4MUSEUM on it (from a bigger card's image) was copied with the media files. The first try failed: Linux still used the card's old partitions and formatted past its end. The new partitions are now checked before anything is formatted. The second try made the card, but it didn't start: Buster's sfdisk (2.33) reads the disk ID as a signed number on the 32-bit system and quietly picked a random one for an ID of 0x80000000 or more, instead of the one in cmdline.txt and fstab. IDs are now below that, read back from the card, and written into the table directly if needed
- [x] Tested on a Pi 3 B+ (after #14): the copied card started in another Pi with a network name of its own (mp4museum-c124, from its serial number), shown on the logo screen
- [x] Tested on a Pi 3 B+ (after #15): the media size shown is the files' (6 MB, not the partition's 19 MB), and the clone's media partition has the files without the Mac folders (.Spotlight-V100, .fseventsd)
- [x] The clone booted in another Pi and plays the files; cloned while the player played a loop video (from its own media)
- [ ] Test on the Pi: a changed password and settings on the copied card; copying without the media files; the player playing a file from the card being erased (a used card's media plugged in)
- [x] Cloning, updates, uploads, copies from USB sticks and reboots share one lock (`system.try_busy_lock`), so none cuts another off half way
- [x] usbmount is put back if the web interface stops while making a card (`clone.recover`)

- [ ] Make an image file (`.img`) of a player, to write onto cards with Raspberry Pi Imager or balenaEtcher on a computer, or to keep as a backup or a release. Small: only the space used (like Clone to another device, the system partition sized to what it uses and a small media partition that grows to fill the card on first start, e.g. `parted resizepart` + exFAT grown or remade at boot). Options: from the web interface onto a USB stick (`clone.py` already lays out the partitions and changes the disk ID; it would write to a loop device on a file instead of a card), or a script on a computer from a card (like PiShrink). Leave out `hostname.txt`, the password (or keep it, asked), DHCP leases; compress it (`.img.xz`)
- [ ] Install script to set up v7 on a fresh Raspberry Pi OS, instead of depending on the image (`install.sh` only covers the v7 image so far)

## 6. Wi-Fi

Wi-Fi isn't set up on the players: no country is set, so Raspberry Pi OS keeps Wi-Fi switched
off (rfkill), and the web interface has no Wi-Fi settings. Anything set has
to survive the RAM overlay, so it's kept on `/boot`, written with `system.writable()`.

- [ ] Find out on the Pi (Buster, overlay on): what `raspi-config` sets for the Wi-Fi country (`do_wifi_country`: `/etc/wpa_supplicant/wpa_supplicant.conf`, rfkill) and whether `/boot/wpa_supplicant.conf` is still picked up at boot with `/boot` read-only (the `raspberrypi-net-mods` service moves it)
- [ ] Wi-Fi country setting (System tab, a list of countries): unblocks Wi-Fi. On `/boot` so it survives the overlay: e.g. `cfg80211.ieee80211_regdom=GB` in `cmdline.txt` (read by the kernel at boot), and `country=GB` for `wpa_supplicant`, applied at boot. Without a country Wi-Fi stays blocked
- [ ] Connect to a Wi-Fi network from the web interface (System tab): scan (`wpa_cli scan` / `iw dev wlan0 scan`), choose a network, enter its password; saved on `/boot` and applied with `wpa_cli` without a reboot. Several saved networks, with one to forget
- [ ] Wi-Fi in the Network card: network name (SSID), signal, address; the logo screen shows the Wi-Fi address when there's no Ethernet (it shows `hostname -I` already)
- [ ] Careful when changing Wi-Fi from a browser connected over that Wi-Fi: the page loses the player. Say so before applying, and keep the old network if the new one doesn't connect within a minute
- [ ] Wi-Fi passwords on `/boot` (FAT) can be read by anyone with the card: store wpa_supplicant's hashed `psk` (`wpa_passphrase`), not the password
- [ ] Clone to another device copies `/boot`, so Wi-Fi settings go to the new card too: fine for players in the same place; say so on the page
- [ ] Turn Wi-Fi off from the web interface (players on Ethernet don't need it; less to go wrong)
- [ ] Offline setup: a Wi-Fi hotspot when no network is found, so the web interface can be reached without a router (`hostapd`/`dnsmasq` aren't on the image: needs `install.sh`, from `legacy.raspbian.org` like exfat-utils)
- [ ] Test all of it on a Pi 3 B+ (2.4 and 5 GHz) and a Pi 4

## Development

- [x] `CLAUDE.md` with the constraints and conventions for working on the code
- [x] Commit the test suite to the repository (`tests/`, run with `python -m pytest tests`)
- [x] `tests/manual/`: clone a card image with the real tools (loop devices, sfdisk, mkfs, rsync, FUSE; needs root)
- [ ] Run the tests on GitHub for every pull request: the workflow is written (`ci/tests.yml`) but not enabled, to save Actions minutes; move it to `.github/workflows/` to turn it on

## Ideas

- [ ] Update from a file (USB stick or upload) for players that are never online
- [ ] Updates that change files outside `/boot` (applied at boot, before the overlay is set up)

- [ ] Announce the web interface over Bonjour/mDNS (`_http._tcp`) so players show up in network browsers
- [ ] "Restore default config.txt" button (the old "Auto" video preset used to do this)
- [ ] OSC control over the network (show control software, Max/MSP, TouchOSC, QLab), for the basic controls:
  - e.g. `/mp4museum/play`, `/pause`, `/toggle`, `/next`, `/previous`, `/rewind`, `/play <file name or number>`
  - maybe also `/sync` (start a file on several players at once) and a status reply (what is playing, position)
  - OSC is simple UDP messages: parse them in Python rather than adding a package (players may never be online)
  - in the web service (it already sends the player its commands) or the player; port and on/off in the web interface (System); off by default, as anyone on the network could control the player
  - the player already takes next, previous, rewind and a file to play (`/tmp/mp4museum-play.json` + SIGUSR1) and pause (SIGUSR2)
