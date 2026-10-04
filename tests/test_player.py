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
    # the logo and images: the still converted once (converting it 10 times a second kept a
    # Pi 3's CPU busy)
    image = [':image-chroma=I420']
    assert first_play(r, 'mp4m-v7beta.jpg')['options'] == [':image-duration=10'] + image
    assert first_play(r, 'c.jpg')['options'] == [':image-duration=10'] + image



def test_only_media_files_are_played(tmp_path):
    # an SD card in a USB reader: the boot files of the Pi it was made for
    r = run(tmp_path, files=['/media/usb0/LICENCE.broadcom', '/media/usb0/bcm2710-rpi-2-b.dtb',
                             '/media/usb0/config.txt', '/media/usb0/Film.MOV', '/media/internal/a.mp4',
                             '/media/internal/notes.txt'], max_plays=7)
    assert plays(r)[3:7] == ['a.mp4', 'Film.MOV', 'a.mp4', 'Film.MOV']


def marquee(result):
    """What was set on VLC's marquee, in order: (option, value, the file playing then)."""
    events, playing = [], None
    for event in result['log']:
        if 'play' in event:
            playing = event['play'].split('/')[-1]
        elif 'marquee' in event:
            events.append((event['marquee'], event['value'], playing))
    return events


def test_address_on_the_logo_screen(tmp_path):
    import system
    enable, text = 0, 1
    serial = 'Serial\t\t: 00000000831b1a81\n'
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/proc/cpuinfo': serial}, max_plays=5)
    m = marquee(r)
    # the name the web interface gives the Pi (from its serial number), worked out the same way
    original, system.read_serial = system.read_serial, lambda: '00000000831b1a81'
    try:
        name = system.default_hostname()
    finally:
        system.read_serial = original
    assert (text, 'http://%s.local\n192.168.1.42' % name, 'mp4m-v7beta.jpg') in m
    # VLC only sets it on a picture being shown: on once the logo shows. Once the logo has ended,
    # VLC can't take it off any more, so VLC is stopped (that drops it) before the first file
    switched = [(e[1], e[2]) for e in m if e[0] == enable]
    assert switched == [(1, 'mp4m-v7beta.jpg'), (0, 'mp4m-v7beta.jpg')]
    on = [e['at'] for e in r['log'] if e.get('marquee') == enable][0]
    logo, first = first_play(r, 'mp4m-v7beta.jpg')['at'], first_play(r, 'a.mp4')['at']
    assert logo < on < logo + 2
    stops = [e['at'] for e in r['log'] if 'stop_call' in e]
    assert [t for t in stops if logo + 9 < t <= first] and not [t for t in stops if t > first]

    # the name set in the web interface; no serial number: from the MAC address, else mp4museum
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/hostname.txt': 'Gallery-3\n'}, max_plays=5)
    assert (text, 'http://gallery-3.local\n192.168.1.42', 'mp4m-v7beta.jpg') in marquee(r)
    r = run(tmp_path, files=['/media/internal/a.mp4'], max_plays=5)
    assert (text, 'http://mp4museum.local\n192.168.1.42', 'mp4m-v7beta.jpg') in marquee(r)

    # no address yet (the network is still coming up): looked up again until there is one
    r = run(tmp_path, files=['/media/internal/a.mp4'], addresses='', addresses_from=[[5, '192.168.1.50']],
            write={'/boot/mp4m-player.txt': 'boot_video_plays=0\n'}, max_plays=3)
    texts = [e[1] for e in marquee(r) if e[0] == text]
    assert texts == ['http://mp4museum.local', 'http://mp4museum.local\n192.168.1.50']
    lookups = [e['at'] for e in r['log'] if 'hostname -I' in e]
    assert 3 <= len(lookups) <= 5 and lookups[-1] < 7

    # two addresses at most; link-local ones (no answer from the router) left out
    r = run(tmp_path, files=['/media/internal/a.mp4'], addresses='169.254.3.4 10.0.0.5 192.168.1.42 172.17.0.1',
            max_plays=5)
    assert (text, 'http://mp4museum.local\n10.0.0.5   192.168.1.42', 'mp4m-v7beta.jpg') in marquee(r)

    # next pressed during the logo: taken off as well
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4m-player.txt': 'boot_video_plays=0\n'},
            signals=[{'at': 4, 'signal': 'SIGUSR1'}], max_plays=3)
    assert [(e[1], e[2]) for e in marquee(r) if e[0] == enable] == [(1, 'mp4m-v7beta.jpg'), (0, 'mp4m-v7beta.jpg')]
    assert plays(r)[:2] == ['mp4m-v7beta.jpg', 'a.mp4']

    # VLC can't do it: said once in the log, and playing goes on as usual
    r = run(tmp_path, files=['/media/internal/a.mp4'], no_marquee=True, max_plays=5)
    assert r['stdout'].count("couldn't show the address") == 1 and plays(r)[2:4] == ['mp4m-v7beta.jpg', 'a.mp4']
    logo, first = first_play(r, 'mp4m-v7beta.jpg')['at'], first_play(r, 'a.mp4')['at']
    assert not [e for e in r['log'] if 'stop_call' in e and logo < e['at'] <= first]

    # turned off in the web interface
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4m-player.txt': 'show_address=no\n'}, max_plays=5)
    assert marquee(r) == [] and plays(r)[2] == 'mp4m-v7beta.jpg' and 'hostname -I' not in str(r['log'])
    # and then VLC isn't stopped after the logo (as before)
    logo, first = first_play(r, 'mp4m-v7beta.jpg')['at'], first_play(r, 'a.mp4')['at']
    assert not [e for e in r['log'] if 'stop_call' in e and logo < e['at'] <= first]



