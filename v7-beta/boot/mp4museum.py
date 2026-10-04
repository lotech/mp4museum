# mp4museum player script v7 beta - july 2025

# (c) julius schmiedel - http://mp4museum.org
# licensed under the GNU GPL v3, see LICENSE

# modified 2026 in https://github.com/lotech/mp4museum (see git history):
# waits between scans when there is nothing to play; one VLC instance for
# everything; image duration setting; now playing, pause and next for the web
# interface; sync mode back from v6; sound cards 10 and above; skips files
# that don't start playing; loop files restarted by the player; custom boot
# video from /boot; VLC kept open between files; omxplayer for loop videos
# when it is installed; position for the web interface, which can also choose
# the file to play; exit code 0 only when stopped on purpose (.bashrc restarts it);
# how often the boot video plays and the player's address on the logo screen (settings);
# previous file; the button on pin 13 pressed once, twice or held; files switched off

import signal, sys
# stopped on purpose (Ctrl-C on the console, SIGTERM, SIGHUP): exit code 0, so .bashrc doesn't
# start it again. Set first, as the imports below take a few seconds; replaced further down.
for quit_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(quit_signal, lambda signum, frame: sys.exit(0))

import time, vlc, os, glob, json, shutil, re, struct, hashlib, threading
import RPi.GPIO as GPIO
import subprocess

MEDIA_FILES = '/media/*/*.*'
# only these are played: a USB stick or SD card can have other files on it (an SD card made
# for a Pi has its boot files). Same list as the web interface's
MEDIA_TYPES = ('.mp4', '.m4v', '.mov', '.mkv', '.avi', '.ts', '.mts', '.m2ts', '.h264', '.mpg', '.mpeg',
               '.m2v', '.vob', '.webm', '.wmv', '.flv', '.ogv', '.3gp',
               '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff',
               '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.aif', '.aiff')
BOOT_VIDEO = '/home/pi/mp4museum-boot.mp4'
# a boot video put on the boot partition (e.g. from a computer) is played instead
CUSTOM_BOOT_VIDEO = '/boot/mp4museum-boot.mp4'
# the logo screen: the one in the web interface's folder (updates bring it), else the one
# install.sh put in /home/pi
LOGO = '/boot/mp4m-web/static/logo.jpg'
OLD_LOGO = '/home/pi/mp4m-v7beta.jpg'
ALSA_FILE = '/boot/alsa.txt'
# image_duration=<seconds>, loop_player=omxplayer|vlc, boot_video_plays=0|1|2 and
# show_address=yes|no, set in the web interface
SETTINGS_FILE = '/boot/mp4m-player.txt'
# the network name set in the web interface (else the web interface sets a default one)
HOSTNAME_FILE = '/boot/hostname.txt'
# files switched off in the web interface, one path per line: left out of the playlist
DISABLED_FILE = '/boot/mp4m-disabled.txt'
# what is playing, for the web interface
STATUS_FILE = '/tmp/mp4museum-status.json'
# from the web interface, followed by SIGUSR1: {"id": ..., "file": ...} plays that file;
# {"id": ..., "command": "rewind"} goes back to the first frame and holds it until play is pressed;
# {"id": ..., "command": "previous"} plays the file before (the player knows which it skips)
PLAY_REQUEST_FILE = '/tmp/mp4museum-play.json'
# files that were playing when the player stopped by itself: [[path, [size, mtime]], ...]
SKIPPED_FILE = '/tmp/mp4museum-skipped.json'
DEFAULT_IMAGE_DURATION = 10
# a file that hasn't started playing after this long is skipped (broken file, stalled USB stick)
OPEN_TIMEOUT = 20
# an image still showing this long after its time is over is moved on from (a very large image
# can take VLC a long time on a Pi, and come out scrambled)
IMAGE_GRACE = 20
# VLC sends a still image to the screen 10 times a second, converting its pixel format each
# time: a whole CPU core on a Pi 3, and the Pi hot. Converted once, to the format the Pi's video
# output takes, it costs almost nothing. (Sending it less often, image-fps, made a paused image
# move on anyway.)
IMAGE_OPTIONS = (':image-chroma=I420',)
IMAGE_TYPES = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff')
# the largest image (pixels on each side) a Pi 3 or older can show: bigger ones come out
# scrambled (seen on a Pi 3B with 3300 x 2550) and are skipped. Not known for a Pi 4 or 5.
OLD_PI_IMAGE_LIMIT = 2048

