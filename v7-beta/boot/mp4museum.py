# mp4museum player script v7 beta - july 2025

# (c) julius schmiedel - http://mp4museum.org
# licensed under the GNU GPL v3, see LICENSE

# modified 2026 in https://github.com/lotech/mp4museum (see git history):
# waits between scans when there is nothing to play; one VLC instance for
# everything; image duration setting; now playing, pause and next for the web
# interface; sync mode back from v6; sound cards 10 and above; skips files
# that don't start playing; loop files restarted by the player; custom boot
# video from /boot; VLC kept open between files; omxplayer for loop videos
# when it is installed

import time, vlc, os, glob, json, signal, shutil, sys
import RPi.GPIO as GPIO
import subprocess

MEDIA_FILES = '/media/*/*.*'
BOOT_VIDEO = '/home/pi/mp4museum-boot.mp4'
# a boot video put on the boot partition (e.g. from a computer) is played instead
CUSTOM_BOOT_VIDEO = '/boot/mp4museum-boot.mp4'
LOGO = '/home/pi/mp4m-v7beta.jpg'
ALSA_FILE = '/boot/alsa.txt'
# image_duration=<seconds> and loop_player=omxplayer|vlc, set in the web interface
SETTINGS_FILE = '/boot/mp4m-player.txt'
# what is playing, for the web interface
STATUS_FILE = '/tmp/mp4museum-status.json'
DEFAULT_IMAGE_DURATION = 10
# a file that hasn't started playing after this long is skipped (broken file, stalled USB stick)
OPEN_TIMEOUT = 20

# read audio device config: the card number, "0" if not set
audiodevice = "0"
if os.path.isfile(ALSA_FILE):
    with open(ALSA_FILE, 'r') as f:
        card = f.read().strip()
    if card.isdecimal():
        audiodevice = card

def read_settings():
    settings = {'image_duration': DEFAULT_IMAGE_DURATION, 'loop_player': 'omxplayer'}
    try:
        with open(SETTINGS_FILE, 'r') as f:
            for line in f:
                key, sep, value = (part.strip() for part in line.partition('='))
                if key == 'image_duration' and value.isdecimal():
                    settings['image_duration'] = max(1, int(value))
                elif key == 'loop_player' and value in ('vlc', 'omxplayer'):
                    settings['loop_player'] = value
    except OSError:
        pass
    return settings

def write_status(state, source=None):
    try:
        with open(STATUS_FILE + '.tmp', 'w') as f:
            json.dump({'state': state, 'file': source, 'since': time.time(), 'pid': os.getpid()}, f)
        os.replace(STATUS_FILE + '.tmp', STATUS_FILE)
    except OSError:
        pass
    # (only called from the main loop, never from button or signal handlers)

# one VLC instance and player for everything
vlc_instance = vlc.Instance('-q -A alsa --alsa-audio-device hw:' + audiodevice)
player = vlc_instance.media_player_new()

# setup GPIO pin
GPIO.setmode(GPIO.BOARD)
GPIO.setup(11, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)
GPIO.setup(13, GPIO.IN, pull_up_down = GPIO.PUD_DOWN)

# functions to be called by event listener
# with code to filter interference / static discharges
def buttonPause(channel):
    inputfilter = 0
    for x in range(0,200):
        if GPIO.input(11):
            inputfilter = inputfilter + 1
        time.sleep(.001)
    if (inputfilter > 50):
        pause_toggle()

def buttonNext(channel):
    inputfilter = 0
    for x in range(0,200):
        if GPIO.input(13):
            inputfilter = inputfilter + 1
        time.sleep(.001)
    if (inputfilter > 50):
        next_file()

# omxplayer, when it is playing a loop file (see omx_loop)
omx = None
omx_paused = False
omx_started = 0
omx_last_key = 0

# stop the current file; also ends a loop file (omx_loop stops omxplayer)
skip_requested = False
def next_file():
    global skip_requested
    skip_requested = True
    if not omx:
        player.stop()

def pause_toggle():
    global omx_paused, omx_last_key
    process = omx
    if process:
        # omxplayer only reads keys once it has started, and keys that arrive together are
        # read as one unknown key, so presses too early or too close together are ignored
        now = time.time()
        if now - omx_started >= 3 and now - omx_last_key >= .3 and omx_key(process, b'p'):
            omx_last_key = now
            omx_paused = not omx_paused
    else:
        player.pause()

# the web interface sends signals for its pause and next buttons
signal.signal(signal.SIGUSR1, lambda signum, frame: next_file())
signal.signal(signal.SIGUSR2, lambda signum, frame: pause_toggle())

# play media with vlc and wait until it has finished
# returns 'ended', 'skipped' (next was pressed) or 'failed' (it didn't play)
def vlc_play(source, options=()):
    media = vlc_instance.media_new(source, *options)
    player.set_media(media)
    player.play()
    started = time.time()
    shown_state = 'playing'
    write_status(shown_state, source)
    time.sleep(1)
    current_state = player.get_state()
    has_played = False
    while current_state in (vlc.State.Opening, vlc.State.Buffering, vlc.State.Playing, vlc.State.Paused):
        if current_state in (vlc.State.Playing, vlc.State.Paused):
            has_played = True
        elif not has_played and time.time() - started > OPEN_TIMEOUT:
            print("skipping %s: it didn't start playing" % source, flush=True)
            break
        state = 'paused' if current_state == vlc.State.Paused else 'playing'
        if state != shown_state:
            shown_state = state
            write_status(state, source)
        time.sleep(.01)
        current_state = player.get_state()
    # a very short file can be over before the first check
    if current_state == vlc.State.Ended:
        has_played = True
    else:
        # skipped, failed or stuck. A file that ended by itself is not stopped: stopping
        # closes VLC's video output (black frames), so the next file starts in the same
        # window and the last frame stays on screen until it does
        player.stop()
    media.release()
    if skip_requested:
        return 'skipped'
    return 'ended' if has_played else 'failed'