def test_logo_from_the_web_interface_folder(tmp_path):
    from pathlib import Path
    logo = Path(__file__).resolve().parents[1] / 'v7-beta' / 'boot' / 'mp4m-web' / 'static' / 'logo.jpg'
    r = run(tmp_path, files=['/media/internal/a.mp4'], logo=str(logo), max_plays=4)
    # the one updates bring, 1920 x 1080: the address in proportion (24 on the old 1280 wide
    # logo, 30 from the edges), grey like the logo's 'Please Wait'
    assert plays(r)[2:4] == ['logo.jpg', 'a.mp4']
    color, size, x, y = 2, 6, 8, 9
    sizes = {e[0]: e[1] for e in marquee(r) if e[0] in (color, size, x, y)}
    assert sizes == {color: 0xB0B0B0, size: 36, x: 45, y: 45}
    # not there (installed before it existed, or removed): the old one in /home/pi
    r = run(tmp_path, files=['/media/internal/a.mp4'], max_plays=4)
    assert plays(r)[2] == 'mp4m-v7beta.jpg'
    assert {e[0]: e[1] for e in marquee(r) if e[0] in (size, x)} == {size: 24, x: 30}


def test_boot_video_plays_setting(tmp_path):
    for times in (0, 1, 2):
        r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4m-player.txt': 'boot_video_plays=%d\n' % times},
                max_plays=times + 2)
        assert plays(r)[:times + 2] == ['mp4museum-boot.mp4'] * times + ['mp4m-v7beta.jpg', 'a.mp4']
    # not set, or anything else: once (twice in the original, as a warm-up)
    for setting in ('', 'boot_video_plays=5\n'):
        r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4m-player.txt': setting},
                real_default=True, max_plays=3)
        assert plays(r)[:3] == ['mp4museum-boot.mp4', 'mp4m-v7beta.jpg', 'a.mp4']


