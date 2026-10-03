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
    result = json.loads(out.stdout.strip().splitlines()[-1])
    # what the player printed (its log)
    result['stdout'] = '\n'.join(out.stdout.strip().splitlines()[:-1])
    return result


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


def test_omxplayer_only_for_codecs_it_can_play(tmp_path):
    # HEVC (or MPEG-2 without a licence key) can play black in omxplayer without it stopping
    files = ['/media/internal/clip-loop.mkv']
    for codec, with_omx in (('hevc', False), ('mpeg2video', False), ('mpeg4', True), ('h264', True)):
        r = run(tmp_path, files=files, installed=['omxplayer'], omx_codec=codec, media={'clip-loop.mkv': 3},
                max_plays=5, max_seconds=60)
        assert [e['probe'] for e in r['log'] if 'probe' in e][:1] == files[:1]
        assert bool(omx_starts(r)) == with_omx, codec


def test_next_while_a_file_is_starting(tmp_path):
    # the press comes before VLC has started the file: it is skipped, not played to the end
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.mp4'], media={'a.mp4': 100},
            signals=[{'at': 20, 'signal': 'SIGUSR1', 'when': 'settings'}], max_plays=6)
    assert 20 <= first_play(r, 'a.mp4')['at'] <= 21 and 21 <= first_play(r, 'b.mp4')['at'] <= 22