# read audio device config: the card number, "0" if not set
audiodevice = "0"
if os.path.isfile(ALSA_FILE):
    with open(ALSA_FILE, 'r') as f:
        card = f.read().strip()
    if card.isdecimal():
        audiodevice = card

def read_settings():
    settings = {'image_duration': DEFAULT_IMAGE_DURATION, 'loop_player': 'omxplayer',
                'boot_video_plays': 1, 'show_address': True}
    try:
        with open(SETTINGS_FILE, 'r') as f:
            for line in f:
                key, sep, value = (part.strip() for part in line.partition('='))
                if key == 'image_duration' and value.isdecimal():
                    settings['image_duration'] = max(1, int(value))
                elif key == 'loop_player' and value in ('vlc', 'omxplayer'):
                    settings['loop_player'] = value
                elif key == 'boot_video_plays' and value in ('0', '1', '2'):
                    settings['boot_video_plays'] = int(value)
                elif key == 'show_address' and value in ('yes', 'no'):
                    settings['show_address'] = value == 'yes'
    except OSError:
        pass
    return settings

# the program looping the loop file playing now ('vlc', 'omxplayer'), else None: while an
# omxplayer loop's first frame is held, VLC shows it (engine) but the loop is omxplayer's
loop_engine = None
# whether omxplayer could loop it (installed, file type, codec), so the web interface knows
# if choosing omxplayer would change anything
loop_omx_ok = None

def write_status(state, source=None, position=None, length=None, temp='.tmp', engine='vlc'):
    # position and length in seconds, when known; play_file: this player reads PLAY_REQUEST_FILE.
    # rewind: it can go back to the first frame (False until the playlist starts)
    # temp: the quit signal handler uses its own temp file, as it can interrupt this one
    try:
        with open(STATUS_FILE + temp, 'w') as f:
            # mono: the clock the web interface uses for how long since, which doesn't jump when
            # the Pi sets its time from the network (it has no clock of its own)
            json.dump({'state': state, 'file': source, 'since': time.time(), 'mono': time.monotonic(),
                       'pid': os.getpid(), 'position': position, 'length': length, 'play_file': True,
                       'rewind': playlist_started, 'previous': playlist_started, 'engine': engine, 'loop_player': loop_engine,
                       'loop_omx_ok': loop_omx_ok}, f)
        os.replace(STATUS_FILE + temp, STATUS_FILE)
    except OSError:
        pass
    # (called from the main loop, and once from the quit signal handler)

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

# the button on pin 13 (next in the original): pressed once, next file (or play, when paused or
# held at the first frame); twice, the previous file; held down, back to the start of the file,
# held there until it is pressed again
BUTTON_TWICE = .4       # pressed again within this long of letting go: twice
BUTTON_HELD = 1         # down this long: held
BUTTON_STEADY = .03     # a press or a release counts once it has lasted this long, so switch
                        # bounce and interference / static discharges aren't taken for presses
# what the button asked for ('press', 'twice' or 'held', and files_started when the press
# began), done by the SIGUSR1 handler: on the main thread, like the web interface's buttons, so
# it doesn't change what the loop is in the middle of
button_pressed = None
# how many playlist files the loop has started: a press is only known for what it is up to 1 s
# later, when the file it was meant for may have ended by itself and the next one started
files_started = 0
button_reader = None

