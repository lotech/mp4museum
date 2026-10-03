"""The player (v7-beta/boot/mp4museum.py), run against fake VLC and GPIO.

Each test describes a scenario (files on the media partition, how long each plays,
settings, signals) and checks what the player did; see player_harness.py.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import json
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).parent / 'player_harness.py'


def run(tmp_path, **scenario):
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(scenario))
    out = subprocess.run([sys.executable, str(HARNESS), str(path)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, universal_newlines=True)
    assert out.returncode == 0 and out.stdout.strip(), out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def plays(result):
    return [event['play'].split('/')[-1] for event in result['log'] if 'play' in event]


def first_play(result, name):
    return [event for event in result['log'] if event.get('play', '').endswith(name)][0]


def test_start_up_and_playlist_order(tmp_path):
    r = run(tmp_path, files=['/media/usb0/b.mp4', '/media/internal/a.mp4', '/media/internal/c.jpg'], max_plays=9)
    p = plays(r)
    # boot video twice (as in the original), the logo, then the files sorted by path, again and again
    assert p[:3] == ['mp4museum-boot.mp4', 'mp4museum-boot.mp4', 'mp4m-v7beta.jpg']
    assert p[3:6] == ['a.mp4', 'c.jpg', 'b.mp4'] and p[6:9] == ['a.mp4', 'c.jpg', 'b.mp4']
    instances = [event['instance'] for event in r['log'] if 'instance' in event]
    assert len(instances) == 1 and instances[0].endswith('hw:0')
    assert first_play(r, 'mp4m-v7beta.jpg')['options'] == [':image-duration=10']
    assert ':image-duration=10' in first_play(r, 'c.jpg')['options']


def test_boot_video_original_or_custom(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4'], max_plays=4)
    assert [event['play'] for event in r['log'] if 'play' in event][:2] == ['/home/pi/mp4museum-boot.mp4'] * 2
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4museum-boot.mp4': 'x'}, max_plays=4)
    assert all(event['play'].endswith('custom-boot.mp4') for event in [e for e in r['log'] if 'play' in e][:2])


def test_settings(tmp_path):
    r = run(tmp_path, files=['/media/internal/still.png'],
            write={'/boot/mp4m-player.txt': 'image_duration=25\n', '/boot/alsa.txt': '12\n'}, max_plays=4)
    assert [event['instance'] for event in r['log'] if 'instance' in event][0].endswith('hw:12')
    assert first_play(r, 'still.png')['options'] == [':image-duration=25']

    r = run(tmp_path, files=['/media/internal/a.png'],
            write={'/boot/mp4m-player.txt': 'image_duration=²\n', '/boot/alsa.txt': 'auto'}, max_plays=4)
    assert [event['instance'] for event in r['log'] if 'instance' in event][0].endswith('hw:0')
    assert first_play(r, 'a.png')['options'] == [':image-duration=10']


def test_stuck_file_is_skipped(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/broken.mp4', '/media/internal/c.mp4'],
            media={'broken.mp4': 'stuck'}, max_plays=7)
    assert plays(r)[3:6] == ['a.mp4', 'broken.mp4', 'c.mp4']
    waited = first_play(r, 'c.mp4')['at'] - first_play(r, 'broken.mp4')['at']
    assert 19 <= waited <= 23


def test_nothing_to_play(tmp_path):
    r = run(tmp_path, files=[], max_seconds=60)
    idle = [status for status in r['statuses'] if status['state'] == 'idle']
    assert 5 <= len(idle) <= 40       # waits between scans instead of spinning


def test_pause_and_next_signals(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.mp4'], media={'a.mp4': 100},
            signals=[{'at': 30, 'signal': 'SIGUSR2'}, {'at': 35, 'signal': 'SIGUSR2'}, {'at': 40, 'signal': 'SIGUSR1'}],
            max_plays=12)
    assert {'pause': True} in r['log'] and {'pause': False} in r['log']
    stop = [event for event in r['log'] if event.get('stop', '').endswith('a.mp4')]
    assert stop and 40 <= stop[0]['at'] < 42 and plays(r)[4] == 'b.mp4'
    states = [status['state'] for status in r['statuses'] if (status.get('file') or '').endswith('a.mp4')]
    assert 'paused' in states and states[-1] == 'playing'


def test_loop_file_restarts_until_next(tmp_path):
    r = run(tmp_path, files=['/media/internal/00VJSurvivalKit_06-loop.mp4', '/media/internal/zz.mp4'],
            media={'00VJSurvivalKit_06-loop.mp4': 4}, signals=[{'at': 60, 'signal': 'SIGUSR1'}], max_plays=40)
    p = plays(r)
    loops = p[3:p.index('zz.mp4')]
    assert len(loops) >= 5 and set(loops) == {'00VJSurvivalKit_06-loop.mp4'}
    assert 60 <= first_play(r, 'zz.mp4')['at'] <= 62
    assert not any('input-repeat' in option for option in first_play(r, '-loop.mp4')['options'])


def test_short_and_broken_loop_files(tmp_path):
    r = run(tmp_path, files=['/media/internal/blink-loop.mp4', '/media/internal/zz.mp4'],
            media={'blink-loop.mp4': 0.5}, max_plays=12)
    assert plays(r)[3:7] == ['blink-loop.mp4'] * 4
    r = run(tmp_path, files=['/media/internal/bad-loop.mp4', '/media/internal/zz.mp4'],
            media={'bad-loop.mp4': 'error'}, max_plays=12)
    assert plays(r)[3:5] == ['bad-loop.mp4', 'zz.mp4']


def test_sync_mode(tmp_path):
    files = ['/media/usb0/sync.mp4', '/media/usb0/sync-leader.txt']
    r = run(tmp_path, files=files, max_plays=5)
    assert not any('run' in event for event in r['log']) and 'sync.mp4' in plays(r)

    r = run(tmp_path, files=files, installed=['omxplayer-sync'], max_plays=6)
    assert [event['run'] for event in r['log'] if 'run' in event] == [['omxplayer-sync', '-u', '-m', '/media/usb0/sync.mp4']]
    assert r['statuses'][-1]['state'] == 'sync'

    r = run(tmp_path, files=['/media/usb0/sync.mp4', '/media/usb0/sync-player.txt'], installed=['omxplayer-sync'], max_plays=6)
    assert [event['run'] for event in r['log'] if 'run' in event] == [['omxplayer-sync', '-u', '-l', '/media/usb0/sync.mp4']]