# loop video files with omxplayer (unless the setting is loop_player=vlc): it loops inside
# the player and holds the last frame, where VLC shows a black frame each time it starts the
# file again on the Pi. omxplayer is no longer developed (it doesn't work on newer Raspberry
# Pi OS), but it is on the v7 image; VLC is used if it isn't installed or can't play the file
OMX_LOOP_TYPES = ('.mp4', '.m4v', '.mov', '.mkv', '.avi', '.ts', '.h264')

def omx_key(process, key):
    """Send omxplayer one of its keyboard controls (p pauses). True if it was sent.
    Unbuffered: it is called from button and signal handlers."""
    try:
        return os.write(process.stdin.fileno(), key) == len(key)
    except (OSError, ValueError):
        return False

def omx_running(process):
    """True while anything of omxplayer runs: /usr/bin/omxplayer is a script that starts
    omxplayer.bin, both in the process group started for it."""
    if process.poll() is None:
        return True
    try:
        os.killpg(process.pid, 0)
        return True
    except OSError:
        return False

def stop_omx(process):
    """Interrupt omxplayer (its clean shutdown); if it doesn't stop, terminate and finally
    kill it, so it can never block the player or stay on screen."""
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        if not omx_running(process):
            return
        try:
            os.killpg(process.pid, signum)
        except OSError:
            pass
        waited = 0
        while waited < 2 and omx_running(process):
            time.sleep(.05)
            waited += .05

def omx_loop(source):
    """Loop a video with omxplayer until next is pressed.
    Returns 'skipped', or 'failed' if omxplayer couldn't play it (then VLC takes over)."""
    global omx, omx_paused, omx_started
    # VLC lets go of the screen; omxplayer blanks the background behind the video (-b)
    player.stop()
    while not skip_requested:
        omx_paused = False
        started = time.time()
        try:
            process = subprocess.Popen(['omxplayer', '--loop', '--no-osd', '-b',
                                        '-o', 'alsa:plughw:%s,0' % audiodevice, source],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return 'failed'
        omx_started = started
        omx = process
        shown_state = None
        while process.poll() is None and not skip_requested:
            state = 'paused' if omx_paused else 'playing'
            if state != shown_state:
                shown_state = state
                write_status(state, source)
            time.sleep(.05)
        stop_omx(process)
        omx = None
        if skip_requested:
            break
        # with --loop it only stops by itself if something went wrong
        print("omxplayer stopped playing %s (exit code %s)" % (source, process.returncode), flush=True)
        if time.time() - started < 5:
            return 'failed'
    return 'skipped'

# find a file, and if found, return its path (for sync)
def search_file(file_name):
    matching_files = glob.glob(f'/media/*/{file_name}') + glob.glob(f'/boot/{file_name}')
    return matching_files[0] if matching_files else False

# sync mode (from v6): several players play sync.mp4 in sync with omxplayer-sync
def sync_mode():
    sync_file = search_file("sync.mp4")
    for role, flag in (("leader", "-m"), ("player", "-l")):
        if sync_file and search_file(f"sync-{role}.txt"):
            if not shutil.which("omxplayer-sync"):
                print("sync mode needs omxplayer-sync, which is not installed", flush=True)
                return
            intro = f"/home/pi/sync-{role}.mp4"
            if os.path.isfile(intro):
                vlc_play(intro)
            # VLC lets go of the screen for omxplayer
            player.stop()
            write_status('sync', sync_file)
            subprocess.run(["omxplayer-sync", "-u", flag, sync_file])

# *** run player ****

boot_video = CUSTOM_BOOT_VIDEO if os.path.isfile(CUSTOM_BOOT_VIDEO) else BOOT_VIDEO

# start player twice to make sure it is working
# seems weird but works
vlc_play(boot_video)
vlc_play(boot_video)

# please do not remove my logo screen
vlc_play(LOGO, (':image-duration=%d' % DEFAULT_IMAGE_DURATION,))

# add event listener which reacts to GPIO signal
GPIO.add_event_detect(11, GPIO.RISING, callback = buttonPause, bouncetime = 234)
GPIO.add_event_detect(13, GPIO.RISING, callback = buttonNext, bouncetime = 1234)

# check for sync mode instructions
sync_mode()

# stop omxplayer if the player is stopped, so it isn't left looping on screen
def quit_player(signum, frame):
    sys.exit(0)
signal.signal(signal.SIGTERM, quit_player)
signal.signal(signal.SIGHUP, quit_player)

# the loop
try:
    while(1):
        files = sorted(glob.glob(MEDIA_FILES))
        # nothing to play yet (no USB stick, empty media partition): check again shortly
        if not files:
            # don't leave the last frame of a deleted file on screen
            player.stop()
            write_status('idle')
            time.sleep(2)
        for file in files:
            # read for every file, so a new image duration applies straight away
            settings = read_settings()
            options = [':image-duration=%d' % settings['image_duration']]
            # a next press from here on skips this file
            skip_requested = False
            if "loop." in file:
                # play it again and again until next is pressed
                if (settings['loop_player'] == 'omxplayer' and file.lower().endswith(OMX_LOOP_TYPES)
                        and shutil.which("omxplayer")):
                    if omx_loop(file) != 'failed':
                        continue
                    print("falling back to VLC for %s" % file, flush=True)
                # VLC starts it again in the same window each time it ends
                while not skip_requested and vlc_play(file, options) == 'ended':
                    pass
            else:
                vlc_play(file, options)
finally:
    if omx:
        stop_omx(omx)
