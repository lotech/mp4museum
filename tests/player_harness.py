"""Run the player (v7-beta/boot/mp4museum.py) against fake VLC and GPIO with a virtual clock.

Used by test_player.py: python player_harness.py <scenario.json> prints, as its last line,
a JSON log of what the player did. Time only passes when the player sleeps, so minutes of
playback take milliseconds.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import json, os, sys, types, time, glob as _glob, signal, subprocess, shutil, tempfile
from pathlib import Path

PLAYER = Path(__file__).resolve().parents[1] / 'v7-beta' / 'boot' / 'mp4museum.py'

scenario = json.load(open(sys.argv[1]))
log = []
clock = {'now': 1000.0}

def fire_signals():
    # scripted events: send signals at given times
    for event in scenario.get('signals', []):
        if not event.get('done') and clock['now'] - 1000 >= event['at']:
            event['done'] = True
            os.kill(os.getpid(), getattr(signal, event['signal']))

active_omx = []

def fake_sleep(seconds):
    clock['now'] += seconds
    if clock['now'] - 1000 > scenario.get('max_seconds', 600):
        raise SystemExit('time limit')
    # while omxplayer plays, VLC isn't polled, so signals are sent from here
    if active_omx:
        fire_signals()
time.sleep = fake_sleep
time.time = lambda: clock['now']

# --- fake vlc ---
vlc = types.ModuleType('vlc')
class _S:
    NothingSpecial, Opening, Buffering, Playing, Paused, Stopped, Ended, Error = range(8)
vlc.State = _S
class Media:
    def __init__(self, path, options):
        self.path, self.options = path, list(options)
    def release(self): pass
class Player:
    def __init__(self): self.media = None; self.state = _S.NothingSpecial; self.paused_for = 0
    def set_media(self, m): self.media = m
    def play(self):
        m = self.media
        log.append({'play': m.path, 'options': m.options, 'at': round(clock['now'] - 1000, 2)})
        if sum('play' in event for event in log) > scenario.get('max_plays', 50):
            raise SystemExit('play limit')
        self.started = clock['now']
        behaviour = scenario.get('media', {}).get(os.path.basename(m.path), 5)
        self.state = {'error': _S.Error, 'stuck': _S.Opening}.get(behaviour, _S.Playing)
        self.length = behaviour if isinstance(behaviour, (int, float)) else 10 ** 9
        if 'image-duration' in ' '.join(m.options) and m.path.endswith('.jpg'):
            self.length = int([o for o in m.options if 'image-duration' in o][0].split('=')[1])
        if any('input-repeat' in o for o in m.options):
            self.length = 10 ** 9
    def get_state(self):
        if self.state == _S.Playing and clock['now'] - self.started >= self.length and self.length:
            self.state = _S.Ended
        fire_signals()
        return self.state
    def stop(self):
        if self.state in (_S.Playing, _S.Paused):
            log.append({'stop': self.media.path, 'at': round(clock['now'] - 1000, 2)})
        log.append({'stop_call': self.media.path if self.media else None, 'state': int(self.state)})
        self.state = _S.Stopped
    def pause(self):
        self.state = _S.Paused if self.state == _S.Playing else _S.Playing
        log.append({'pause': self.state == _S.Paused})
class Instance:
    def __init__(self, args):
        log.append({'instance': args})
    def media_player_new(self): return Player()
    def media_new(self, path, *options): return Media(path, options)
vlc.Instance = Instance
sys.modules['vlc'] = vlc

# --- fake GPIO ---
gpio = types.ModuleType('RPi.GPIO')
for name in ('setmode', 'setup', 'add_event_detect', 'input'):
    setattr(gpio, name, lambda *a, **k: 0)
gpio.BOARD = gpio.IN = gpio.PUD_DOWN = gpio.RISING = 0
rpi = types.ModuleType('RPi'); rpi.GPIO = gpio
sys.modules['RPi'] = rpi; sys.modules['RPi.GPIO'] = gpio

# --- files ---
media_files = scenario.get('files', [])
_glob.glob = lambda pattern: [f for f in media_files if _glob.fnmatch.fnmatch(f, pattern)]
import fnmatch; _glob.fnmatch = fnmatch
subprocess.run = lambda cmd, *a, **k: log.append({'run': cmd}) or (_ for _ in ()).throw(SystemExit('sync ran'))

# --- fake omxplayer ---
# /usr/bin/omxplayer is a script that runs omxplayer.bin; both are modelled. Scenario options:
# omx_fails (exits at once), omx_exits_after (seconds), omx_hangs (omxplayer.bin ignores SIGINT
# and SIGTERM; the script dies on SIGTERM)
class FakeStdin:
    def __init__(self, process): self.process = process
    def fileno(self): return -self.process.pid
class FakeOmxplayer:
    def __init__(self, cmd, **kwargs):
        log.append({'omxplayer': cmd, 'at': round(clock['now'] - 1000, 2)})
        if len([e for e in log if 'omxplayer' in e]) > scenario.get('max_omx', 20):
            raise SystemExit('omx limit')
        self.pid = 90000 + len(log)
        self.stdin = FakeStdin(self)
        self.returncode = None
        self.bin_running = True
        self.started = clock['now']
        omx_processes[self.pid] = self
        active_omx.append(self)
        if scenario.get('omx_fails'):
            self.end(1)
    def end(self, code, script_only=False):
        if self.returncode is None:
            self.returncode = code
        if not script_only:
            self.bin_running = False
        if not self.bin_running and self in active_omx:
            active_omx.remove(self)
    def poll(self):
        after = scenario.get('omx_exits_after')
        if self.returncode is None and after and clock['now'] - self.started >= after:
            self.end(0)
        return self.returncode
omx_processes = {}
def fake_killpg(pid, signum):
    process = omx_processes[pid]
    if signum == 0:
        if process.returncode is None or process.bin_running:
            return
        raise ProcessLookupError(pid)
    log.append({'killpg': int(signum), 'at': round(clock['now'] - 1000, 2)})
    if signum == signal.SIGKILL or not scenario.get('omx_hangs'):
        process.end(-int(signum))
    elif signum == signal.SIGTERM:
        process.end(-int(signum), script_only=True)
os.killpg = fake_killpg
_real_write = os.write
def fake_write(fd, data):
    if -fd in omx_processes:
        log.append({'key': data.decode(), 'at': round(clock['now'] - 1000, 2)})
        if not omx_processes[-fd].bin_running:
            raise BrokenPipeError()
        return len(data)
    return _real_write(fd, data)
os.write = fake_write
def fake_popen(cmd, **kwargs):
    if cmd[0] == 'omxplayer':
        return FakeOmxplayer(cmd, **kwargs)
    raise SystemExit('unexpected Popen %r' % cmd)
subprocess.Popen = fake_popen
shutil.which = lambda name: '/usr/bin/' + name if name in scenario.get('installed', []) else None

tmp = tempfile.mkdtemp()
paths = {'/boot/mp4museum-boot.mp4': os.path.join(tmp, 'custom-boot.mp4'),
         '/boot/alsa.txt': os.path.join(tmp, 'alsa.txt'), '/boot/mp4m-player.txt': os.path.join(tmp, 'mp4m-player.txt'),
         '/tmp/mp4museum-status.json': os.path.join(tmp, 'status.json')}
for real, fake in paths.items():
    if real in scenario.get('write', {}):
        open(fake, 'w').write(scenario['write'][real])
source = PLAYER.read_text()
for real, fake in paths.items():
    source = source.replace(repr(real)[1:-1], fake)
statuses = []
_real_replace = os.replace
def watching_replace(a, b):
    _real_replace(a, b)
    if b == paths['/tmp/mp4museum-status.json']:
        statuses.append(json.load(open(b)))
os.replace = watching_replace
try:
    exec(compile(source, 'mp4museum.py', 'exec'), {'__name__': '__main__'})
except SystemExit as e:
    log.append({'exit': str(e)})
print(json.dumps({'log': log, 'statuses': statuses}))