def pin_stays(pin, level, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if GPIO.input(pin) != level:
            return False
        time.sleep(.005)
    return True

def wait_for(pin, level, seconds):
    """Wait up to seconds for the pin to be steadily at level (1: pressed); True if it was."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if GPIO.input(pin) == level and pin_stays(pin, level, BUTTON_STEADY):
            return True
        time.sleep(.005)
    return False

def read_and_send(started):
    """Read the press now starting and send what it is, as soon as that's known: held at 1 s
    (not when let go), twice at the second press, once when no second one has come. started:
    the playlist's file count when it began (see next_signal)."""
    global button_pressed
    if not wait_for(13, 1, .1):
        return
    if not wait_for(13, 0, BUTTON_HELD - BUTTON_STEADY):
        pressed = 'held'
    elif wait_for(13, 1, BUTTON_TWICE):
        pressed = 'twice'
    else:
        pressed = 'press'
    button_pressed = (pressed, started)
    os.kill(os.getpid(), signal.SIGUSR1)
    if pressed != 'press':
        # let go first (while this thread is busy, new presses aren't read), or the rest of a
        # hold would be read as another press
        wait_for(13, 0, 10)

def buttonNext(channel):
    # read on a thread of its own, so the pause button (RPi.GPIO calls them one at a time) isn't
    # kept waiting. The edges of a press being read (switch bounce, the second of two) are left
    # to it; any after it read as not pressed
    global button_reader
    if button_reader and button_reader.is_alive():
        return
    button_reader = threading.Thread(target=read_and_send, args=(files_started,), daemon=True)
    button_reader.start()

# omxplayer, when it is playing a loop file (see omx_loop)
omx = None
omx_paused = False
omx_started = 0
omx_last_key = 0

def read_play_request():
    try:
        with open(PLAY_REQUEST_FILE, 'r') as f:
            request = json.load(f)
        return request['id'], request
    except (OSError, ValueError, KeyError, TypeError):
        return None, None

# a request left from before the player started is not played
handled_play_request = read_play_request()[0]

def play_request_waiting():
    request_id = read_play_request()[0]
    return request_id is not None and request_id != handled_play_request

def play_request():
    """The request from the web interface since the last time, or None."""
    global handled_play_request
    request_id, request = read_play_request()
    if request_id is None or request_id == handled_play_request:
        return None
    handled_play_request = request_id
    return request

# back to the first frame, paused, until play is pressed. The file is started again rather than
# VLC jumping back: on the Pi it was slow to play on after a jump back, with a black frame.
rewind_requested = False
# play pressed while a rewind is on its way: it plays on as soon as the first frame is shown
play_requested = False
# rewind is for the playlist, not the boot video and logo
playlist_started = False

def take_play_request():
    global play_requested
    requested, play_requested = play_requested, False
    return requested
def rewind():
    global rewind_requested
    if skip_requested or not playlist_started:
        # next was pressed first: the file is on its way out
        return
    # vlc_play and omx_loop start the file again; between files, the next file waits at its
    # first frame. omxplayer can't hold a frame: omx_loop shows it in VLC.
    rewind_requested = True

# stop the current file; also ends a loop file (omx_loop stops omxplayer). Read here, with
# the signal the web interface sends after it, so the file chosen there plays next
skip_requested = False
requested_file = None
# previous pressed this many times since the loop last chose a file (two quick presses: two back)
previous_requested = 0
def next_signal():
    """SIGUSR1: from the web interface (next, or its request), or the button on pin 13."""
    global button_pressed
    pressed, button_pressed = button_pressed, None
    if pressed is None or play_request_waiting():
        next_file()
    if pressed is None:
        return
    pressed, started = pressed
    # files the playlist has moved on by itself since the press began: it is meant for the file
    # playing then
    moved_on = files_started - started
    if pressed == 'twice':
        for _ in range(1 + moved_on):
            previous_file()
    elif pressed == 'held':
        if moved_on:
            for _ in range(moved_on):
                previous_file()
        else:
            rewind()
    elif pressed == 'press' and not moved_on:
        # (moved on: that was the next file already)
        if is_paused():
            pause_toggle()
        else:
            skip()

def next_file():
    global requested_file, previous_requested
    request = play_request()
    if request and request.get('command') == 'rewind':
        rewind()
        return
    if request and request.get('command') == 'previous':
        previous_file()
        return
    if request and isinstance(request.get('file'), str):
        requested_file = request['file']
        previous_requested = 0
    skip()

def previous_file():
    global requested_file, previous_requested
    if not playlist_started:
        # the boot video and logo: there's nothing before them
        return
    previous_requested += 1
    requested_file = None
    skip()

def skip():
    global skip_requested, rewind_requested
    skip_requested = True
    # next overtakes a rewind that is still waiting
    rewind_requested = False
    take_play_request()
    if not omx:
        player.stop()

def is_paused():
    if rewind_requested:
        # held at the first frame (on its way), unless play has been pressed already
        return not play_requested
    if omx:
        return omx_paused
    return player.get_state() == vlc.State.Paused

def pause_toggle():
    global omx_paused, omx_last_key, play_requested
    if rewind_requested:
        # for the first frame on its way (not the omxplayer being stopped)
        play_requested = not play_requested
        return
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
signal.signal(signal.SIGUSR1, lambda signum, frame: next_signal())
signal.signal(signal.SIGUSR2, lambda signum, frame: pause_toggle())

# stopped on purpose: as at the top, and omxplayer is stopped too (see the end) so it isn't left
# looping on screen
def quit_player(signum, frame):
    # so the file playing now isn't taken for the one that stopped the player
    write_status('stopped', temp='.stop.tmp')
    sys.exit(0)
for quit_signal in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(quit_signal, quit_player)

def pause_at_first_picture(started):
    """Pause as soon as VLC shows the first picture (a moment after it says it plays).
    Returns 'held', 'playing' (play was pressed meanwhile) or None if it didn't get that far."""
    global rewind_requested
    playing_since = None
    while not skip_requested and time.time() - started < OPEN_TIMEOUT:
        state = player.get_state()
        if state in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped):
            break
        if state == vlc.State.Playing:
            playing_since = playing_since or time.time()
            if player.get_time() > 0 or time.time() - playing_since > .5:
                break
        time.sleep(.01)
    showing = player.get_state() == vlc.State.Playing
    rewind_requested = False
    # a play press waiting for this picture is used up either way
    play_pressed = take_play_request()
    if not showing:
        return None
    if play_pressed:
        return 'playing'
    player.set_pause(1)
    return 'held'