def test_boot_video_original_or_custom(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4'], max_plays=4)
    assert [event['play'] for event in r['log'] if 'play' in event][:2] == ['/home/pi/mp4museum-boot.mp4'] * 2
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4museum-boot.mp4': 'x'}, max_plays=4)
    assert all(event['play'].endswith('custom-boot.mp4') for event in [e for e in r['log'] if 'play' in e][:2])


def test_settings(tmp_path):
    r = run(tmp_path, files=['/media/internal/still.png'],
            write={'/boot/mp4m-player.txt': 'image_duration=25\n', '/boot/alsa.txt': '12\n'}, max_plays=4)
    assert [event['instance'] for event in r['log'] if 'instance' in event][0].endswith('hw:12')
    assert first_play(r, 'still.png')['options'][0] == ':image-duration=25'

    r = run(tmp_path, files=['/media/internal/a.png'],
            write={'/boot/mp4m-player.txt': 'image_duration=²\n', '/boot/alsa.txt': 'auto'}, max_plays=4)
    assert [event['instance'] for event in r['log'] if 'instance' in event][0].endswith('hw:0')
    assert first_play(r, 'a.png')['options'][0] == ':image-duration=10'


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
    assert [e['pause'] for e in r['log'] if 'pause' in e] == [True, False]
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
    # (only after the logo, at start-up, to take the address off)
    assert [e['stop_call'] for e in stop_calls(r)] == ['/home/pi/mp4m-v7beta.jpg']


def test_vlc_stopped_when_skipped_and_when_idle(tmp_path):
    r = run(tmp_path, files=['/media/internal/a.mp4', '/media/internal/b.mp4'], media={'a.mp4': 100},
            signals=[{'at': 30, 'signal': 'SIGUSR1'}], max_plays=6)
    assert [e['stop_call'] for e in stop_calls(r)][:2] == ['/home/pi/mp4m-v7beta.jpg', '/media/internal/a.mp4']
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



def test_previous_file(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4', '/media/usb0/d.mp4']
    media = {name: 100 for name in ('a.mp4', 'b.mp4', 'c.mp4', 'd.mp4')}

    def previous(*at):
        return [{'at': t, 'command': 'previous'} for t in at]
    # a.mp4 starts at about 20 s: the one before it is the last (the playlist repeats), then the
    # files after that one again
    r = run(tmp_path, files=files, media=media, signals=previous(30, 40), max_plays=7)
    assert plays(r)[3:7] == ['a.mp4', 'd.mp4', 'c.mp4', 'd.mp4']
    assert 30 <= first_play(r, 'd.mp4')['at'] <= 31 and 40 <= first_play(r, 'c.mp4')['at'] <= 41
    # pressed twice before the next file starts: two back
    r = run(tmp_path, files=files, media=media, signals=previous(30, 30.001), max_plays=5)
    assert plays(r)[3:5] == ['a.mp4', 'c.mp4']
    # in the second round (a.mp4 again from 40 s): across the end to d.mp4, then on from there
    r = run(tmp_path, files=files, signals=previous(41.5), max_plays=9)
    assert plays(r)[3:10] == ['a.mp4', 'b.mp4', 'c.mp4', 'd.mp4', 'a.mp4', 'd.mp4', 'a.mp4']
    # not during start-up (there's nothing before the boot video and logo): ignored, and the
    # status says so
    r = run(tmp_path, files=files, signals=previous(3, 14), max_plays=5)
    assert plays(r)[:5] == ['mp4museum-boot.mp4', 'mp4museum-boot.mp4', 'mp4m-v7beta.jpg', 'a.mp4', 'b.mp4']
    assert {s['previous'] for s in r['statuses'] if s['file'] and not s['file'].startswith('/media/')} == {False}
    assert {s['previous'] for s in r['statuses'] if s['file'] and s['file'].startswith('/media/')} == {True}


def test_previous_passes_over_files_the_player_skips(tmp_path):
    # b-poster.png is too large for a Pi 3 and c.mp4 has stopped the player twice: from d.mp4,
    # back to a.mp4
    files = ['/media/internal/a.mp4', '/media/internal/b-poster.png', '/media/internal/c.mp4', '/media/internal/d.mp4']
    r = run(tmp_path, files=files, media={'a.mp4': 5, 'd.mp4': 100}, contents={'/media/internal/b-poster.png': png_header(3300, 2550)},
            write={'/proc/device-tree/model': 'Raspberry Pi 3 Model B Rev 1.2\0'},
            crashed={'file': '/media/internal/c.mp4', 'times': 2}, signals=[{'at': 40, 'command': 'previous'}], max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'd.mp4', 'a.mp4'] and 40 <= [e['at'] for e in r['log'] if 'play' in e][5] <= 41


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


def bashrc_autostart(tmp_path, exit_codes, output=0, last_line=None):
    """Run the autostart part of .bashrc with a fake python3 that exits with these codes in turn
    (printing output bytes each time). Returns how often it ran, the log and the waits between."""
    tmp_path.mkdir(exist_ok=True)
    bashrc = (Path(__file__).parents[1] / 'v7-beta' / 'home' / 'pi' / '.bashrc').read_text()
    start = bashrc.index('# mp4museum autostart')
    block = bashrc[start:bashrc.index('setterm -cursor on', start)].replace('/tmp/mp4museum.log', str(tmp_path / 'log'))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    (tmp_path / 'codes').write_text(' '.join(map(str, exit_codes)))
    fakes = {'python3': 'set -- $(cat "%s"); echo "run $*" >> "%s"; echo "${@:2}" > "%s"; head -c %d /dev/zero | tr "\\0" x; %s exit $1'
                        % (tmp_path / 'codes', tmp_path / 'runs', tmp_path / 'codes', output,
                           'echo; echo "%s";' % last_line if last_line else ''),
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
    # started again (on the Pi VLC was slow to play on after jumping back), without stopping
    # it first (black frames), and paused as soon as its first picture is shown
    assert plays(r)[3:5] == ['a.mp4', 'a.mp4'] and 50 <= [e['at'] for e in r['log'] if 'play' in e][4] <= 51
    assert not [e for e in r['log'] if 'stop_call' in e and 30 < e.get('at', 50) < 70] and 'set_time' not in str(r['log'])
    held = [s for s in r['statuses'] if s['state'] == 'paused'][0]
    assert held['file'].endswith('a.mp4') and held['position'] < .1 and held['rewind'] is True
    # held for 20 seconds, then the whole file plays from the start
    assert 169 <= first_play(r, 'b.mp4')['at'] <= 172
    # not offered for the boot video and logo
    playlist = [i for i, s in enumerate(r['statuses']) if s['file'] and s['file'].startswith('/media/')][0]
    assert playlist > 0 and not any(s['rewind'] for s in r['statuses'][:playlist])
    assert all(s['rewind'] is True for s in r['statuses'][playlist:])
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
    assert held['file'].endswith('clip-loop.mp4') and held['position'] == 0 and held['engine'] == 'vlc'
    # omxplayer starts in front of the held frame (no black background), then VLC lets go of it
    first, second = omx_starts(r)
    assert '-b' in first['omxplayer'] and '-b' not in second['omxplayer']
    let_go = [e['at'] for e in r['log'] if 'stop_call' in e and e['stop_call'].endswith('clip-loop.mp4')]
    assert second['at'] + 1.4 <= let_go[-1] <= second['at'] + 1.7 and [s['engine'] for s in r['statuses'] if s['state'] == 'playing'][-1] == 'omxplayer'


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
    assert held and held[0]['file'].endswith('b.mp4') and held[0]['position'] < .1
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


def test_boot_video_forgiven_only_after_both_plays(tmp_path):
    # it can stop the player on its second play: the first finishing doesn't clear the count
    custom = {'/boot/mp4museum-boot.mp4': 'a video'}
    r = run(tmp_path, files=['/media/internal/a.mp4'], write=custom, crashed={'file': '/boot/mp4museum-boot.mp4', 'times': 1},
            signals=[{'at': 8, 'signal': 'SIGTERM'}], max_plays=10)     # stopped during the second play
    assert [e['play'].split('/')[-1] for e in r['log'] if 'play' in e][:2] == ['custom-boot.mp4'] * 2
    assert len(r['skipped']) == 1 and r['skipped'][0][2] == 1
    # both played: forgiven
    r = run(tmp_path, files=['/media/internal/a.mp4'], write=custom, crashed={'file': '/boot/mp4museum-boot.mp4', 'times': 1},
            max_plays=4)
    assert r['skipped'] == []


def test_bashrc_ctrl_c_with_a_player_script_from_before(tmp_path):
    # an edited player kept by install.sh, without the quit handlers: Python 3.7 exits with 1 and a
    # KeyboardInterrupt traceback on Ctrl-C (3.8 and later with 130). Either way it's a stop on purpose.
    runs, log, sleeps = bashrc_autostart(tmp_path, [1, 0], output=0, last_line='KeyboardInterrupt')
    assert runs == 1 and 'starting it again' not in log
    runs, log, sleeps = bashrc_autostart(tmp_path / 'b', [130, 0])
    assert runs == 1


def test_rewind_a_loop_in_vlc(tmp_path):
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], media={'clip-loop.mp4': 30},
            write={'/boot/mp4m-player.txt': 'loop_player=vlc\n'},
            signals=[{'at': 40, 'command': 'rewind'}, {'at': 60, 'signal': 'SIGUSR2'}], max_seconds=150)
    starts = [round(e['at']) for e in r['log'] if e.get('play', '').endswith('clip-loop.mp4')]
    # pass at 20, started again at 40 and held until 60, then 30 s passes again
    assert starts[:4] == [20, 40, 90, 120], starts
    held = [s for s in r['statuses'] if s['state'] == 'paused']
    assert held[0]['engine'] == 'vlc' and held[0]['position'] < .1


def test_rewind_again_while_held(tmp_path):
    # the rewind button stays on while the first frame is held: pressing it again mustn't stop play from working
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'],
            signals=[{'at': 40, 'command': 'rewind'}, {'at': 50, 'command': 'rewind'},
                     {'at': 60, 'signal': 'SIGUSR2'}], max_seconds=100)
    starts = [round(e['at']) for e in omx_starts(r)]
    assert len(starts) == 2 and 60 <= starts[1] <= 61, starts