def test_status_has_a_clock_that_does_not_jump(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4'], max_plays=4)
    status = r['statuses'][-1]
    assert status['mono'] == status['since'] - 500


# ----- Stopping and starting again ----- #
def test_ctrl_c_stops_the_player_with_exit_code_0(tmp_path):
    # .bashrc starts the player again unless it exits with 0
    for at in (2, 30):     # during the boot video, and while playing
        r = run(tmp_path, files=['/media/internal/a.mp4'], signals=[{'at': at, 'signal': 'SIGINT'}], max_plays=20)
        assert r['log'][-1] == {'exit': '0'}
    r = run(tmp_path, files=['/media/internal/a-loop.mp4'], installed=['omxplayer'],
            signals=[{'at': 40, 'signal': 'SIGINT'}], max_plays=20)
    assert r['log'][-1] == {'exit': '0'} and killpgs(r)[:1] == [(2, 40)]


def bashrc_autostart(tmp_path, exit_codes, output=0):
    """Run the autostart part of .bashrc with a fake python3 that exits with these codes in turn
    (printing output bytes each time). Returns how often it ran, the log and the waits between."""
    bashrc = (Path(__file__).parents[1] / 'v7-beta' / 'home' / 'pi' / '.bashrc').read_text()
    start = bashrc.index('# mp4museum autostart')
    block = bashrc[start:bashrc.index('setterm -cursor on', start)].replace('/tmp/mp4museum.log', str(tmp_path / 'log'))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    (tmp_path / 'codes').write_text(' '.join(map(str, exit_codes)))
    fakes = {'python3': 'set -- $(cat "%s"); echo "run $*" >> "%s"; echo "${@:2}" > "%s"; head -c %d /dev/zero | tr "\\0" x; exit $1'
                        % (tmp_path / 'codes', tmp_path / 'runs', tmp_path / 'codes', output),
             'setterm': 'exit 0', 'clear': 'exit 0', 'sleep': 'echo $1 >> "%s"' % (tmp_path / 'sleeps')}
    for name, body in fakes.items():
        (bin_dir / name).write_text('#!/bin/bash\n' + body + '\n')
        (bin_dir / name).chmod(0o755)
    subprocess.run(['bash', '-c', block], env={'PATH': '%s:/usr/bin:/bin' % bin_dir}, check=True, timeout=30)
    sleeps = (tmp_path / 'sleeps').read_text().split() if (tmp_path / 'sleeps').exists() else []
    return (tmp_path / 'runs').read_text().count('run'), (tmp_path / 'log').read_text(), [int(n) for n in sleeps]


def test_bashrc_starts_the_player_again_if_it_dies(tmp_path):
    # killed for lack of memory (137), then an error (1), then stopped with Ctrl-C (0)
    runs, log, sleeps = bashrc_autostart(tmp_path, [137, 1, 0])
    assert runs == 3 and 'exit code 137)' in log and 'exit code 1)' in log


def test_bashrc_when_the_player_keeps_stopping(tmp_path):
    # e.g. an error in an edited script: waits longer each time, and the log (in memory) stays small
    runs, log, sleeps = bashrc_autostart(tmp_path, [1] * 8 + [0], output=300000)
    assert runs == 9 and sleeps == [6, 12, 24, 48, 60, 60, 60, 60]
    assert 150000 < len(log) <= 1000000


def crashed_on(path, state='playing', times_before=0, pid=999999):
    # the status the last player left behind (no process has pid 999999), and how many times
    # that file had already stopped the player
    write = {'/tmp/mp4museum-status.json': json.dumps({'state': state, 'file': path, 'since': 1, 'pid': pid})}
    if times_before:
        write['/tmp/mp4museum-skipped.json'] = json.dumps([[path, None, times_before]])
    return write


def test_file_that_stopped_the_player_twice_is_skipped(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/b.png', '/media/internal/c.mp4']
    # once could be chance: still played
    r = run(tmp_path, files=files, write=crashed_on('/media/internal/b.png'), max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'b.png', 'c.mp4'] and 'b.png was playing when the player stopped (1 time)' in r['stdout']
    # twice: skipped
    r = run(tmp_path, files=files, write=crashed_on('/media/internal/b.png', times_before=1), max_plays=7)
    assert plays(r)[3:7] == ['a.mp4', 'c.mp4', 'a.mp4', 'c.mp4'] and 'skipped until it is replaced' in r['stdout']
    # stopped on purpose: nothing counts
    r = run(tmp_path, files=files, write=crashed_on('/media/internal/b.png', state='stopped', times_before=1), max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'b.png', 'c.mp4']
    # chosen in the web interface, it is tried again
    r = run(tmp_path, files=files, write=crashed_on('/media/internal/b.png', times_before=1),
            signals=[{'at': 21, 'play': '/media/internal/b.png'}], max_plays=8)
    assert plays(r)[3:8] == ['a.mp4', 'b.png', 'c.mp4', 'a.mp4', 'b.png']


def test_a_player_still_running_is_not_blamed(tmp_path):
    # e.g. started a second time over SSH: the first one is still playing that file
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', '/boot/mp4museum.py'])
    try:
        r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.png'],
                write=crashed_on('/media/internal/b.png', times_before=1, pid=other.pid), max_plays=6)
    finally:
        other.kill()
        other.wait()
    assert plays(r)[3:6] == ['a.mp4', 'b.png', 'a.mp4'] and 'was playing when' not in r['stdout']


def test_everything_skipped_is_tried_again(tmp_path):
    # better than a black screen: e.g. a single loop video that stopped the player twice by chance
    r = run(tmp_path, files=['/media/internal/b.png'], write=crashed_on('/media/internal/b.png', times_before=1),
            max_plays=5)
    assert plays(r)[3:5] == ['b.png', 'b.png'] and 'trying them again' in r['stdout']


def test_stopping_on_purpose_says_so_in_the_status(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4'], signals=[{'at': 30, 'signal': 'SIGTERM'}], max_plays=20)
    assert r['statuses'][-1]['state'] == 'stopped' and r['log'][-1] == {'exit': '0'}


def test_image_that_never_ends_is_moved_on_from(tmp_path):
    # a very large image on a Pi: VLC can take far longer than the image duration
    files = ['/media/internal/a.mp4', '/media/internal/b-huge.png', '/media/internal/c.mp4']
    r = run(tmp_path, files=files, media={'b-huge.png': 'slow'}, write={'/boot/mp4m-player.txt': 'image_duration=5\n'},
            max_plays=6)
    shown = first_play(r, 'b-huge.png')['at']
    assert 25 <= first_play(r, 'c.mp4')['at'] - shown <= 27
    assert plays(r)[3:6] == ['a.mp4', 'b-huge.png', 'c.mp4']      # and shown again next time round
    # paused, it isn't moved on from
    r = run(tmp_path, files=files, media={'b-huge.png': 'slow'}, write={'/boot/mp4m-player.txt': 'image_duration=5\n'},
            signals=[{'at': 26, 'signal': 'SIGUSR2'}, {'at': 66, 'signal': 'SIGUSR2'}], max_plays=6)
    assert first_play(r, 'c.mp4')['at'] - first_play(r, 'b-huge.png')['at'] >= 60


def png_header(width, height):
    import struct
    return (b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', width, height) + b'\x08\x02\x00\x00\x00').hex()


def jpeg_header(width, height):
    import struct
    return (b'\xff\xd8' + b'\xff\xe0' + struct.pack('>H', 6) + b'JFIF' +
            b'\xff\xc0' + struct.pack('>HBHH', 11, 8, height, width) + b'\x03\x01\x11\x00').hex()


def test_images_too_large_for_a_pi_3_are_skipped(tmp_path):
    # 3300 x 2550 came out scrambled on a Pi 3B, after a long wait
    files = ['/media/internal/a.mp4', '/media/internal/b-poster.png', '/media/internal/c-photo.jpg',
             '/media/internal/d-fine.png']
    contents = {'/media/internal/b-poster.png': png_header(3300, 2550),
                '/media/internal/c-photo.jpg': jpeg_header(1200, 4000),
                '/media/internal/d-fine.png': png_header(2048, 1536)}
    pi3 = {'/proc/device-tree/model': 'Raspberry Pi 3 Model B Rev 1.2\0'}
    r = run(tmp_path, files=files, contents=contents, write=pi3, max_plays=7)
    assert plays(r)[3:7] == ['a.mp4', 'd-fine.png', 'a.mp4', 'd-fine.png']
    assert 'b-poster.png: 3300 x 2550 pixels' in r['stdout'] and 'c-photo.jpg: 1200 x 4000' in r['stdout']
    # a Pi 4 (limit not known), or not a Pi: shown
    for model in ({'/proc/device-tree/model': 'Raspberry Pi 4 Model B Rev 1.4\0'}, {}):
        r = run(tmp_path, files=files, contents=contents, write=model, max_plays=7)
        assert plays(r)[3:7] == ['a.mp4', 'b-poster.png', 'c-photo.jpg', 'd-fine.png']


# ----- Rewind: back to the first frame, held until play ----- #
def test_rewind_holds_the_first_frame(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/b.mp4']
    r = run(tmp_path, files=files, media={'a.mp4': 100},
            signals=[{'at': 50, 'command': 'rewind'}, {'at': 70, 'signal': 'SIGUSR2'}], max_plays=5)
    assert [e['set_time'] for e in r['log'] if 'set_time' in e] == [0]
    held = [s for s in r['statuses'] if s['state'] == 'paused'][0]
    assert held['file'].endswith('a.mp4') and held['position'] == 0 and held['rewind'] is True
    # held for 20 seconds, then the whole file plays from the start
    assert 169 <= first_play(r, 'b.mp4')['at'] <= 172
    # already paused: it stays paused, at the start
    r = run(tmp_path, files=files, media={'a.mp4': 100},
            signals=[{'at': 40, 'signal': 'SIGUSR2'}, {'at': 50, 'command': 'rewind'}], max_seconds=200)
    assert 'b.mp4' not in plays(r) and [s['state'] for s in r['statuses']][-1] == 'paused'


def test_rewind_a_loop_in_omxplayer(tmp_path):
    # omxplayer can't hold a frame: VLC shows the first frame until play, then omxplayer loops again
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'],
            signals=[{'at': 40, 'command': 'rewind'}, {'at': 60, 'signal': 'SIGUSR2'}], max_seconds=100)
    starts = [round(e['at']) for e in omx_starts(r)]
    assert starts[0] == 20 and 60 <= starts[1] <= 61 and len(starts) == 2
    assert killpgs(r)[0] == (2, 40)
    assert plays(r)[3:] == ['clip-loop.mp4']          # the first frame, in VLC
    held = [s for s in r['statuses'] if s['state'] == 'paused'][0]
    assert held['file'].endswith('clip-loop.mp4') and held['position'] == 0


def test_rewind_then_next_leaves_nothing_behind(tmp_path):
    # a rewind that was overtaken by next must not freeze the next loop on its first frame
    files = ['/media/internal/a-loop.mp4', '/media/internal/b.mp4']
    for first, second in (('rewind', 'next'), ('next', 'rewind')):
        events = {'rewind': {'command': 'rewind'}, 'next': {'signal': 'SIGUSR1'}}
        r = run(tmp_path, files=files, installed=['omxplayer'], omx_hangs=(first == 'next'), max_seconds=120,
                signals=[dict(events[first], at=40), dict(events[second], at=41)])
        starts = [round(e['at']) for e in omx_starts(r)]
        assert len(starts) == 2, (first, starts)
        # the loop's second omxplayer keeps playing until the run ends
        assert [at for signum, at in killpgs(r) if at > starts[1]] == [120], (first, killpgs(r))


def test_rewind_between_files_holds_the_next_one(tmp_path):
    # pressed while the player is between files: the next file waits at its first frame
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.mp4'], max_seconds=120,
            signals=[{'at': 25, 'command': 'rewind', 'when': 'settings'}])
    held = [s for s in r['statuses'] if s['state'] == 'paused']
    assert held and held[0]['file'].endswith('b.mp4') and held[0]['position'] == 0
    assert plays(r)[3:] == ['a.mp4', 'b.mp4']


def test_skipped_file_that_plays_is_forgiven(tmp_path):
    # a single video that stopped the player twice by chance: tried again, it plays on without gaps
    r = run(tmp_path, files=['/media/internal/v.mp4'], write=crashed_on('/media/internal/v.mp4', times_before=1),
            max_plays=8)
    assert plays(r)[3:8] == ['v.mp4'] * 5 and r['stdout'].count('trying them again') == 1
    assert [s['state'] for s in r['statuses']].count('idle') == 1 and r['skipped'] == []
    # stopped once, then played to the end: the count is cleared
    r = run(tmp_path, files=['/media/internal/v.mp4'], write=crashed_on('/media/internal/v.mp4'), max_plays=5)
    assert r['skipped'] == []


def test_skipping_large_images_is_logged_once(tmp_path):
    files = ['/media/internal/a.png', '/media/internal/b.png']
    contents = {f: png_header(4000, 3000) for f in files}
    r = run(tmp_path, files=files, contents=contents, write={'/proc/device-tree/model': 'Raspberry Pi 3 Model B\0'},
            max_seconds=300)
    assert r['stdout'].count('a.png: 4000 x 3000') == 1 and r['stdout'].count('b.png: 4000 x 3000') == 1


def test_large_webp_skipped_too(tmp_path):
    import struct
    vp8x = b'RIFF' + struct.pack('<I', 30) + b'WEBPVP8X' + struct.pack('<I', 10) + bytes(4) + \
        (3999).to_bytes(3, 'little') + (2999).to_bytes(3, 'little')
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.webp'],
            contents={'/media/internal/b.webp': vp8x.hex()},
            write={'/proc/device-tree/model': 'Raspberry Pi 3 Model B\0'}, max_plays=5)
    assert 'b.webp: 4000 x 3000' in r['stdout'] and 'b.webp' not in plays(r)


def test_play_pressed_while_a_rewind_is_on_its_way(tmp_path):
    # omxplayer takes a while to stop: play pressed meanwhile is for the first frame, not lost
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'], omx_hangs=True,
            signals=[{'at': 40, 'command': 'rewind'}, {'at': 41, 'signal': 'SIGUSR2'}], max_seconds=100)
    starts = [round(e['at']) for e in omx_starts(r)]
    assert len(starts) == 2 and starts[1] < 50, starts       # straight back to playing from the start
    assert [e for e in r['log'] if 'key' in e] == []          # not sent to the omxplayer being stopped
    assert not [s for s in r['statuses'] if s['state'] == 'paused']


def test_custom_boot_video_that_stops_the_player_is_replaced_by_the_original(tmp_path):
    # it plays before everything else: skipping it like the others needs doing at startup
    custom = {'/boot/mp4museum-boot.mp4': 'a video that stops the player'}
    r = run(tmp_path, files=['/media/internal/a.mp4'], write=custom,
            crashed={'file': '/boot/mp4museum-boot.mp4', 'times': 2}, max_plays=4)
    assert plays(r)[:3] == ['mp4museum-boot.mp4', 'mp4museum-boot.mp4', 'mp4m-v7beta.jpg']
    assert all(e['play'].startswith('/home/pi/') for e in r['log'] if e.get('play', '').endswith('boot.mp4'))
    # once could be chance: still played
    r = run(tmp_path, files=['/media/internal/a.mp4'], write=custom,
            crashed={'file': '/boot/mp4museum-boot.mp4', 'times': 1}, max_plays=4)
    assert not first_play(r, 'boot.mp4')['play'].startswith('/home/pi/')