# play media with vlc and wait until it has finished
# returns 'ended', 'skipped' (next was pressed), 'rewind' (to be started again, held at its
# first frame) or 'failed' (it didn't play)
def vlc_play(source, options=(), limit=None, while_playing=None):
    """limit: seconds it may play (not counting pauses) before it is stopped. while_playing:
    called with the time and length (ms) every time it is checked, while it plays."""
    media = vlc_instance.media_new(source, *options)
    player.set_media(media)
    player.play()
    started = time.time()
    shown_state = 'playing'
    shown_length = 0
    write_status(shown_state, source, 0)
    if rewind_requested:
        # rewind: held at the first frame until play is pressed
        pause_at_first_picture(started)
    else:
        time.sleep(1)
    current_state = player.get_state()
    has_played = False
    rewinding = False
    unpaused, checked = 1, time.time()
    while current_state in (vlc.State.Opening, vlc.State.Buffering, vlc.State.Playing, vlc.State.Paused):
        if skip_requested:
            # next was pressed while this file was being started
            break
        now = time.time()
        if current_state != vlc.State.Paused:
            unpaused += now - checked
        checked = now
        if limit and unpaused > limit:
            print("moving on from %s: still showing after %d seconds" % (source, limit), flush=True)
            has_played = True
            break
        if current_state in (vlc.State.Playing, vlc.State.Paused):
            has_played = True
        elif not has_played and time.time() - started > OPEN_TIMEOUT:
            print("skipping %s: it didn't start playing" % source, flush=True)
            break
        if rewind_requested and current_state in (vlc.State.Playing, vlc.State.Paused):
            # started again by the caller, without stopping (that would show black frames)
            rewinding = True
            break
        state = 'paused' if current_state == vlc.State.Paused else 'playing'
        length = player.get_length()
        if while_playing:
            while_playing(player.get_time(), length)
        if state != shown_state or (length > 0 and length != shown_length):
            shown_state, shown_length = state, length
            write_status(state, source, max(0, player.get_time()) / 1000, length / 1000 if length > 0 else None)
        time.sleep(.01)
        current_state = player.get_state()
    # a very short file can be over before the first check
    if current_state == vlc.State.Ended:
        has_played = True
    elif not rewinding:
        # skipped, failed or stuck. A file that ended by itself is not stopped: stopping
        # closes VLC's video output (black frames), so the next file starts in the same
        # window and the last frame stays on screen until it does
        player.stop()
    media.release()
    if skip_requested:
        return 'skipped'
    if rewinding:
        return 'rewind'
    return 'ended' if has_played else 'failed'

# loop video files with omxplayer (unless the setting is loop_player=vlc): it loops inside
# the player and holds the last frame, where VLC shows a black frame each time it starts the
# file again on the Pi. omxplayer is no longer developed (it doesn't work on newer Raspberry
# Pi OS), but it is on the v7 image; VLC is used if it isn't installed or can't play the file
OMX_LOOP_TYPES = ('.mp4', '.m4v', '.mov', '.mkv', '.avi', '.ts', '.h264')
# seconds VLC keeps showing a held frame while omxplayer starts (it takes about a second)
HANDOVER = 1.5