def test_play_pressed_for_a_file_that_fails_is_forgotten(tmp_path):
    # rewind between files, play pressed, the file doesn't open: the next rewind still holds
    files = ['/media/internal/a.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4']
    r = run(tmp_path, files=files, media={'b.mp4': 'stuck', 'c.mp4': 100}, max_seconds=150,
            signals=[{'at': 25, 'command': 'rewind', 'when': 'settings'}, {'at': 30, 'signal': 'SIGUSR2'},
                     {'at': 90, 'command': 'rewind'}])
    assert 'c.mp4' in plays(r)
    held = [s for s in r['statuses'] if s['state'] == 'paused']
    assert held and held[-1]['file'].endswith('c.mp4')


def test_status_says_which_program_loops_it(tmp_path):
    # while an omxplayer loop's first frame is held, VLC shows it but the loop is still omxplayer's
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4', '/media/internal/b.mp4'], installed=['omxplayer'],
            signals=[{'at': 40, 'command': 'rewind'}], max_seconds=80)
    held = [s for s in r['statuses'] if s['state'] == 'paused'][0]
    assert held['engine'] == 'vlc' and held['loop_player'] == 'omxplayer'
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4', '/media/internal/b.mp4'],
            write={'/boot/mp4m-player.txt': 'loop_player=vlc\n'}, max_plays=6)
    assert {s['loop_player'] for s in r['statuses'] if (s['file'] or '').endswith('clip-loop.mp4')} == {'vlc'}
    assert {s['loop_player'] for s in r['statuses'] if (s['file'] or '').endswith('b.mp4')} == {None}


