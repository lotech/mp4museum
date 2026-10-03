# mp4museum player script v7 beta - july 2025

# (c) julius schmiedel - http://mp4museum.org
# licensed under the GNU GPL v3, see LICENSE

# modified 2026 in https://github.com/lotech/mp4museum (see git history):
# waits between scans when there is nothing to play; one VLC instance for
# everything; image duration setting; now playing, pause and next for the web
# interface; sync mode back from v6; sound cards 10 and above; skips files
# that don't start playing; loop files restarted by the player

import time, vlc, os, glob, json, signal, shutil
import RPi.GPIO as GPIO
import subprocess

MEDIA_FILES = '/media/*/*.*'
BOOT_VIDEO = '/home/pi/mp4museum-boot.mp4'
LOGO = '/home/pi/mp4m-v7beta.jpg'
ALSA_FILE = '/boot/alsa.txt'
# image_duration=<seconds>, set in the web interface
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
    settings = {'image_duration': DEFAULT_IMAGE_DURATION}
    try:
        with open(SETTINGS_FILE, 'r') as f:
            for line in f:
                key, sep, value = line.partition('=')
                if key.strip() == 'image_duration' and value.strip().isdecimal():
                    settings['image_duration'] = max(1, int(value.strip()))
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
        player.pause()

def buttonNext(channel):
    inputfilter = 0
    for x in range(0,200):
        if GPIO.input(13):
            inputfilter = inputfilter + 1
        time.sleep(.001)
    if (inputfilter > 50):
        next_file()

# stop the current file; also ends a loop file
skip_requested = False
def next_file():
    global skip_requested
    skip_requested = True
    player.stop()

# the web interface sends signals for its pause and next buttons
signal.signal(signal.SIGUSR1, lambda signum, frame: next_file())
signal.signal(signal.SIGUSR2, lambda signum, frame: player.pause())

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
    player.stop()
    media.release()
    if skip_requested:
        return 'skipped'
    return 'ended' if has_played else 'failed'

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
            write_status('sync', sync_file)
            subprocess.run(["omxplayer-sync", "-u", flag, sync_file])

# *** run player ****

# start player twice to make sure it is working
# seems weird but works
vlc_play(BOOT_VIDEO)
vlc_play(BOOT_VIDEO)

# please do not remove my logo screen
vlc_play(LOGO, (':image-duration=%d' % DEFAULT_IMAGE_DURATION,))

# add event listener which reacts to GPIO signal
GPIO.add_event_detect(11, GPIO.RISING, callback = buttonPause, bouncetime = 234)
GPIO.add_event_detect(13, GPIO.RISING, callback = buttonNext, bouncetime = 1234)

# check for sync mode instructions
sync_mode()

# the loop
while(1):
    files = sorted(glob.glob(MEDIA_FILES))
    # nothing to play yet (no USB stick, empty media partition): check again shortly
    if not files:
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
            # (VLC's own input-repeat could freeze on the last frame on the Pi)
            while not skip_requested and vlc_play(file, options) == 'ended':
                pass
        else:
            vlc_play(file, options)
