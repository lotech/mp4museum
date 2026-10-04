# CLAUDE.md

Notes for working on this repository. `README.md` and `v7-beta/README.md` explain what the
player does and how to install it; this file is about changing the code safely.

## What this is

A fork of [MP4MUSEUM](https://github.com/JuliusCode/MP4MUSEUM) by Julius Schmiedel: a
Raspberry Pi media player for exhibitions. The original repository only has the version 6
scripts (the `*.py` files in the root, left unchanged). Version 7 was only released as an
image, so `v7-beta/` holds its files, copied off a running install and developed further.
Paths under `v7-beta/` mirror where each file lives on the Pi (`v7-beta/boot/...` → `/boot/...`).

Plans and ideas are tracked in `TODO.md`; keep it up to date when finishing or finding work.

## The device

- Raspberry Pi 3B (others should keep working), Raspberry Pi OS Legacy (Buster) 2023-05-03.
- **Python 3.7**, Flask 2.2.5, Werkzeug 2.2.3, python-vlc 3.0.18122, RPi.GPIO. Don't use
  newer Python features (no walrus, `str.removeprefix`, etc.); check with
  `vermin -t=3.7- --violations <files>`. Don't add Python packages: devices may never be online.
- **Offline first.** The player must work with no network at all. The web interface must not
  load anything from the internet (no CDNs, fonts or scripts from elsewhere).
- **Read-only storage:**
  - `/` is a RAM overlay (`boot=overlay`): anything written there is lost at reboot. Files
    outside `/boot` can only be changed with `install.sh` while the overlay is turned off.
  - `/boot` (FAT) and `/media/internal` (exFAT, the media partition) are mounted read-only.
    Write to them only inside `system.writable(<mount point>)`, which remounts read-write,
    takes a cross-process lock, and makes them read-only again afterwards.
  - Replace files with `system.write_file()` (temp file, fsync, rename) so a power cut leaves
    the old or the new version.
  - FAT doesn't support chmod or symlinks: `shutil.copytree`/`copy2` fail there; use
    `shutil.copyfile`.
- The player runs as user `pi`, started from `.bashrc` on tty1. The web interface runs as
  root, as the `mp4m-webservice` systemd service.

## Code layout (`v7-beta/`)

- `boot/mp4museum.py`: the player.
  - **What it plays:** one VLC instance; the media files in `/media/*/` in order of path
    (`MEDIA_TYPES`, by extension, the same list as `system.py`'s). Hidden files never: the glob
    skips names starting with `.`, which uploads and copies from USB sticks rely on (they're
    written as `.upload-*` first).
  - **Buttons:** GPIO pin 11 pause; pin 13 next (pressed once), previous (twice), back to the
    start (held).
  - **Restarts:** `.bashrc` starts it again unless it exits with 0 (Ctrl-C, SIGTERM, SIGHUP). A
    file it was playing when it died twice (`SKIP_AFTER`) is skipped until replaced, chosen in
    the web interface or the Pi restarts (`/tmp/mp4museum-skipped.json`; all are tried again
    when every file is skipped; a custom boot video falls back to the original).
  - **VLC isn't stopped when a file ends by itself** (`stop()` closes the video output: black
    frames), only on skip, failure, idle, before handing the screen to omxplayer, and after the
    logo screen when it showed the address (VLC's marquee can only be changed on a picture
    being shown; stopping drops it).
  - **Loops:** `loop.` H.264/MPEG-4 videos (checked with `omxplayer -i`) are looped by
    `omxplayer --loop` when it is installed (it holds the last frame; VLC shows a black frame
    each time it starts a file again on the Pi). omxplayer isn't developed any more nor on
    newer OS versions, so with `loop_player=vlc` or without omxplayer the player restarts them
    in VLC (VLC's `input-repeat` froze on the Pi).
  - **Talking to the web interface:**
    - `/tmp/mp4museum-status.json`: the status it writes, with position, length, `engine` (vlc,
      omxplayer or omxplayer-sync), `loop_player`/`loop_omx_ok`, and which commands it can do
      (`play_file`, `rewind`, `previous`: true);
    - `SIGUSR1` (next), `SIGUSR2` (pause);
    - `/tmp/mp4museum-play.json`: a file chosen in the web interface, or `"command": "rewind"` or
      `"previous"`, read when SIGUSR1 arrives;
    - `/boot/mp4m-player.txt`: settings (`image_duration`, `loop_player`, `boot_video_plays`,
      `show_address`);
    - `/boot/mp4m-disabled.txt`: files switched off in the web interface, one path per line.
  - **Edited players** are kept by updates. The web interface finds out what one can do by
    searching its text (`system.player_*`, `get_boot_video_plays`): `MEDIA_TYPES`,
    `mp4m-disabled.txt`, `boot_video_plays` and `show_address`, `'boot_video_plays': <n>`. Keep
    these names, or change both sides. One that doesn't write `play_file` in its status isn't
    offered choosing a file.
  - **The author's logo screen** ("please do not remove my logo screen") and his `(c)` header
    stay. The logo is `boot/mp4m-web/static/logo.jpg` (updates bring it; `/home/pi/mp4m-v7beta.jpg`
    from the v7 image if it's missing); keep crediting MP4MUSEUM and Julius Schmiedel on it.
  - Users can edit this file in the web interface, so the updater only replaces it if unedited.
- `boot/mp4m-web/`: the web interface, on `/boot` so it can be updated with the overlay on.
  `webservice.py` (Flask routes), `system.py` (everything that touches the Pi),
  `updater.py` (software updates, also the `mp4m-update` command), `clone.py` (copying the
  player to an SD card in a USB reader: new partitions and disk ID, needs exfat-utils from
  `install.sh`), `network.py` (fixed addresses and Wi-Fi), `templates/`, `static/`.
  Icons are [Lucide](https://lucide.dev) symbols in `static/icons.svg`, used with the `icon()`
  macro in `templates/_icons.html`; to add one, copy its `<symbol>` from the lucide-static
  package (same version) into the sprite. `test_every_icon_is_in_the_sprite` checks them.
- `install.sh` installs onto a v7 image (overlay off); `usr/local/bin/mp4m-update` and
  `etc/systemd/system/mp4m-webservice.service` are installed by it, and exfat-utils (from
  `legacy.raspbian.org`, where Buster's packages moved) if it isn't there.
  `etc/fstab`, `etc/usbmount/usbmount.conf`, `boot/cmdline.txt` etc. are as on the image, for
  reference: nothing installs them, so changing them reaches no device.

### Network settings (`network.py`)

- Kept in `/boot/mp4m-network.json`; without it nothing is changed. The web interface writes
  them into `/etc/dhcpcd.conf` (a marked block at the end) and `wpa_supplicant.conf` when it
  starts (`apply_at_start`, in a thread) and on a change, so they need no file outside `/boot`.
- A change from the page (`network.change`) is applied after `APPLY_DELAY` (the page saying
  where to find the player is sent first) and only saved to `/boot` by `keep()`; after
  `KEEP_SECONDS` without it, `undo()` puts the saved settings back. A reboot does the same.
  *Save for next start* saves without using it: those interfaces are listed in `/run`
  (`NEXT_START_FILE`), so a restart of the web interface (updates) doesn't use them yet.
- Without a Wi-Fi country, Wi-Fi only uses 2.4 GHz channels 1-11 (`freq_list`): the Pi 3 B's
  firmware would use every channel. Wi-Fi passwords are kept as their WPA key.

### Things that mustn't be cut off half way

`system.try_busy_lock()` (`/run/lock/mp4m-busy.lock`, `flock`, across processes):
- **exclusive** for making a card (until it's done), installing an update (web: until the web
  interface restarts; `mp4m-update`: until it exits) and a reboot (from the request on);
- **shared** for uploads and copies from USB sticks (several at once).

Whichever comes second is refused with a message. Anything new that mustn't be stopped half way
takes it. Also: `system.media_rename_lock` is held while uploads, copies and renames put a name
in place on the media partition, and the clone holds `/boot`'s mount lock while it copies that
partition block by block.

### Clone to another device (`clone.py`)

- Needs exfat-utils, the overlay on (`boot=overlay`) and `/boot` on `/dev/mmcblk0`
  (`clone.unavailable()`). Lists only USB disks no system path mounts (`/media/usbN` is fine).
- Steps: unmount the card (the player is made to move off it; anything else holding it stops
  the copy); check the card is still the one chosen (`card_identity`); wait until Linux can
  re-read its table (`blockdev --rereadpt`, then `udevadm settle`); write the new table with
  `sfdisk` (new disk ID below 0x80000000), read the ID back and write it at byte 440 if
  needed; re-read and check the partitions Linux sees in `/sys/block`; then copy `/boot` block
  by block, `mkfs.ext4` + `rsync` the system from the read-only lower partition (not the
  overlay), change the disk ID in `fstab` and `cmdline.txt`, drop `hostname.txt` and DHCP
  leases, `mkfs.exfat` + `rsync --exclude '.*'` the media.
- usbmount is turned off meanwhile; `/run/mp4m-clone.json` keeps its settings, and
  `clone.recover()` at web interface start puts them back and unmounts leftovers.
  `mp4m-update --recover` (the service's `ExecStartPre`) puts `mp4m-web` back after a power cut
  during an update.

### Learned on the Pi (Buster, util-linux 2.33.1, kernel 5.10, 32-bit)

- `sfdisk` has no `--disk-id` (it came in 2.36), and it reads `label-id` as a signed 32-bit
  number: an ID of 0x80000000 or more is quietly replaced by a random one.
- Linux keeps a disk's old partitions while anything holds it open (`blockdev --rereadpt` fails
  as busy, and `sfdisk` doesn't report that). exFAT is mounted through FUSE, whose helper can
  hold the card a moment after unmounting.
- `lsblk -J` gives numbers as strings; there's no `/sys/block/*/diskseq`; USB readers don't pass
  on an SD card's own serial.
- Images over 2048 pixels come out scrambled on a Pi 3; VLC's `input-repeat` freezes; mpv needs
  KMS. Buster's packages are on `legacy.raspbian.org`.

## Updates: keep the update path working

Devices update with the updater code **they already have installed**, so a new version must
still be installable by older updaters:

- Keep `v7-beta/boot/mp4m-web/` (with `webservice.py`, `system.py`, `updater.py`) and
  `v7-beta/boot/mp4museum.py` where they are (`APP_SOURCE`, `PLAYER_SOURCE` in `updater.py`).
- Every `.py` file in `mp4m-web` must compile on Python 3.7 (the updater refuses otherwise).
- Changes to files outside `/boot` (`.bashrc`, the service, `mp4m-update`, the boot video, the
  old logo: `updater.SYSTEM_FILES`) don't reach devices through updates; the update reports them
  so `install.sh` can be run. A new file outside `/boot` goes in both `install.sh` and
  `SYSTEM_FILES`.
- Updates come from the `master` branch of `lotech/mp4museum` by default.
- To try a branch on a Pi: `sudo mp4m-update --branch <name>`. It stays installed after a
  reboot; the update check still uses the configured branch (`/boot/mp4m-update.txt`), so the
  update bar offers going back, with a note. The web interface or `sudo mp4m-update` returns to
  `master`.

## Licence

GPL v3, as the original (`LICENSE` is unchanged from it). Keep Julius Schmiedel's copyright
notices. Every file this fork changes or adds says so at the top, with the year, e.g.
`# modified 2026 in https://github.com/lotech/mp4museum (see git history)` (GPL v3
section 5a). The web interface footer and the README credit MP4MUSEUM and link to the
original.

## Working conventions

- Pull requests go to **`lotech/mp4museum` only**, never to the original repository.
- Don't add files to `.github/workflows/`: GitHub Actions minutes are shared with other
  projects. The test workflow is kept, disabled, in `ci/tests.yml`.
- Run a code review before opening a PR. Codex also reviews PRs on GitHub; fix its findings
  (verify each one) and reply on the thread.
- User-facing text (web interface, README, messages) is plain, short English.
- Nothing replaces trying it on a Pi: say what hasn't been tested on hardware.

## Tests

```bash
pip install -r tests/requirements.txt     # Flask/Werkzeug pinned to the image's versions
python -m pytest tests
```

- `tests/conftest.py`: the `pi` fixture gives each test a simulated Pi 3 B started from its SD
  card with the overlay on (temporary `/boot` and media folders; `mount`, `reboot` etc. stubbed
  and recorded in `pi.commands`, hostname changes in `pi.hostnames`; `pi.read_only`;
  `pi.disks` is what `lsblk` lists; network interfaces are folders in `pi.root / 'net'`; what
  `network.change` leaves for later runs with `pi.run_later()`); `client` is a logged-in
  browser; `github` is a fake GitHub serving a temporary copy of this repository to the
  updater. The fixture fails if `system`, `clone` or `network` has a path constant under
  `/boot`, `/media`, `/etc`, `/proc`, `/sys`, `/run` or `/tmp` it doesn't replace: add new ones
  to it.
- `test_clone.py`: copying to an SD card, on simulated disks (partitions are files and
  folders). Its `Disks` helper behaves like Buster's `sfdisk` (no `--disk-id`; a `label-id` of
  0x80000000 or more gets a random ID), and can keep the old table (`kernel_keeps_old_table`) or
  find the card busy (`busy`).
- `tests/manual/`: `make_source.sh` and `clone_real_tools.py` clone a card image with the real
  tools (loop devices, sfdisk, mkfs, rsync, FUSE); needs root, not run by pytest.
- `test_web.py`, `test_storage.py`, `test_updater.py`, `test_network.py`: the web interface,
  read-only partitions and file writes, software updates and `mp4m-update`, network settings.
- `test_player.py` runs the real player through `player_harness.py`: fake `vlc`, `RPi.GPIO`,
  omxplayer and `hostname -I`, and a virtual clock, so minutes of playback take milliseconds.
  The harness changes the player's paths by replacing their text (`paths`): add a new path
  there. It sets `boot_video_plays=2` unless a scenario has `real_default`.
- `tests/data/v7-image-player.py` is the v7 image's player (for updater tests); one test reads
  an older player from git history, so tests need the full history (`ci/tests.yml` uses
  `fetch-depth: 0`).
- Tests must never change files in the repository (the `github` fixture works on a copy).
- Add a test with every fix, and check it fails without the fix.