def test_status_says_whether_omxplayer_could_loop_it(tmp_path):
    # for the web interface: choosing omxplayer only changes anything for loops it can play
    for name, codec, ok in (('clip-loop.mp4', 'h264', True), ('clip-loop.mp4', 'hevc', False), ('clip-loop.webm', 'h264', False)):
        r = run(tmp_path, files=['/media/internal/' + name], installed=['omxplayer'], omx_codec=codec,
                write={'/boot/mp4m-player.txt': 'loop_player=vlc\n'}, max_plays=5)
        assert {s['loop_omx_ok'] for s in r['statuses'] if s['file'] == '/media/internal/' + name} == {ok}, (name, codec)
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], write={'/boot/mp4m-player.txt': 'loop_player=vlc\n'}, max_plays=5)
    assert {s['loop_omx_ok'] for s in r['statuses'] if s['file'] == '/media/internal/clip-loop.mp4'} == {False}


# ----- The button on pin 13: once next, twice previous, held back to the start ----- #
BUTTON_FILES = ['/media/internal/a.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4', '/media/usb0/d.mp4']
BUTTON_MEDIA = {name: 100 for name in ('a.mp4', 'b.mp4', 'c.mp4', 'd.mp4')}


def test_button_pressed_once_twice_or_held(tmp_path):
    # a.mp4 plays from about 20 s. Pressed once: next, once it's clear no second press follows
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, .1]], max_plays=5)
    assert plays(r)[3:5] == ['a.mp4', 'b.mp4'] and 30.4 <= first_play(r, 'b.mp4')['at'] <= 31
    # twice within 0.4 s: previous (from the first, the last)
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, .1], [30.3, .1]], max_plays=5)
    assert plays(r)[3:5] == ['a.mp4', 'd.mp4'] and 30.3 <= first_play(r, 'd.mp4')['at'] <= 31
    # twice, but further apart: next twice, including just after the 0.4 s
    for second in (32, 30.55):
        r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, .1], [second, .1]], max_plays=6)
        assert plays(r)[3:6] == ['a.mp4', 'b.mp4', 'c.mp4'] and first_play(r, 'c.mp4')['at'] < second + 1
    # held: back to the start of a.mp4, held at its first frame; pressed once: it plays on (no next)
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, 1.5], [40, .1]], max_seconds=60)
    assert plays(r)[3:] == ['a.mp4', 'a.mp4']
    # sent at 1 s, while it's still held (not when it's let go). (Here the reader runs in the
    # player's loop, which only gets the signal once it is let go at 31.5 s; on the Pi it is a thread)
    assert [e['at'] for e in r['log'] if 'button_signal' in e][0] <= 31.05
    restarts = [e['at'] for e in r['log'] if e.get('play', '').endswith('a.mp4')]
    assert len(restarts) == 2 and 31 <= restarts[1] <= 31.6
    states = [(s['state'], round(s['position'] or 0, 1)) for s in r['statuses'] if s['file'].endswith('a.mp4')]
    assert ('paused', 0) in states and states[-1][0] == 'playing'
    resumed = [e['at'] for e in r['log'] if e.get('set_pause') == 0 or e.get('pause') is False]
    assert resumed and 40.4 <= resumed[-1] <= 41


