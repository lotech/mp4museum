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

def fake_sleep(seconds):
    clock['now'] += seconds
    if clock['now'] - 1000 > scenario.get('max_seconds', 600):
        raise SystemExit('time limit')
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
        if len(log) > scenario.get('max_plays', 50):
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
        # scripted events: send signals at given times
        for event in scenario.get('signals', []):
            if not event.get('done') and clock['now'] - 1000 >= event['at']:
                event['done'] = True
                os.kill(os.getpid(), getattr(signal, event['signal']))
        return self.state
    def stop(self):
        if self.state in (_S.Playing, _S.Paused):
            log.append({'stop': self.media.path, 'at': round(clock['now'] - 1000, 2)})
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
