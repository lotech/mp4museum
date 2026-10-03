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


def stop_calls(result):
    return [event for event in result['log'] if 'stop_call' in event]


def test_vlc_stays_open_between_files_and_loop_passes(tmp_path):
    # stopping closes VLC's video output (black frames), so files that end by themselves aren't stopped
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/clip-loop.mp4'],
            media={'clip-loop.mp4': 3}, max_plays=12)
    assert plays(r)[3:4] == ['a.mp4'] and plays(r)[4:9] == ['clip-loop.mp4'] * 5
    assert stop_calls(r) == []


def test_vlc_stopped_when_skipped_and_when_idle(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.mp4'], media={'a.mp4': 100},
            signals=[{'at': 30, 'signal': 'SIGUSR1'}], max_plays=6)
    assert [e['stop_call'] for e in stop_calls(r)][:1] == ['/media/internal/a.mp4']
    r = run(tmp_path, files=[], max_seconds=30)
    assert stop_calls(r)     # no last frame left on screen while there's nothing to play


OMX_SETTING = {'/boot/mp4m-player.txt': 'loop_player=omxplayer\n'}


def omx_starts(result):
    return [event for event in result['log'] if 'omxplayer' in event]


def killpgs(result):
    return [(event['killpg'], round(event['at'])) for event in result['log'] if 'killpg' in event]


def test_loop_with_omxplayer_by_default(tmp_path):
    # omxplayer holds the last frame at the loop; VLC showed a black frame on the Pi
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'], max_seconds=60)
    assert len(omx_starts(r)) == 1 and plays(r)[3:] == []
    # VLC when chosen in the web interface, or when omxplayer isn't installed
    for options in ({'installed': ['omxplayer'], 'write': {'/boot/mp4m-player.txt': 'loop_player=vlc\n'}}, {}):
        r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], media={'clip-loop.mp4': 3}, max_plays=6, **options)
        assert omx_starts(r) == [] and plays(r)[3:5] == ['clip-loop.mp4'] * 2


def test_loop_with_omxplayer(tmp_path):
    r = run(tmp_path, files=['/media/internal/00VJSurvivalKit_06-loop.mp4', '/media/internal/zz.mp4'],
            installed=['omxplayer'], write=dict(OMX_SETTING, **{'/boot/alsa.txt': '1'}),
            signals=[{'at': 40, 'signal': 'SIGUSR2'}, {'at': 45, 'signal': 'SIGUSR2'}, {'at': 60, 'signal': 'SIGUSR1'}],
            max_plays=6)
    # started once, until next was pressed (then again when the playlist comes round)
    assert [start['at'] < 60 for start in omx_starts(r)][:2] == [True, False]
    assert omx_starts(r)[0]['omxplayer'] == ['omxplayer', '--loop', '--no-osd', '-b', '-o', 'alsa:plughw:1,0',
                                             '/media/internal/00VJSurvivalKit_06-loop.mp4']
    # VLC lets go of the screen first
    first_omx = r['log'].index(omx_starts(r)[0])
    assert any('stop_call' in event for event in r['log'][first_omx - 3:first_omx])
    # pause and resume are omxplayer's p key; next stops it with SIGINT (its clean shutdown)
    assert [event['key'] for event in r['log'] if 'key' in event] == ['p', 'p']
    assert killpgs(r)[:1] == [(2, 60)]
    states = [status['state'] for status in r['statuses']
              if (status.get('file') or '').endswith('-loop.mp4') and status['since'] < 1060]
    assert states == ['playing', 'paused', 'playing']
    # then the playlist carries on in VLC
    assert '-loop.mp4' not in ' '.join(plays(r)) and 60 <= first_play(r, 'zz.mp4')['at'] <= 62


def test_omxplayer_restarted_if_it_stops_by_itself(tmp_path):
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4', '/media/internal/zz.mp4'], installed=['omxplayer'],
            write=OMX_SETTING, omx_exits_after=30, signals=[{'at': 100, 'signal': 'SIGUSR1'}], max_plays=6)
    assert len(omx_starts(r)) >= 3 and 100 <= first_play(r, 'zz.mp4')['at'] <= 102


def test_omxplayer_that_hangs_is_killed(tmp_path):
    # /usr/bin/omxplayer is a script: it dies on SIGTERM, but omxplayer.bin can still be running
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4', '/media/internal/zz.mp4'], installed=['omxplayer'],
            write=OMX_SETTING, omx_hangs=True, signals=[{'at': 40, 'signal': 'SIGUSR1'}], max_plays=6)
    assert [signum for signum, at in killpgs(r)][:3] == [2, 15, 9]
    assert 44 <= first_play(r, 'zz.mp4')['at'] <= 46