def test_button_pressed_once_while_paused_plays_on(tmp_path):
    # paused from the web interface: one press plays on, as when held at the first frame
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, signals=[{'at': 25, 'signal': 'SIGUSR2'}],
            button=[[30, .1]], max_seconds=60)
    assert plays(r)[3:] == ['a.mp4'] and [s['state'] for s in r['statuses']][-1] == 'playing'


def test_button_ignores_blips(tmp_path):
    # shorter than a press (switch bounce, interference, static): nothing happens
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, .01], [35, .02]], max_seconds=60)
    assert plays(r)[3:] == ['a.mp4']
    # the bounce of a press isn't taken for a second press
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, .1], [30.12, .01], [30.15, .01]], max_plays=5)
    assert plays(r)[3:5] == ['a.mp4', 'b.mp4']


def test_button_held_on_an_omxplayer_loop(tmp_path):
    # as the rewind button: the first frame in VLC, then omxplayer loops again on a press
    r = run(tmp_path, files=['/media/internal/clip-loop.mp4'], installed=['omxplayer'],
            button=[[40, 1.5], [60, .1]], max_seconds=100)
    starts = [round(e['at']) for e in omx_starts(r)]
    assert starts[0] == 20 and 60 <= starts[1] <= 62 and len(starts) == 2
    held = [s for s in r['statuses'] if s['state'] == 'paused'][0]
    assert held['file'].endswith('clip-loop.mp4') and held['position'] == 0 and held['engine'] == 'vlc'