def omx_can_play(source):
    """True if the video is H.264 or MPEG-4, which omxplayer decodes on every Pi. With other
    codecs (HEVC, or MPEG-2 without a licence key) it can keep running with a black picture."""
    try:
        info = subprocess.run(['omxplayer', '-i', source], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, timeout=15, universal_newlines=True, errors='replace').stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return re.search(r'Video: (h264|mpeg4)\b', info) is not None

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
    global omx, omx_paused, omx_started, rewind_requested
    # VLC lets go of the screen; omxplayer blanks the background behind the video (-b)
    player.stop()
    while not skip_requested:
        held = None
        if rewind_requested:
            held = hold_first_frame(source)
        def let_go():
            # VLC stops showing the held frame
            player.stop()
            held.release()
        if skip_requested:
            if held:
                let_go()
            break
        omx_paused = False
        started = time.time()
        # after a held frame, no black background (-b): VLC shows the frame until omxplayer's
        # picture is up (the console behind is black, see .bashrc)
        try:
            process = subprocess.Popen(['omxplayer', '--loop', '--no-osd'] + ([] if held else ['-b']) +
                                       ['-o', 'alsa:plughw:%s,0' % audiodevice, source],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            if held:
                let_go()
            return 'failed'
        omx_started = started
        omx = process
        shown_state = None
        while process.poll() is None and not skip_requested and not rewind_requested:
            if held and time.time() - started > HANDOVER:
                let_go()
                held = None
            state = 'paused' if omx_paused else 'playing'
            if state != shown_state:
                shown_state = state
                write_status(state, source, engine='omxplayer')
            time.sleep(.05)
        if held:
            let_go()
        stop_omx(process)
        omx = None
        if skip_requested:
            break
        if rewind_requested:
            # the first frame (at the top), then omxplayer plays it from the start
            continue
        # with --loop it only stops by itself if something went wrong
        print("omxplayer stopped playing %s (exit code %s)" % (source, process.returncode), flush=True)
        if time.time() - started < 5:
            return 'failed'
    return 'skipped'

# A file that was playing when the player stopped by itself twice (e.g. an image too big for
# the memory) is skipped until it is replaced or chosen in the web interface, so it can't stop
# the player again each time round. Once could be chance. /tmp is emptied at boot, so a reboot
# tries everything again. SKIPPED_FILE: [[path, [size, mtime], times it stopped the player], ...]
def file_version(path):
    try:
        info = os.stat(path)
        return [info.st_size, int(info.st_mtime)]
    except OSError:
        return None

SKIP_AFTER = 2

def read_skipped():
    try:
        with open(SKIPPED_FILE, 'r') as f:
            return {path: [version, int(count)] for path, version, count in json.load(f)}
    except (OSError, ValueError, TypeError):
        return {}

def save_skipped():
    try:
        with open(SKIPPED_FILE + '.tmp', 'w') as f:
            json.dump([[path, version, count] for path, (version, count) in skipped.items()], f)
        os.replace(SKIPPED_FILE + '.tmp', SKIPPED_FILE)
    except OSError:
        pass

def forgive(path):
    """It played to the end: whatever stopped the player before, it wasn't (only) this file."""
    if path in skipped:
        del skipped[path]
        save_skipped()

# messages printed every time round would fill the log, which is in memory
reported = set()
def report_once(key, message):
    if key not in reported:
        reported.add(key)
        print(message, flush=True)

def is_player(pid):
    """True if pid is a running player (this script)."""
    try:
        with open('/proc/%d/cmdline' % int(pid), 'rb') as f:
            return any(argument.endswith(b'mp4museum.py') for argument in f.read().split(b'\0'))
    except (OSError, ValueError, TypeError):
        return False

skipped = read_skipped()
try:
    with open(STATUS_FILE, 'r') as f:
        last_status = json.load(f)
    last_file, last_pid = last_status.get('file'), last_status.get('pid')
    if (last_status.get('state') in ('playing', 'paused') and last_file and last_pid != os.getpid()
            and not is_player(last_pid)):
        version = file_version(last_file)
        old_version, count = skipped.get(last_file, (None, 0))
        count = count + 1 if old_version == version else 1
        skipped[last_file] = [version, count]
        save_skipped()
        print("%s was playing when the player stopped (%d time%s)%s" % (
            last_file, count, '' if count == 1 else 's',
            ': it is skipped until it is replaced' if count >= SKIP_AFTER else ''), flush=True)
except (OSError, ValueError, TypeError, AttributeError):
    pass

# omxplayer runs on its own: one left from a player that was killed would stay on top of the screen
for process in os.listdir('/proc'):
    try:
        if process.isdigit():
            with open('/proc/%s/comm' % process, 'r') as f:
                if f.read().strip() == 'omxplayer.bin':
                    os.kill(int(process), signal.SIGKILL)
    except (OSError, ValueError):
        pass

def find_image_limit():
    try:
        with open('/proc/device-tree/model', 'r', errors='replace') as f:
            model = f.read()
    except OSError:
        return None
    if any(newer in model for newer in ('Pi 4', 'Pi 5', 'Pi 400', 'Pi 500', 'Compute Module 4', 'Compute Module 5')):
        return None
    return OLD_PI_IMAGE_LIMIT if 'Raspberry Pi' in model else None

image_limit = find_image_limit()

def image_size(path):
    """(width, height) of a PNG, JPEG, GIF, BMP or WebP from its header, or None."""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                return struct.unpack('>II', head[16:24])
            if head[:6] in (b'GIF87a', b'GIF89a'):
                return struct.unpack('<HH', head[6:10])
            if head[:2] == b'BM':
                width, height = struct.unpack('<ii', head[18:26])
                return abs(width), abs(height)
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                if head[12:16] == b'VP8X':
                    return int.from_bytes(head[24:27], 'little') + 1, int.from_bytes(head[27:30], 'little') + 1
                if head[12:16] == b'VP8 ':
                    width, height = struct.unpack('<HH', head[26:30])
                    return width & 0x3fff, height & 0x3fff
                if head[12:16] == b'VP8L':
                    bits = int.from_bytes(head[21:25], 'little')
                    return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
            if head[:2] == b'\xff\xd8':
                f.seek(2)
                for _ in range(10000):
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xff:
                        return None
                    if marker[1] == 0xff:
                        # fill byte before a marker
                        f.seek(-1, 1)
                    elif 0xc0 <= marker[1] <= 0xcf and marker[1] not in (0xc4, 0xc8, 0xcc):
                        height, width = struct.unpack('>xxxHH', f.read(7))
                        return width, height
                    elif not (marker[1] == 0x01 or 0xd0 <= marker[1] <= 0xd8):
                        f.seek(struct.unpack('>H', f.read(2))[0] - 2, 1)
    except (OSError, struct.error):
        pass
    return None

def hold_first_frame(source):
    """Show a video's first frame in VLC, paused, until play or next is pressed (for omxplayer,
    which can't hold a frame). Returns the media, with VLC still showing that frame: omx_loop
    starts omxplayer in front of it, so there is no black screen in between."""
    while True:
        media = vlc_instance.media_new(source)
        player.set_media(media)
        player.play()
        shown = pause_at_first_picture(time.time())
        if shown == 'held':
            write_status('paused', source, 0)
            # VLC pauses a moment later
            paused_by = time.time() + 2
            while player.get_state() == vlc.State.Playing and time.time() < paused_by and not skip_requested:
                time.sleep(.01)
            while player.get_state() == vlc.State.Paused and not skip_requested and not rewind_requested:
                time.sleep(.05)
        if rewind_requested and not skip_requested:
            # rewind pressed again while held: start it again
            media.release()
            continue
        if shown and not skip_requested:
            # play: VLC holds this picture while omxplayer starts
            player.set_pause(1)
        return media

# find a file, and if found, return its path (for sync)
def search_file(file_name):
    matching_files = glob.glob(f'/media/*/{file_name}') + glob.glob(f'/boot/{file_name}')
    return matching_files[0] if matching_files else False

# sync mode (from v6): several players play sync.mp4 in sync with omxplayer-sync
def sync_mode():
    sync_file = search_file("sync.mp4")
    if sync_file and sync_file in read_disabled():
        # switched off in the web interface: the playlist plays instead (without it)
        print("%s is switched off: no sync mode" % sync_file, flush=True)
        return
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
            write_status('sync', sync_file, engine='omxplayer-sync')
            subprocess.run(["omxplayer-sync", "-u", flag, sync_file])

# the network name the web interface gives the Pi (system.configured_hostname in mp4m-web): the
# one set there, else mp4museum-xxxx from the serial number (or a MAC address). Worked out here
# as the Pi may not have taken it yet when the logo screen shows
def network_name():
    try:
        with open(HOSTNAME_FILE, 'r') as f:
            name = f.read().strip().lower()
        if re.match(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$', name):
            return name
    except OSError:
        pass
    unique_id = ''
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    unique_id = line.split(':', 1)[1].strip()
                    break
    except OSError:
        pass
    if not unique_id:
        try:
            interfaces = sorted(name for name in os.listdir('/sys/class/net') if name != 'lo')
        except OSError:
            interfaces = []
        for interface in interfaces:
            try:
                with open('/sys/class/net/%s/address' % interface, 'r') as f:
                    unique_id = f.read().strip()
            except OSError:
                continue
            if unique_id:
                break
    if not unique_id:
        return 'mp4museum'
    return 'mp4museum-' + hashlib.sha256(unique_id.encode()).hexdigest()[:4]

# the address of the web interface, e.g. "http://mp4museum-1a2b.local" and "192.168.1.42" on a
# second line (not there until the Pi has an address from the network)
def address_text():
    text = 'http://%s.local' % network_name()
    try:
        output = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5).stdout
        addresses = [a for a in output.split() if '.' in a and not a.startswith('169.254.')]
        if addresses:
            text += '\n' + '   '.join(addresses[:2])
    except (OSError, subprocess.SubprocessError):
        pass
    return text

def set_marquee(text, width=1280):
    """Text in the bottom right corner of the picture, or None to take it off. VLC only sets it
    on a picture being shown, so while a file plays. Sizes are in the picture's pixels: width
    is the picture's, so the text is the same size on any picture. Grey, like the logo's
    'Please Wait', so it doesn't stand out more than the logo."""
    try:
        if text:
            margin = max(10, round(30 * width / 1280))
            player.video_set_marquee_string(vlc.VideoMarqueeOption.Text, text)
            player.video_set_marquee_int(vlc.VideoMarqueeOption.Size, max(10, round(24 * width / 1280)))
            player.video_set_marquee_int(vlc.VideoMarqueeOption.Color, 0xB0B0B0)
            player.video_set_marquee_int(vlc.VideoMarqueeOption.X, margin)
            player.video_set_marquee_int(vlc.VideoMarqueeOption.Y, margin)
            player.video_set_marquee_int(vlc.VideoMarqueeOption.Position, 10)
        player.video_set_marquee_int(vlc.VideoMarqueeOption.Enable, 1 if text else 0)
        return True
    except Exception as e:
        print("couldn't show the address on the logo screen: %s" % e, flush=True)
        return False

# the address on the logo screen, so it's easy to find the web interface: shown once the logo
# is, and looked up again every 2 seconds until the Pi has an IP address
class LogoAddress:
    def __init__(self, picture):
        size = image_size(picture)
        self.width = size[0] if size else 1280
        self.text = None
        self.checked = 0
        self.failed = False
    def __call__(self, position, length):
        if (position > 0 and not self.failed and time.time() - self.checked >= 2
                and (self.text is None or '\n' not in self.text)):
            self.checked = time.time()
            text = address_text()
            if text != self.text:
                if set_marquee(text, self.width):
                    self.text = text
                else:
                    # VLC can't show it here: not tried again (it says why in the log once)
                    self.failed = True

def media_files():
    """The files to play, in order."""
    return sorted(path for path in glob.glob(MEDIA_FILES) if path.lower().endswith(MEDIA_TYPES))

def read_disabled():
    try:
        with open(DISABLED_FILE, 'r') as f:
            return {line.rstrip('\n') for line in f if line.strip()}
    except OSError:
        return set()

# whether the loop below passes over a file without playing it (it says why there)
def would_skip(file, try_skipped, disabled):
    if file in disabled:
        return True
    if file in skipped and not try_skipped:
        version, count = skipped[file]
        if count >= SKIP_AFTER and version == file_version(file):
            return True
    if image_limit and file.lower().endswith(IMAGE_TYPES):
        size = image_size(file)
        if size and max(size) > image_limit:
            return True
    return False

# *** run player ****

boot_video = CUSTOM_BOOT_VIDEO if os.path.isfile(CUSTOM_BOOT_VIDEO) else BOOT_VIDEO
# it plays before anything else, so one that keeps stopping the player would keep it from ever
# getting to the playlist: the original is played instead
entry = skipped.get(boot_video)
if boot_video != BOOT_VIDEO and entry and entry[1] >= SKIP_AFTER and entry[0] == file_version(boot_video):
    print("%s stopped the player twice: playing the original boot video instead" % boot_video, flush=True)
    boot_video = BOOT_VIDEO

# start player twice to make sure it is working
# seems weird but works
# (boot_video_plays: once by default. Without the boot video, a Pi 3 B+ showed the first file
# properly after a reboot and 4 cold starts, so the warm-up isn't needed there; 2 as before)
settings = read_settings()
boot_played = [vlc_play(boot_video) for _ in range(settings['boot_video_plays'])]
# forgiven only when all played: it could stop the player on the second
if boot_played and all(result == "ended" for result in boot_played):
    forgive(boot_video)

# please do not remove my logo screen
skip_requested = False
logo = LOGO if os.path.isfile(LOGO) else OLD_LOGO
logo_address = LogoAddress(logo) if settings['show_address'] else None
vlc_play(logo, (':image-duration=%d' % DEFAULT_IMAGE_DURATION,) + IMAGE_OPTIONS, while_playing=logo_address)
if logo_address and logo_address.text is not None:
    # once the logo has ended, VLC can't take the address off: it would stay on the next file.
    # Stopping VLC drops its picture, and the address with it (a black moment, only at start-up)
    set_marquee(None)
    player.stop()

# add event listener which reacts to GPIO signal
# (RPi.GPIO's thread, and the button's, are started with the signals from the web interface
# blocked, so those always go to the main thread, where the loop can hold them off while it
# picks the next file)
signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1, signal.SIGUSR2})
GPIO.add_event_detect(11, GPIO.RISING, callback = buttonPause, bouncetime = 234)
GPIO.add_event_detect(13, GPIO.RISING, callback = buttonNext, bouncetime = 50)
signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGUSR1, signal.SIGUSR2})