def test_omxplayer_keys_not_sent_too_early_or_together(tmp_path):
    # omxplayer ignores keys until it has started, and reads two keys at once as an unknown key
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'], write=OMX_SETTING,
            signals=[{'at': 21, 'signal': 'SIGUSR2'}, {'at': 30, 'signal': 'SIGUSR2'}, {'at': 30.1, 'signal': 'SIGUSR2'}],
            max_seconds=60)
    assert [round(event['at']) for event in r['log'] if 'key' in event] == [30]
    assert [status['state'] for status in r['statuses'] if status['file']][-1] == 'paused'


def test_omxplayer_stopped_when_player_exits(tmp_path):
    for signals in ([], [{'at': 40, 'signal': 'SIGTERM'}], [{'at': 40, 'signal': 'SIGHUP'}]):
        r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'], write=OMX_SETTING,
                signals=signals, max_seconds=100)
        assert len(omx_starts(r)) == 1 and [signum for signum, at in killpgs(r)] == [2]
        assert r['log'][-1] == {'exit': '0' if signals else 'time limit'}


def test_loop_falls_back_to_vlc_when_omxplayer_fails(tmp_path):
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4', '/media/internal/zz.mp4'], installed=['omxplayer'],
            write=OMX_SETTING, omx_fails=True, media={'clip-loop.mp4': 4}, max_plays=8)
    assert omx_starts(r) and plays(r)[3:6] == ['clip-loop.mp4'] * 3


def test_loop_images_stay_in_vlc(tmp_path):
    r = run(tmp_path, files=['/media/internal/still-loop.jpg'], installed=['omxplayer'],
            write={'/boot/mp4m-player.txt': 'loop_player=omxplayer\nimage_duration=3\n'}, max_plays=6)
    assert omx_starts(r) == [] and plays(r)[3:5] == ['still-loop.jpg'] * 2


# ----- Web interface: choosing a file, position ----- #
def test_play_file_chosen_in_web_interface(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4', '/media/usb0/d.mp4']
    # chosen while a.mp4 plays: c.mp4 plays straight away, then the files after it
    r = run(tmp_path, files=files, signals=[{'at': 21, 'play': '/media/internal/c.mp4'}], max_plays=8)
    assert plays(r)[3:8] == ['a.mp4', 'c.mp4', 'd.mp4', 'a.mp4', 'b.mp4']
    assert 21 <= first_play(r, 'c.mp4')['at'] <= 22


def test_play_file_ends_a_loop(tmp_path):
    files = ['/media/internal/a-loop.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4']
    for options in ({}, {'installed': ['omxplayer']}):
        r = run(tmp_path, files=files, signals=[{'at': 40, 'play': '/media/internal/c.mp4'}], max_plays=7, **options)
        assert 40 <= first_play(r, 'c.mp4')['at'] <= 41 and 'b.mp4' not in plays(r)[:-1]


def test_play_request_ignored_if_old_or_unknown(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4']
    # left from before the player started
    r = run(tmp_path, files=files, write={'/tmp/mp4museum-play.json': '{"id": "old", "file": "/media/internal/c.mp4"}'},
            signals=[{'at': 21, 'signal': 'SIGUSR1'}], max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'b.mp4', 'c.mp4']
    # not a file the player plays: next is pressed and the playlist carries on
    r = run(tmp_path, files=files, signals=[{'at': 21, 'play': '/etc/passwd'}], max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'b.mp4', 'c.mp4'] and 21 <= first_play(r, 'b.mp4')['at'] <= 22


def test_status_has_position_and_length(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4'], media={'a.mp4': 30},
            signals=[{'at': 30, 'signal': 'SIGUSR2'}, {'at': 35, 'signal': 'SIGUSR2'}], max_plays=4)
    statuses = [s for s in r['statuses'] if s['file'] == '/media/internal/a.mp4']
    assert statuses[0]['position'] == 0 and statuses[0]['length'] is None and statuses[0]['play_file'] is True
    assert statuses[1]['state'] == 'playing' and statuses[1]['length'] == 30 and statuses[1]['position'] < 2
    paused = [s for s in statuses if s['state'] == 'paused'][0]
    resumed = statuses[statuses.index(paused) + 1]
    assert 9 <= paused['position'] <= 11 and abs(resumed['position'] - paused['position']) < .1