def test_button_press_meant_for_the_file_playing_when_it_began(tmp_path):
    # a.mp4 plays from 20 s to 35 s. Pressed at 34.5 s, but only known to be one press at about
    # 35 s, when b.mp4 has started by itself (held back here to 36 s): that was the next file
    # already, so b.mp4 isn't skipped
    media = dict(BUTTON_MEDIA, **{'a.mp4': 15})
    r = run(tmp_path, files=BUTTON_FILES, media=media, button=[[34.5, .1]], button_delay=1, max_seconds=60)
    assert plays(r)[3:] == ['a.mp4', 'b.mp4']
    # twice: the one before a.mp4 (d.mp4), not before b.mp4
    r = run(tmp_path, files=BUTTON_FILES, media=media, button=[[34.5, .1], [34.7, .1]], button_delay=1, max_plays=5)
    assert plays(r)[3:6] == ['a.mp4', 'b.mp4', 'd.mp4']
    # held: back to a.mp4 (the file it was held on), not b.mp4 held
    r = run(tmp_path, files=BUTTON_FILES, media=media, button=[[33.5, 2]], button_delay=2, max_plays=5)
    assert plays(r)[3:6] == ['a.mp4', 'b.mp4', 'a.mp4']
    # not moved on: as usual
    r = run(tmp_path, files=BUTTON_FILES, media=BUTTON_MEDIA, button=[[30, .1]], button_delay=1, max_plays=5)
    assert plays(r)[3:5] == ['a.mp4', 'b.mp4'] and 31 <= first_play(r, 'b.mp4')['at'] <= 32


# ----- Files switched off in the web interface ----- #
def test_switched_off_files_are_left_out(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/b.mp4', '/media/internal/c.mp4', '/media/usb0/d.mp4']
    off = {'/boot/mp4m-disabled.txt': '/media/internal/b.mp4\n/media/usb0/d.mp4\n'}
    r = run(tmp_path, files=files, write=off, max_plays=7)
    assert plays(r)[3:7] == ['a.mp4', 'c.mp4', 'a.mp4', 'c.mp4']
    # previous passes over them too: from c.mp4 back to a.mp4
    r = run(tmp_path, files=files, media={'a.mp4': 5, 'c.mp4': 100}, write=off,
            signals=[{'at': 40, 'command': 'previous'}], max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'c.mp4', 'a.mp4']
    # saved on Windows (text files are read with universal newlines)
    r = run(tmp_path, files=files, write={'/boot/mp4m-disabled.txt': '/media/internal/b.mp4\r\n'}, max_plays=6)
    assert plays(r)[3:6] == ['a.mp4', 'c.mp4', 'd.mp4']
    # all switched off: nothing to play, as with no files (no spinning through the list)
    r = run(tmp_path, files=['/media/internal/a.mp4'], write={'/boot/mp4m-disabled.txt': '/media/internal/a.mp4\n'},
            max_seconds=60)
    assert plays(r)[3:] == [] and r['statuses'][-1]['state'] == 'idle'


def test_switched_off_sync_file_starts_no_sync_mode(tmp_path):
    files = ['/media/internal/a.mp4', '/media/internal/sync.mp4', '/media/internal/sync-leader.txt']
    r = run(tmp_path, files=files, installed=['omxplayer-sync'],
            write={'/boot/mp4m-disabled.txt': '/media/internal/sync.mp4\n'}, max_plays=5)
    assert not [e for e in r['log'] if 'run' in e] and 'sync.mp4 is switched off: no sync mode' in r['stdout']
    assert 'a.mp4' in plays(r) and 'sync.mp4' not in plays(r)
    # not switched off: sync mode, as before
    r = run(tmp_path, files=files, installed=['omxplayer-sync'], max_plays=5)
    assert [e['run'][0] for e in r['log'] if 'run' in e] == ['omxplayer-sync']