# check for sync mode instructions
sync_mode()

# the loop
retry_next_round = False
playlist_started = True
try:
    while(1):
        files = media_files()
        # nothing to play yet (no USB stick, empty media partition): check again shortly
        if not files:
            # don't leave the last frame of a deleted file on screen
            player.stop()
            write_status('idle')
            time.sleep(2)
        index = 0
        played = False
        try_skipped = retry_next_round
        retry_next_round = False
        while index < len(files):
            # a file chosen in the web interface plays next, then the files after it; a next press
            # from here on skips this file. (No signal in between, or it would be lost.)
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
            requested, requested_file = requested_file, None
            back, previous_requested = previous_requested, 0
            skip_requested = False
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGUSR1})
            # switched off in the web interface: read for every file, so a change applies straight away
            disabled = read_disabled()
            if back and files:
                # previous: back from the file that was playing (files[index - 1]; the last one
                # at the start of a round), over the files this round skips, around the end
                position = (index - 1) % len(files)
                for _ in range(len(files)):
                    position = (position - 1) % len(files)
                    if not would_skip(files[position], try_skipped, disabled):
                        back -= 1
                        if not back:
                            break
                index = position
            if requested and requested not in files:
                files = media_files()
            if requested in files:
                index = files.index(requested)
                if requested in skipped:
                    # chosen in the web interface: try it again
                    del skipped[requested]
                    save_skipped()
            if index >= len(files):
                break
            file = files[index]
            index += 1
            if file in disabled:
                continue
            if file in skipped:
                version, count = skipped[file]
                if version != file_version(file):
                    # replaced since
                    del skipped[file]
                    save_skipped()
                elif count >= SKIP_AFTER and not try_skipped:
                    continue
            if image_limit and file.lower().endswith(IMAGE_TYPES):
                size = image_size(file)
                if size and max(size) > image_limit:
                    report_once(('large', file, tuple(size)),
                                "skipping %s: %d x %d pixels, more than this Pi can show (%d); resize it"
                                % (file, size[0], size[1], image_limit))
                    continue
            played = True
            files_started += 1
            # read for every file, so a new image duration applies straight away
            settings = read_settings()
            options = [':image-duration=%d' % settings['image_duration']] + list(IMAGE_OPTIONS)
            loop_engine = loop_omx_ok = None
            if "loop." in file:
                # play it again and again until next is pressed
                loop_omx_ok = bool(file.lower().endswith(OMX_LOOP_TYPES) and shutil.which("omxplayer")
                                   and omx_can_play(file))
                if settings['loop_player'] == 'omxplayer' and loop_omx_ok:
                    loop_engine = 'omxplayer'
                    if omx_loop(file) != 'failed':
                        forgive(file)
                        continue
                    print("falling back to VLC for %s" % file, flush=True)
                    loop_omx_ok = False
                loop_engine = 'vlc'
                # VLC starts it again in the same window each time it ends
                while not skip_requested:
                    result = vlc_play(file, options)
                    if result == 'ended':
                        forgive(file)
                    elif result != 'rewind':
                        break
            else:
                limit = settings['image_duration'] + IMAGE_GRACE if file.lower().endswith(IMAGE_TYPES) else None
                result = vlc_play(file, options, limit=limit)
                while result == 'rewind':
                    # started again, held at its first frame
                    result = vlc_play(file, options, limit=limit)
                if result == 'ended':
                    forgive(file)
        if files and not played:
            # nothing could be played: better to try the skipped files again than show nothing
            if any(count >= SKIP_AFTER for version, count in skipped.values()):
                report_once(('retry', tuple(sorted(skipped))), "every file is skipped: trying them again")
                retry_next_round = True
            player.stop()
            write_status('idle')
            time.sleep(2)
finally:
    if omx:
        stop_omx(omx)
