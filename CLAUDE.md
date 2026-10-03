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

- `boot/mp4museum.py`: the player. One VLC instance; plays `/media/*/*.*` in order; GPIO
  pin 11 pause, pin 13 next. `.bashrc` starts it again unless it exits with 0 (Ctrl-C, SIGTERM,
  SIGHUP); a file it was playing when it died is skipped until replaced (`/tmp/mp4museum-skipped.json`),
  so one bad file can't stop it again and again. VLC is not stopped when a file ends by itself (`stop()` closes
  the video output: black frames), only on skip, failure, idle, before handing the screen to
  omxplayer, and after the logo screen when it showed the address (VLC's marquee can only be
  changed on a picture being shown; stopping drops it). `loop.` H.264/MPEG-4 videos (checked with `omxplayer -i`) are looped by `omxplayer --loop` when it is installed (it
  holds the last frame; VLC still shows a black frame each time it starts a file again on
  the Pi). omxplayer is no longer developed and isn't on newer OS versions, so with
  `loop_player=vlc` or without omxplayer, the player restarts them in VLC (VLC's
  `input-repeat` froze on the Pi). Talks to the web interface through
  `/tmp/mp4museum-status.json` (status it writes, with position, length and `engine`: vlc or omxplayer), `SIGUSR1` (next),
  `SIGUSR2` (pause), `/tmp/mp4museum-play.json` (a file chosen in the web interface, or
  `"command": "rewind"`, read when SIGUSR1 arrives) and `/boot/mp4m-player.txt` (settings: `image_duration`,
  `loop_player`, `boot_video_plays`, `show_address`). Edited players from before may not
  write `play_file` in their status; the web interface then doesn't offer choosing a file.
  Keep the author's logo screen ("please do not remove my logo screen") and his `(c)` header.
  The logo is `boot/mp4m-web/static/logo.jpg` (updates bring it; `/home/pi/mp4m-v7beta.jpg`
  from the v7 image if it's missing); keep crediting MP4MUSEUM and Julius Schmiedel on it.
  Users can edit this file in the web interface, so the updater only replaces it if unedited.
- `boot/mp4m-web/`: the web interface, on `/boot` so it can be updated with the overlay on.
  `webservice.py` (Flask routes), `system.py` (everything that touches the Pi),
  `updater.py` (software updates, also the `mp4m-update` command), `templates/`, `static/`.
  Icons are [Lucide](https://lucide.dev) symbols in `static/icons.svg`, used with the `icon()`
  macro in `templates/_icons.html`; to add one, copy its `<symbol>` from the lucide-static
  package (same version) into the sprite. `test_every_icon_is_in_the_sprite` checks them.
- `install.sh` installs onto a v7 image (overlay off); `usr/local/bin/mp4m-update` and
  `etc/systemd/system/mp4m-webservice.service` are installed by it.

## Updates: keep the update path working

Devices update with the updater code **they already have installed**, so a new version must
still be installable by older updaters:

- Keep `v7-beta/boot/mp4m-web/` (with `webservice.py`, `system.py`, `updater.py`) and
  `v7-beta/boot/mp4museum.py` where they are (`APP_SOURCE`, `PLAYER_SOURCE` in `updater.py`).
- Every `.py` file in `mp4m-web` must compile on Python 3.7 (the updater refuses otherwise).
- Changes to files outside `/boot` (`.bashrc`, the service, `mp4m-update`, the boot video)
  don't reach devices through updates; the update reports them so `install.sh` can be run.
- Updates come from the `master` branch of `lotech/mp4museum` by default.

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

- `tests/conftest.py`: the `pi` fixture gives each test a simulated Pi (temporary `/boot`
  and media folders; `mount`, `reboot` and hostname changes stubbed and recorded in
  `pi.commands`); `client` is a logged-in browser; `github` is a fake GitHub serving a
  temporary copy of this repository to the updater.
- `test_web.py`, `test_storage.py`, `test_updater.py`: the web interface, read-only
  partitions and file writes, software updates and `mp4m-update`.
- `test_player.py` runs the real player through `player_harness.py`: fake `vlc` and
  `RPi.GPIO` modules and a virtual clock, so minutes of playback take milliseconds.
- Tests must never change files in the repository (the `github` fixture works on a copy).
- Add a test with every fix, and check it fails without the fix.
