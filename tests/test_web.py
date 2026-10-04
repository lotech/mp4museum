"""Web interface: login, media files, settings, player controls.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import io
import json
import os
import shutil
import signal
import threading
import time
from pathlib import Path

import pytest

import system
import webservice

REPO_PLAYER = Path(__file__).resolve().parents[1] / 'v7-beta' / 'boot' / 'mp4museum.py'


def upload(client, name, data=b'video data', **kwargs):
    return client.post('/upload', data={'file': (io.BytesIO(data), name)},
                       content_type='multipart/form-data', **kwargs)


def media_files(pi):
    return sorted(os.listdir(str(pi.media)))


# ----- Login ----- #
def test_pages_need_login(pi):
    c = webservice.app.test_client()
    r = c.get('/')
    assert r.status_code == 302 and '/login' in r.location
    r = c.post('/reboot')
    assert r.status_code == 302 and ['reboot'] not in pi.commands


def test_page_titles_name_the_player(pi, client, monkeypatch):
    # to tell players apart in a browser's tabs
    monkeypatch.setattr(webservice.socket, 'gethostname', lambda: 'gallery-3')
    assert '<title>gallery-3 - MP4Museum</title>' in client.get('/').data.decode()
    assert '<title>gallery-3 - Reboot - MP4Museum</title>' in client.get('/confirm_reboot').data.decode()
    assert '<title>gallery-3 - Log in - MP4Museum</title>' in webservice.app.test_client().get('/login').data.decode()
    # after a change of name, the page at the old address names the new one: the tab moves there
    r = client.post('/set_hostname', data={'hostname': 'gallery-4'}, base_url='http://gallery-3.local')
    assert '<title>gallery-4 - Network name changed - MP4Museum</title>' in r.data.decode()


def test_wrong_password_refused(pi):
    r = webservice.app.test_client().post('/login', data={'password': 'wrong'})
    assert b'Wrong password' in r.data


def test_index_shows_footer_and_defaults(client):
    html = client.get('/').data.decode()
    assert 'mp4museum.org' in html and 'github.com/lotech/mp4museum' in html and 'JuliusCode/MP4MUSEUM' in html
    assert 'Enable Write Access' not in html
    assert 'still the default' in html


def test_javascript_requests_get_401_when_logged_out(pi):
    c = webservice.app.test_client()
    r = c.post('/save_script', data={'script_content': 'x'}, headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 401
    assert open(system.SCRIPT_FILE).read() != 'x'
    assert c.post('/reboot', headers={'X-Requested-With': 'fetch'}).status_code == 401


def test_static_files_load_without_login(pi):
    c = webservice.app.test_client()
    r = c.get('/static/style.css')
    assert r.status_code == 200 and b'--surface' in r.data
    assert c.get('/static/mp4museum.js').status_code == 200
    # the icons, used on the login page too
    r = c.get('/static/icons.svg')
    assert r.status_code == 200 and r.mimetype == 'image/svg+xml' and b'<symbol id="play"' in r.data
    assert b'href="/static/style.css"' in c.get('/login').data


# ----- Password ----- #
def test_change_password(pi, client):
    other = webservice.app.test_client()
    other.post('/login', data={'password': 'mp4museum'})
    assert other.get('/').status_code == 200

    client.post('/set_password', data={'current_password': 'mp4museum', 'new_password': 'a', 'confirm_password': 'b'})
    assert not os.path.exists(system.PASSWORD_FILE)

    client.post('/set_password', data={'current_password': 'mp4museum', 'new_password': 's3cret', 'confirm_password': 's3cret'})
    assert 's3cret' not in open(system.PASSWORD_FILE).read()
    # this browser stays logged in, every other one is logged out
    assert client.get('/').status_code == 200
    assert other.get('/').status_code == 302
    assert b'Wrong' in other.post('/login', data={'password': 'mp4museum'}).data
    assert other.post('/login', data={'password': 's3cret'}).status_code == 302


def test_logout(client):
    client.post('/logout')
    assert client.get('/').status_code == 302


# ----- Media files ----- #
def test_upload(pi, client):
    upload(client, 'Café clip 01.mp4')
    assert media_files(pi) == ['Café clip 01.mp4']
    assert (pi.media / 'Café clip 01.mp4').read_bytes() == b'video data'
    assert pi.mounts() == [['mount', '-o', 'remount,rw', str(pi.media)], ['mount', '-o', 'remount,ro', str(pi.media)]]


def test_upload_waits_for_a_card_an_update_or_a_reboot(pi, client, monkeypatch):
    # one holds the lock (here another process): no upload; a card being made counted the media
    # files before copying them
    other = system.try_busy_lock()
    r = upload(client, 'late.mp4', follow_redirects=True)
    assert 'A card is being made, an update installed or the player rebooting' in r.data.decode()
    assert media_files(pi) == []
    os.close(other)
    # and an upload holds it (shared) until its file is in place
    held = []
    real = os.replace
    monkeypatch.setattr(system.os, 'replace', lambda src, dst: (held.append(system.try_busy_lock()), real(src, dst)))
    upload(client, 'clip.mp4')
    assert held == [None] and media_files(pi) == ['clip.mp4']
    lock = system.try_busy_lock()
    assert lock is not None
    os.close(lock)


def test_upload_large_file_streams_to_disk(pi, client):
    upload(client, 'big.mp4', b'x' * 200 * 1024)
    assert (pi.media / 'big.mp4').stat().st_size == 200 * 1024


def test_upload_unicode_space_names(pi, client):
    name = 'Screen Recording 2024-01-01 at 10.00.00 AM.mov'
    upload(client, name)
    assert media_files(pi) == [name]


def test_upload_invalid_names_refused(pi, client):
    for bad in ['.hidden.mp4', 'a:b.mp4', 'a?.mp4']:
        upload(client, bad)
    assert media_files(pi) == []


def test_upload_without_space_refused(pi, client, monkeypatch):
    monkeypatch.setattr(system, 'get_free_space', lambda: 10)
    r = upload(client, 'toolarge.mp4', b'x' * 1000, follow_redirects=True)
    assert b'Not enough free space' in r.data and media_files(pi) == []


def test_upload_without_declared_size_refused(pi, client):
    body = (b'--xyz\r\nContent-Disposition: form-data; name="file"; filename="chunked.mp4"\r\n'
            b'Content-Type: application/octet-stream\r\n\r\n' + b'x' * 100 + b'\r\n--xyz--\r\n')
    r = client.post('/upload', input_stream=io.BytesIO(body), content_type='multipart/form-data; boundary=xyz',
                    environ_overrides={'CONTENT_LENGTH': '', 'HTTP_TRANSFER_ENCODING': 'chunked', 'wsgi.input_terminated': True},
                    follow_redirects=True)
    assert b'say how big' in r.data and media_files(pi) == [] and pi.mounts() == []


def test_upload_failure_cleans_up(pi, client, monkeypatch):
    real_replace = os.replace
    monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk error')))
    r = upload(client, 'fail.mp4', b'x' * 5000, follow_redirects=True)
    monkeypatch.setattr(os, 'replace', real_replace)
    assert b'File upload failed' in r.data and media_files(pi) == []
    assert pi.mounts()[-1] == ['mount', '-o', 'remount,ro', str(pi.media)]


def test_stale_uploads_removed_before_space_check(pi, client, monkeypatch):
    stale = pi.media / '.upload-crashed'
    stale.write_text('x')
    os.utime(str(stale), (0, 0))
    fresh = pi.media / '.upload-in-progress'
    fresh.write_text('x')
    monkeypatch.setattr(system, 'get_free_space', lambda: 100 if stale.exists() else 100000)
    upload(client, 'after-crash.mp4', b'x' * 5000)
    assert not stale.exists() and fresh.exists() and (pi.media / 'after-crash.mp4').exists()


def test_concurrent_uploads_share_free_space(pi, client, monkeypatch):
    written = {'bytes': 6000}
    monkeypatch.setattr(system, 'get_free_space', lambda: 20000 - written['bytes'])
    with system.upload_space(8000, lambda: written['bytes']):
        # the other upload still needs 2000 of the 14000 free
        upload(client, 'fits.mp4', b'x' * 10000)
        r = upload(client, 'too-big.mp4', b'x' * 13000, follow_redirects=True)
    assert (pi.media / 'fits.mp4').exists()
    assert b'Not enough free space' in r.data and not (pi.media / 'too-big.mp4').exists()
    assert system._active_uploads == []


def test_file_names_escaped_in_page(pi, client):
    (pi.media / "it's <b>.mp4").write_text('x')
    html = client.get('/').data.decode()
    assert '<b>.mp4' not in html and 'value="it&#39;s &lt;b&gt;.mp4"' in html


def test_delete(pi, client):
    (pi.media / 'a.mp4').write_text('x')
    assert client.get('/delete/a.mp4').status_code in (404, 405)
    client.post('/delete', data={'filename': 'a.mp4'})
    assert media_files(pi) == []
    assert pi.mounts()[-1] == ['mount', '-o', 'remount,ro', str(pi.media)]


def test_delete_outside_media_refused(pi, client):
    client.post('/delete', data={'filename': '../boot/config.txt'})
    assert os.path.exists(system.CONFIG_FILE)


def test_delete_refused_when_media_not_mounted(pi, client, monkeypatch):
    (pi.media / 'keep.mp4').write_text('x')
    monkeypatch.setattr(system, 'media_available', lambda: False)
    client.post('/delete', data={'filename': 'keep.mp4'})
    assert (pi.media / 'keep.mp4').exists()


def test_download(pi, client):
    (pi.media / 'a.mp4').write_text('x')
    r = client.get('/download/a.mp4')
    assert r.status_code == 200 and r.data == b'x'


# ----- Reboot ----- #
def test_reboot_needs_post(pi, client):
    assert client.get('/reboot').status_code == 405
    client.post('/reboot')
    assert pi.commands == [['reboot']]


def test_confirm_reboot_page_shows_result(pi, client):
    client.post('/set_sound_device', data={'device': '3'})
    r = client.get('/confirm_reboot')
    assert b'Reboot Now' in r.data and b'Sound device set to 3.' in r.data


# ----- Video presets ----- #
def active_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]


def test_video_preset_changes_only_video_lines(pi, client):
    before = open(system.CONFIG_FILE).read()
    assert 'selected>1920x1080 FullHD 60fps' in client.get('/').data.decode()
    client.post('/set_video_mode', data={'mode': 'pal'})
    after = open(system.CONFIG_FILE).read()
    assert sorted(set(active_lines(before)) - set(active_lines(after))) == ['hdmi_group=1', 'hdmi_ignore_cec=1', 'hdmi_mode=16']
    assert 'sdtv_mode=2' in active_lines(after)
    assert 'disable_splash=1' in after and 'dtoverlay=vc4-fkms-v3d' in after
    # under [all], after the [pi4] section, so it applies to every Pi
    assert after.index('[all]\nenable_tvout=1') > after.index('[pi4]')
    assert system.get_current_video_mode(after)[0] == 'pal'

    client.post('/set_video_mode', data={'mode': 'pal'})
    assert open(system.CONFIG_FILE).read() == after

    client.post('/set_video_mode', data={'mode': '4k60'})
    after4k = open(system.CONFIG_FILE).read()
    assert system.get_current_video_mode(after4k)[0] == '4k60'
    assert not any(line.startswith('sdtv_mode') for line in active_lines(after4k))

    client.post('/set_video_mode', data={'mode': 'auto'})
    auto = open(system.CONFIG_FILE).read()
    assert system.get_current_video_mode(auto)[0] == 'auto' and 'mp4museum video mode' not in auto


def test_video_preset_with_end_marker_deleted_loses_nothing():
    broken = 'gpu_mem=128\n' + system.VIDEO_BLOCK_START + '\n[all]\nsdtv_mode=2\nmy_setting=1\n[pi4]\ndtoverlay=vc4-fkms-v3d\n'
    fixed = system.set_video_mode_in_config(broken, '1080p60')
    assert 'my_setting=1' in fixed and 'dtoverlay=vc4-fkms-v3d' in fixed and 'sdtv_mode=2' not in fixed
    assert fixed.count(system.VIDEO_BLOCK_START) == 1


def test_custom_video_settings_not_shown_as_auto(pi, client):
    open(system.CONFIG_FILE, 'w').write('gpu_mem=128\nhdmi_group=1\nhdmi_mode=4\n')
    html = client.get('/').data.decode()
    assert 'Custom: hdmi_group=1, hdmi_mode=4' in html and 'value="" selected disabled' in html


# ----- Sound ----- #
def test_sound_card(pi, client):
    client.post('/set_sound_device', data={'device': '12'})
    assert open(system.ALSA_FILE).read() == '12'
    client.post('/set_sound_device', data={'device': '100'})
    assert open(system.ALSA_FILE).read() == '12'
    client.post('/set_sound_device', data={'device': 'auto'})
    assert not os.path.exists(system.ALSA_FILE)


# ----- Player script ----- #
def test_save_script_with_unix_line_endings(client):
    r = client.post('/save_script', data={'script_content': 'print(1)\r\nprint(2)\r\n'}, headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 200 and open(system.SCRIPT_FILE).read() == 'print(1)\nprint(2)\n'


# ----- Network name ----- #
def test_network_name(pi, client):
    client.post('/set_hostname', data={'hostname': '-bad-'})
    assert not os.path.exists(system.HOSTNAME_FILE)

    r = client.post('/set_hostname', data={'hostname': 'Gallery-Left'}, base_url='http://mp4museum.local')
    assert open(system.HOSTNAME_FILE).read().strip() == 'gallery-left' and pi.hostnames[-1] == 'gallery-left'
    # the old address stops working: the page has its CSS inline and links to the new one
    assert b'http://gallery-left.local/' in r.data and b'<style>' in r.data and b'/static/style.css' not in r.data

    client.post('/set_hostname', data={'hostname': ''})
    assert not os.path.exists(system.HOSTNAME_FILE) and pi.hostnames[-1] == system.default_hostname()


def test_default_network_name(monkeypatch, tmp_path):
    assert system.default_hostname().startswith('mp4museum')
    monkeypatch.setattr(system, 'read_serial', lambda: '')
    net = tmp_path / 'net'
    (net / 'enxb827eb4e4fd4').mkdir(parents=True)
    (net / 'enxb827eb4e4fd4' / 'address').write_text('b8:27:eb:4e:4f:d4\n')
    monkeypatch.setattr(system, 'NET_PATH', str(net))
    monkeypatch.setattr(system, 'read_mac', lambda i: (net / i / 'address').read_text().strip())
    import hashlib
    assert system.default_hostname() == 'mp4museum-' + hashlib.sha256(b'b8:27:eb:4e:4f:d4').hexdigest()[:4]


# ----- Network section ----- #
IP_OUTPUT = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
1: lo    inet6 ::1/128 scope host \\       valid_lft forever preferred_lft forever
2: enxb827eb4e4fd4    inet 192.168.1.120/24 brd 192.168.1.255 scope global dynamic enxb827eb4e4fd4\\       valid_lft 85000sec
2: enxb827eb4e4fd4    inet6 fe80::1234:5678:9abc:def0/64 scope link \\       valid_lft forever preferred_lft forever"""


def test_network_section_lists_every_interface(pi, client, monkeypatch, tmp_path):
    net = tmp_path / 'net'
    for name, mac, state, wireless in (('enxb827eb4e4fd4', 'b8:27:eb:4e:4f:d4', 'up', False),
                                       ('wlan0', 'b8:27:eb:11:22:33', 'down', True),
                                       ('lo', '00:00:00:00:00:00', 'unknown', False)):
        (net / name).mkdir(parents=True)
        if wireless:
            (net / name / 'wireless').mkdir()
        (net / name / 'address').write_text(mac + '\n')
        (net / name / 'operstate').write_text(state + '\n')
    monkeypatch.setattr(system, 'NET_PATH', str(net))
    monkeypatch.setattr(system, 'read_mac', lambda i: (net / i / 'address').read_text().strip())
    monkeypatch.setattr(system, 'run_command', lambda cmd, timeout=None: (True, IP_OUTPUT) if cmd[:2] == ['ip', '-o'] else pi.run_command(cmd))
    card = client.get('/').data.decode().split('id="networkStatus">')[1].split('</section>')[0]
    wired = card.split('enxb827eb4e4fd4</span>')[1].split('wlan0</span>')[0]
    assert '<span class="badge on">Connected</span>' in wired and '192.168.1.120/24' in wired
    assert 'b8:27:eb:4e:4f:d4' in wired and 'fe80::1234:5678:9abc:def0/64' in wired
    wireless = card.split('wlan0</span>')[1]
    assert 'Not connected' in wireless and '<dt>IP address</dt><dd>None</dd>' in wireless
    assert '127.0.0.1' not in card and 'lo</span>' not in card


# ----- Player status and controls ----- #
def write_status(state, file=None, pid=4242, since=None):
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': state, 'file': file, 'since': since or time.time(), 'pid': pid}, f)


def test_player_not_running(client):
    r = client.get('/player/status').get_json()
    assert r['running'] is False and 'not running' in r['text']
    assert b'player is not running' in client.post('/player/next', follow_redirects=True).data


def test_player_status_and_controls(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    monkeypatch.setattr(webservice.time, 'sleep', lambda s: None)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    write_status('playing', '/media/internal/intro.mp4', since=time.time() - 130)

    assert client.get('/player/status').get_json()['text'] == 'Playing intro.mp4 (internal) for 2 min'
    html = client.get('/').data.decode()
    assert 'intro.mp4' in html and 'Pause or resume' in html

    client.post('/player/next')
    client.post('/player/pause')
    r = client.post('/player/next', headers={'X-Requested-With': 'fetch'})
    assert sent == [(4242, signal.SIGUSR1), (4242, signal.SIGUSR2), (4242, signal.SIGUSR1)]
    assert r.get_json()['running'] is True


def test_player_controls_refused_when_they_cant_work(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    write_status('playing', '/x.mp4', pid=999)   # not the player's pid
    client.post('/player/next')
    write_status('idle')
    assert 'Nothing to play' in client.get('/player/status').get_json()['text']
    assert b'Nothing is playing' in client.post('/player/next', follow_redirects=True).data
    write_status('sync', '/media/usb0/sync.mp4')
    assert b'sync mode' in client.post('/player/pause', follow_redirects=True).data
    assert sent == []


def test_real_player_pid_check():
    assert not system._is_player_process(os.getpid())
    assert not system._is_player_process('x') and not system._is_player_process(None)


def test_image_duration(client):
    assert system.get_image_duration() == 10
    open(system.PLAYER_SETTINGS_FILE, 'w').write('other=1\nimage_duration=7\n')
    client.post('/set_image_duration', data={'seconds': '30'})
    assert system.get_image_duration() == 30 and 'other=1' in open(system.PLAYER_SETTINGS_FILE).read()
    for bad in ('0', '²', 'abc'):
        r = client.post('/set_image_duration', data={'seconds': bad}, follow_redirects=True)
        assert r.status_code == 200 and b'from 1 to 86400' in r.data
    assert system.get_image_duration() == 30
    open(system.PLAYER_SETTINGS_FILE, 'w').write('image_duration=²\n')
    assert system.get_image_duration() == 10 and client.get('/').status_code == 200


def test_player_settings_saved_together(pi, monkeypatch):
    # two saves at once (the web server is threaded) both read the file before either writes
    real_read = system.read_player_settings
    def slow_read():
        settings = real_read()
        time.sleep(.2)
        return settings
    monkeypatch.setattr(system, 'read_player_settings', slow_read)
    saves = [threading.Thread(target=system.save_image_duration, args=(30,)),
             threading.Thread(target=system.save_player_setting, args=('loop_player', 'omxplayer'))]
    for save in saves:
        save.start()
    for save in saves:
        save.join()
    assert sorted(open(system.PLAYER_SETTINGS_FILE).read().split()) == ['image_duration=30', 'loop_player=omxplayer']


def test_loop_player(client, monkeypatch):
    assert system.get_loop_player() == 'omxplayer'
    open(system.PLAYER_SETTINGS_FILE, 'w').write('image_duration=7\n')
    monkeypatch.setattr(system.shutil, 'which', lambda name: None)
    r = client.get('/')
    assert b'<option value="omxplayer" selected>' in r.data
    assert b'Add -loop to the end of the file name to loop or hold a clip. omxplayer is preferred for loops.' in r.data
    r = client.post('/set_loop_player', data={'loop_player': 'vlc'}, follow_redirects=True)
    assert b'played with VLC' in r.data and b'<option value="vlc" selected>' in r.data
    assert open(system.PLAYER_SETTINGS_FILE).read() == 'image_duration=7\nloop_player=vlc\n'
    r = client.post('/set_loop_player', data={'loop_player': 'mplayer'}, follow_redirects=True)
    assert b'choose VLC or omxplayer' in r.data and system.get_loop_player() == 'vlc'
    monkeypatch.setattr(system.shutil, 'which', lambda name: '/usr/bin/' + name)
    assert b"isn't installed here" not in client.get('/').data
    open(system.PLAYER_SETTINGS_FILE, 'w').write('loop_player=VLC\n')
    assert system.get_loop_player() == 'omxplayer'


# ----- Player card, playlist ----- #
def test_status_for_the_player_card(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    path = os.path.join(system.MEDIA_PATH, 'intro-loop.mp4')
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': path, 'since': time.time() - 10, 'pid': 4242,
                   'position': 5, 'length': 60, 'play_file': True}, f)
    r = client.get('/player/status').get_json()
    assert r['name'] == 'intro-loop.mp4' and r['folder'] == 'internal' and r['kind'] == 'video' and r['loop']
    # the position now, worked out on the Pi: the browser's clock may not match the Pi's
    assert 14.5 <= r['position'] <= 16 and r['length'] == 60 and r['play_file'] is True
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'paused', 'file': path, 'since': time.time() - 10, 'pid': 4242,
                   'position': 5, 'length': 60, 'play_file': True}, f)
    assert client.get('/player/status').get_json()['position'] == 5
    # an older player script: no position, how long it has played instead
    write_status('playing', path, since=time.time() - 75)
    r = client.get('/player/status').get_json()
    assert r['position'] is None and 74 <= r['elapsed'] <= 76 and r['play_file'] is False


def test_playlist_shows_what_the_player_plays(pi, client):
    usb = pi.media.parent / 'usb0'
    usb.mkdir()
    for path in (pi.media / 'b.mp4', pi.media / 'a-loop.mov', pi.media / 'notes', pi.media / 'notes.txt',
                 pi.media / '.hidden.mp4', usb / 'z.jpg', usb / 'LICENCE.broadcom', usb / 'config.txt'):
        path.write_bytes(b'x' * 2048)
    names = [(e['name'], e['folder'], e['plays'], e['internal']) for e in system.get_playlist()]
    # other files on the media partition are listed (to delete them), not those on USB sticks
    # (an SD card in a reader has a Pi's boot files)
    assert names == [('a-loop.mov', 'internal', True, True), ('b.mp4', 'internal', True, True),
                     ('notes', 'internal', False, True), ('notes.txt', 'internal', False, True),
                     ('z.jpg', 'usb0', True, False)]
    html = client.get('/').data.decode()
    assert 'z.jpg' in html and 'usb0' in html and 'not played: not a media file' in html
    # files on USB sticks can be played but not deleted or downloaded here
    assert 'download/z.jpg' not in html and 'download/b.mp4' in html


def test_play_chosen_file(pi, client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    monkeypatch.setattr(webservice.time, 'sleep', lambda s: None)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    (pi.media / 'a.mp4').write_bytes(b'x')
    (pi.media / 'b.mp4').write_bytes(b'x')
    path = str(pi.media / 'b.mp4')
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': str(pi.media / 'a.mp4'), 'since': time.time(), 'pid': 4242,
                   'play_file': True}, f)

    old_umask = os.umask(0o077)
    try:
        r = client.post('/player/play', data={'file': path}, headers={'X-Requested-With': 'fetch'})
    finally:
        os.umask(old_umask)
    assert r.status_code == 200 and sent == [(4242, signal.SIGUSR1)]
    request = json.load(open(system.PLAY_REQUEST_FILE))
    assert request['file'] == path and request['id']
    assert oct(os.stat(system.PLAY_REQUEST_FILE).st_mode & 0o777) == '0o644'   # the player runs as pi
    assert [n for n in os.listdir(os.path.dirname(system.PLAY_REQUEST_FILE)) if n.startswith('.mp4museum-play')] == []
    # a new id each time, so the player knows it is a new request
    client.post('/player/play', data={'file': path})
    assert json.load(open(system.PLAY_REQUEST_FILE))['id'] != request['id']

    # only files in the playlist
    sent.clear()
    for bad in ('/etc/passwd', str(pi.media / 'missing.mp4'), str(pi.media / '../internal/b.mp4')):
        r = client.post('/player/play', data={'file': bad}, headers={'X-Requested-With': 'fetch'})
        assert r.status_code == 409 and 'playlist' in r.get_json()['error']
    assert b"t in the playlist" in client.post('/player/play', data={'file': '/etc/passwd'}, follow_redirects=True).data
    # an older player script can't, and nothing is sent
    write_status('playing', str(pi.media / 'a.mp4'))
    r = client.post('/player/play', data={'file': path}, headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 409 and 'older version' in r.get_json()['error'] and sent == []



def test_previous_file(pi, client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    monkeypatch.setattr(webservice.time, 'sleep', lambda s: None)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    fetch = {'X-Requested-With': 'fetch'}

    def status(state='playing', **fields):
        with open(system.PLAYER_STATUS_FILE, 'w') as f:
            json.dump(dict({'state': state, 'file': '/media/internal/c.mp4', 'since': time.time(), 'pid': 4242,
                            'play_file': True, 'rewind': True, 'previous': True}, **fields), f)

    # the player works out which file (it knows its order and what it skips): the web interface
    # sends the command
    status()
    assert client.get('/player/status').get_json()['previous'] is True
    r = client.post('/player/previous', headers=fetch)
    assert r.status_code == 200 and sent == [(4242, signal.SIGUSR1)]
    request = json.load(open(system.PLAY_REQUEST_FILE))
    assert request['command'] == 'previous' and request['id'] and 'file' not in request
    assert b'Playing the previous file' in client.post('/player/previous', follow_redirects=True).data
    # not during start-up, nor with nothing playing; an older player would take it for Next
    sent.clear()
    for fields, error in (({'previous': False}, "There's no previous file during start-up."),
                          ({'state': 'idle'}, 'Nothing is playing.'),
                          ({'state': 'sync'}, "This doesn't work in sync mode."),
                          ({'previous': None}, 'older version')):
        status(**fields)
        r = client.post('/player/previous', headers=fetch)
        assert r.status_code == 409 and error in r.get_json()['error']
    assert sent == []
    os.remove(system.PLAYER_STATUS_FILE)
    assert b'The player is not running' in client.post('/player/previous', follow_redirects=True).data
    html = client.get('/').data.decode()
    assert 'id="previousButton"' in html and 'id="rewindButton"' in html


def test_player_buttons_tell_the_page_why_not(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    write_status('idle')
    r = client.post('/player/next', headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 409 and r.get_json()['error'] == 'Nothing is playing.'
    os.remove(system.PLAYER_STATUS_FILE)
    r = client.post('/player/pause', headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 409 and 'not running' in r.get_json()['error']


def test_upload_from_the_page_gets_json(pi, client):
    fetch = {'X-Requested-With': 'fetch'}
    r = client.post('/upload', data={'file': (io.BytesIO(b'video'), 'clip.mp4')}, headers=fetch)
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert r.get_json()['messages'] == [['success', "File 'clip.mp4' uploaded successfully."]]
    assert (pi.media / 'clip.mp4').read_bytes() == b'video'
    r = client.post('/upload', data={'file': (io.BytesIO(b'x'), '.hidden')}, headers=fetch)
    assert r.status_code == 400 and 'Invalid filename' in r.get_json()['error']
    # the page shows the messages itself: they don't pile up in the login cookie, file after file
    for n in range(30):
        client.post('/upload', data={'file': (io.BytesIO(b'x'), 'clip-%d with a long name.mp4' % n)}, headers=fetch)
    with client.session_transaction() as session:
        assert not session.get('_flashes')
    # a form without JavaScript still gets the message on the page
    html = client.post('/upload', data={'file': (io.BytesIO(b'x'), 'form.mp4')}, follow_redirects=True).data.decode()
    assert "form.mp4&#39; uploaded successfully" in html


# ----- Sound ----- #
def test_sound_cards_listed(client):
    cards = system.parse_sound_cards(
        'card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]\n'
        '  Subdevices: 8/8\n'
        'card 1: vc4hdmi [vc4-hdmi], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]\n'
        'card 1: vc4hdmi [vc4-hdmi], device 1: other [other]\n'
        'card 12: Device [USB Audio Device], device 0: USB Audio [USB Audio]\n')
    assert [(c['number'], c['name'], len(c['devices'])) for c in cards] == [
        ('0', 'bcm2835 Headphones', 1), ('1', 'vc4-hdmi', 2), ('12', 'USB Audio Device', 1)]
    assert cards[0]['devices'] == ['device 0: bcm2835 Headphones']
    client.post('/set_sound_device', data={'device': '1'})
    assert 'Card <strong>1</strong>' in client.get('/').data.decode()


def test_every_icon_is_in_the_sprite():
    import re
    app = os.path.dirname(system.__file__)
    sprite = open(os.path.join(app, 'static', 'icons.svg')).read()
    symbols = set(re.findall(r'<symbol id="([a-z0-9-]+)"', sprite))
    used = set()
    for folder, names in (('templates', os.listdir(os.path.join(app, 'templates'))), ('static', ['mp4museum.js'])):
        for name in names:
            text = open(os.path.join(app, folder, name)).read()
            used |= set(re.findall(r"icon\('([a-z0-9-]+)'", text))
    # chosen by file type, in the template and the JavaScript
    used |= {'film', 'image', 'music', 'file', 'square-play'}
    assert len(used) > 20 and used <= symbols, used - symbols


def test_play_requests_at_the_same_time(pi, monkeypatch):
    monkeypatch.setattr(system, 'signal_player', lambda signum: True)
    errors = []
    def ask(n):
        for i in range(200):
            try:
                system.request_play('/media/internal/%d-%d.mp4' % (n, i))
            except Exception as e:
                errors.append(e)
    threads = [threading.Thread(target=ask, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and json.load(open(system.PLAY_REQUEST_FILE))['file'].endswith('-199.mp4')


def test_position_survives_the_clock_being_set(client, monkeypatch):
    # the Pi has no clock of its own: its time can jump by days when it gets a network
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': '/media/internal/a.mp4', 'since': time.time() - 3 * 86400,
                   'mono': time.monotonic() - 10, 'pid': 4242, 'position': 5, 'length': 600, 'play_file': True}, f)
    assert 14.5 <= client.get('/player/status').get_json()['position'] <= 16


def test_page_starts_with_doctype(client):
    assert client.get('/').data.startswith(b'<!doctype html>')
    assert client.get('/login').data.startswith(b'<!doctype html>')


# ----- Images too big to show, the player's log ----- #
def png(width, height):
    import struct
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', width, height) + b'\x08\x02\x00\x00\x00'


def jpeg(width, height):
    import struct
    exif = b'\xff\xe1' + struct.pack('>H', 8) + b'Exif\x00\x00'
    sof = b'\xff\xc2' + struct.pack('>HBHH', 11, 8, height, width) + b'\x03\x01\x11\x00'
    return b'\xff\xd8' + exif + sof + b'\xff\xd9'


def test_image_sizes(pi):
    files = {'a.png': png(1920, 1080), 'b.jpg': jpeg(6000, 4000), 'c.gif': b'GIF89a' + bytes([0x80, 0x07, 0x38, 0x04]),
             'd.png': png(2048, 2048), 'e.png': png(3300, 2550), 'f.jpg': b'\xff\xd8\xff', 'g.mp4': png(9000, 9000)}
    for name, data in files.items():
        (pi.media / name).write_bytes(data)
    found = {e['name']: (e['pixels'], e['large']) for e in system.get_playlist()}
    assert found == {'a.png': ((1920, 1080), False), 'b.jpg': ((6000, 4000), True), 'c.gif': ((1920, 1080), False),
                     'd.png': ((2048, 2048), False), 'e.png': ((3300, 2550), True),   # scrambled on a Pi 3B
                     'f.jpg': (None, False),
                     'g.mp4': (None, False)}              # only images are read


def test_very_large_image_gets_a_warning(pi, client):
    fetch = {'X-Requested-With': 'fetch'}
    r = client.post('/upload', data={'file': (io.BytesIO(png(8000, 6000)), 'huge.png')}, headers=fetch).get_json()
    assert r['ok'] is True and r['messages'][1][0] == 'warning' and '8000×6000' in r['messages'][1][1]
    r = client.post('/upload', data={'file': (io.BytesIO(png(1920, 1080)), 'fine.png')}, headers=fetch).get_json()
    assert len(r['messages']) == 1
    html = client.get('/').data.decode()
    assert '8000×6000' in html and html.count('too large</span>') == 1


def test_player_log_on_the_system_tab(client):
    assert 'Nothing yet.' in client.get('/').data.decode()
    with open(system.PLAYER_LOG_FILE, 'w') as f:
        f.write(''.join('line %d\n' % n for n in range(100)) + '2026-10-03 05:00:00 the player stopped (exit code 137)\n')
    html = client.get('/').data.decode()
    assert 'exit code 137' in html and 'line 99' in html and 'line 40\n' not in html


def test_file_name_plays_the_file(pi, client):
    (pi.media / 'a.mp4').write_bytes(b'x')
    html = client.get('/').data.decode()
    # the name is a second button for the same play form
    assert 'id="play-1"' in html and 'form="play-1" class="item-name js-play"' in html
    assert 'reboot' in client.get('/player/status').get_json()['text']


def test_files_the_player_skips_are_marked(pi, client):
    (pi.media / 'big.png').write_bytes(png(9000, 9000))
    (pi.media / 'ok.mp4').write_bytes(b'x')
    info = os.stat(pi.media / 'big.png')
    with open(system.PLAYER_SKIPPED_FILE, 'w') as f:
        json.dump([[str(pi.media / 'big.png'), [info.st_size, int(info.st_mtime)], 2],
                   [str(pi.media / 'ok.mp4'), [1, int(info.st_mtime)], 1]], f)   # once doesn't count
    assert [e['skipped'] for e in system.get_playlist()] == [True, False]
    assert 'skipped</span>' in client.get('/').data.decode()
    # replaced (another size or time): the player plays it again, so it isn't marked
    (pi.media / 'big.png').write_bytes(png(1920, 1080) + b'more')
    assert [e['skipped'] for e in system.get_playlist()] == [False, False]


def test_image_size_odd_headers(pi):
    import struct
    # fill bytes before a JPEG marker are allowed
    jpg = b'\xff\xd8\xff\xff\xff\xc0' + struct.pack('>HBHH', 11, 8, 600, 800) + b'\x03'
    (pi.media / 'fill.jpg').write_bytes(jpg)
    assert system.image_size(str(pi.media / 'fill.jpg')) == (800, 600)
    bmp = b'BM' + bytes(16) + struct.pack('<ii', -640, -480)
    (pi.media / 'neg.bmp').write_bytes(bmp)
    assert system.image_size(str(pi.media / 'neg.bmp')) == (640, 480)
    for junk in (b'', b'\xff\xd8', b'\xff\xd8\xff\xe0\x00\x01', b'\x89PNG\r\n\x1a\n', b'GIF89a\x01'):
        (pi.media / 'junk.jpg').write_bytes(junk)
        assert system.image_size(str(pi.media / 'junk.jpg')) is None


def test_rewind(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    monkeypatch.setattr(webservice.time, 'sleep', lambda s: None)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    fetch = {'X-Requested-With': 'fetch'}
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': '/media/internal/a.mp4', 'since': time.time(), 'pid': 4242,
                   'play_file': True, 'rewind': True}, f)
    assert client.get('/player/status').get_json()['rewind'] is True
    r = client.post('/player/rewind', headers=fetch)
    assert r.status_code == 200 and sent == [(4242, signal.SIGUSR1)]
    request = json.load(open(system.PLAY_REQUEST_FILE))
    assert request['command'] == 'rewind' and request['id'] and 'file' not in request
    assert b'Press play to start' in client.post('/player/rewind', follow_redirects=True).data
    # an older player would take the signal for Next: nothing is sent
    sent.clear()
    write_status('playing', '/media/internal/a.mp4')
    r = client.post('/player/rewind', headers=fetch)
    assert r.status_code == 409 and 'older version' in r.get_json()['error'] and sent == []
    write_status('idle')
    assert client.post('/player/rewind', headers=fetch).get_json()['error'] == 'Nothing is playing.'
    # the boot video is playing: the player can rewind, but not that
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': '/home/pi/mp4museum-boot.mp4', 'since': time.time(), 'pid': 4242,
                   'play_file': True, 'rewind': False}, f)
    assert client.get('/player/status').get_json()['rewind'] is False
    r = client.post('/player/rewind', headers=fetch)
    assert r.status_code == 409 and 'start-up video' in r.get_json()['error'] and sent == []
    assert 'id="rewindButton"' in client.get('/').data.decode()


def test_large_images_only_marked_where_the_player_skips_them(pi, client, tmp_path):
    (pi.media / 'big.png').write_bytes(png(4000, 3000))
    assert system.get_playlist()[0]['large'] is True
    for model in ('Raspberry Pi 4 Model B Rev 1.4\0', 'Raspberry Pi 5 Model B Rev 1.0\0'):
        (tmp_path / 'model').write_text(model)
        assert system.get_playlist()[0]['large'] is False
        r = client.post('/upload', data={'file': (io.BytesIO(png(4000, 3000)), 'b2.png')},
                        headers={'X-Requested-With': 'fetch'}).get_json()
        assert len(r['messages']) == 1


def test_rename(pi, client):
    (pi.media / 'clip.mp4').write_bytes(b'video')
    (pi.media / 'other.mp4').write_bytes(b'x')
    r = client.post('/rename', data={'filename': 'clip.mp4', 'new_name': ' 01 clip-loop.mp4 '}, follow_redirects=True)
    assert b'Renamed' in r.data and (pi.media / '01 clip-loop.mp4').read_bytes() == b'video'
    assert not (pi.media / 'clip.mp4').exists() and ['mount', '-o', 'remount,rw', str(pi.media)] in pi.commands
    # not over another file, not to a bad name, not outside the media partition
    for new in ('other.mp4', '.hidden.mp4', '../escape.mp4', 'a/b.mp4', ''):
        r = client.post('/rename', data={'filename': '01 clip-loop.mp4', 'new_name': new}, follow_redirects=True)
        assert b'Renamed' not in r.data, new
    assert (pi.media / '01 clip-loop.mp4').exists() and not (pi.media.parent / 'escape.mp4').exists()
    assert b'not found' in client.post('/rename', data={'filename': 'gone.mp4', 'new_name': 'x.mp4'}, follow_redirects=True).data
    # a name without an extension isn't played: said so
    r = client.post('/rename', data={'filename': 'other.mp4', 'new_name': 'notes'}, follow_redirects=True)
    assert b"won&#39;t play it" in r.data
    assert 'askNewName' in client.get('/').data.decode()


def test_engine_badge(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    for engine, shown in (('omxplayer', 'omxplayer'), ('vlc', 'vlc'), ('something', None)):
        with open(system.PLAYER_STATUS_FILE, 'w') as f:
            json.dump({'state': 'playing', 'file': '/media/internal/a-loop.mp4', 'since': time.time(), 'pid': 4242,
                       'engine': engine}, f)
        assert client.get('/player/status').get_json()['engine'] == shown
    assert 'id="playerEngine"' in client.get('/').data.decode()


def test_changing_the_loop_player_starts_the_loop_again(pi, client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    (pi.media / 'a-loop.mp4').write_bytes(b'x')
    (pi.media / 'b.mp4').write_bytes(b'x')
    for playing, restarted in (('a-loop.mp4', True), ('b.mp4', False)):
        sent.clear()
        with open(system.PLAYER_STATUS_FILE, 'w') as f:
            json.dump({'state': 'playing', 'file': str(pi.media / playing), 'since': time.time(), 'pid': 4242,
                       'play_file': True, 'engine': 'omxplayer'}, f)
        r = client.post('/set_loop_player', data={'loop_player': 'vlc'}, follow_redirects=True)
        assert (b'has started again' in r.data) == restarted and bool(sent) == restarted
        if restarted:
            assert json.load(open(system.PLAY_REQUEST_FILE))['file'] == str(pi.media / playing)


def test_rename_case_only_on_a_case_insensitive_partition(pi, client, monkeypatch):
    # exFAT ignores case: renaming Clip.mp4 to clip.mp4 in one go changes nothing there
    (pi.media / 'Clip.mp4').write_bytes(b'video')
    real_exists, real_samefile = os.path.exists, os.path.samefile
    lower = str(pi.media / 'clip.mp4')
    monkeypatch.setattr(webservice.os.path, 'exists', lambda p: True if p == lower else real_exists(p))
    monkeypatch.setattr(webservice.os.path, 'samefile', lambda a, b: True if lower in (a, b) else real_samefile(a, b))
    renames = []
    real_rename = os.rename
    monkeypatch.setattr(webservice.os, 'rename', lambda a, b: (renames.append((os.path.basename(a), os.path.basename(b))), real_rename(a, b)))
    r = client.post('/rename', data={'filename': 'Clip.mp4', 'new_name': 'clip.mp4'}, follow_redirects=True)
    assert b'Renamed' in r.data and len(renames) == 2 and renames[0][1].startswith('.') and renames[1][1] == 'clip.mp4'
    assert sorted(os.listdir(pi.media)) == ['clip.mp4']


def test_rename_refuses_names_ending_in_a_dot(pi, client):
    # exFAT drops a trailing dot, so the file would lose its extension
    (pi.media / 'a.mp4').write_bytes(b'x')
    for bad in ('a.mp4.', 'b.'):
        r = client.post('/rename', data={'filename': 'a.mp4', 'new_name': bad}, follow_redirects=True)
        assert b'Renamed' not in r.data
    assert not system.is_valid_filename('clip.') and system.is_valid_filename('clip.mp4')


def test_renames_to_the_same_name_one_at_a_time(pi, client, monkeypatch):
    (pi.media / 'a.mp4').write_bytes(b'a')
    (pi.media / 'b.mp4').write_bytes(b'b')
    real_rename = os.rename
    def slow_rename(a, b):
        time.sleep(.2)
        real_rename(a, b)
    monkeypatch.setattr(webservice.os, 'rename', slow_rename)
    def rename(name):
        c = webservice.app.test_client()
        c.post('/login', data={'password': 'mp4museum'})
        c.post('/rename', data={'filename': name, 'new_name': 'same.mp4'})
    threads = [threading.Thread(target=rename, args=(n,)) for n in ('a.mp4', 'b.mp4')]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # one of them was renamed, the other is still there: nothing was overwritten
    assert sorted(os.listdir(pi.media)) in (['b.mp4', 'same.mp4'], ['a.mp4', 'same.mp4'])


def test_loop_restarted_only_when_the_program_changes(pi, client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append(sig))
    (pi.media / 'a-loop.mp4').write_bytes(b'x')
    def playing(engine):
        with open(system.PLAYER_STATUS_FILE, 'w') as f:
            json.dump({'state': 'playing', 'file': str(pi.media / 'a-loop.mp4'), 'since': time.time(), 'pid': 4242,
                       'play_file': True, 'engine': engine}, f)
    monkeypatch.setattr(system.shutil, 'which', lambda name: '/usr/bin/' + name)
    for engine, choice, restarted in (('vlc', 'vlc', False), ('omxplayer', 'omxplayer', False),
                                      ('omxplayer', 'vlc', True), ('vlc', 'omxplayer', True)):
        sent.clear()
        playing(engine)
        r = client.post('/set_loop_player', data={'loop_player': choice}, follow_redirects=True)
        assert (b'has started again' in r.data) == restarted and bool(sent) == restarted, (engine, choice)
    # an omxplayer loop's first frame held (VLC shows it), VLC chosen: the loop starts again in VLC
    sent.clear()
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'paused', 'file': str(pi.media / 'a-loop.mp4'), 'since': time.time(), 'pid': 4242,
                   'play_file': True, 'engine': 'vlc', 'loop_player': 'omxplayer'}, f)
    assert b'has started again' in client.post('/set_loop_player', data={'loop_player': 'vlc'}, follow_redirects=True).data
    # omxplayer chosen, but this loop can't use it (file type, codec): VLC carries on, nothing to restart
    sent.clear()
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': str(pi.media / 'a-loop.mp4'), 'since': time.time(), 'pid': 4242,
                   'play_file': True, 'engine': 'vlc', 'loop_player': 'vlc', 'loop_omx_ok': False}, f)
    assert b'has started again' not in client.post('/set_loop_player', data={'loop_player': 'omxplayer'},
                                                   follow_redirects=True).data and not sent
    # omxplayer chosen but not installed: VLC still plays it
    monkeypatch.setattr(system.shutil, 'which', lambda name: None)
    sent.clear()
    playing('vlc')
    assert b'has started again' not in client.post('/set_loop_player', data={'loop_player': 'omxplayer'},
                                                   follow_redirects=True).data and not sent


def test_engine_badge_only_while_something_plays(client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'idle', 'file': None, 'since': time.time(), 'pid': 4242, 'engine': 'vlc'}, f)
    assert client.get('/player/status').get_json()['engine'] is None


# ----- Graphics memory ----- #
def test_graphics_memory(pi, client, tmp_path):
    original = open(system.CONFIG_FILE).read()
    assert system.get_gpu_mem(original) == 128
    html = client.get('/').data.decode()
    assert 'Now: <strong>128 MB</strong>' in html and '256 MB (recommended)' in html   # a Pi 3: 1 GB
    r = client.post('/set_gpu_mem', data={'gpu_mem': '256'})
    assert r.status_code == 302 and '/confirm_reboot' in r.location
    changed = open(system.CONFIG_FILE).read()
    assert changed == original.replace('gpu_mem=128', 'gpu_mem=256') and system.get_gpu_mem(changed) == 256
    for bad in ('1024', '64', 'lots', ''):
        r = client.post('/set_gpu_mem', data={'gpu_mem': bad}, follow_redirects=True)
        assert b'Please choose' in r.data
    assert system.get_gpu_mem(open(system.CONFIG_FILE).read()) == 256
    # a Pi 4 with 4 GB
    (tmp_path / 'cpuinfo').write_text('Revision\t: c03114\n')
    (tmp_path / 'meminfo').write_text('MemTotal:        3884000 kB\n')
    assert system.recommended_gpu_mem() == 512 and system.gpu_mem_choices() == (128, 256, 512)
    # 512 MB (a Pi Zero W): at most 384 MB, so 512 isn't offered or taken
    (tmp_path / 'cpuinfo').write_text('Revision\t: 9000c1\n')
    html = client.get('/').data.decode()
    assert 'value="512"' not in html and '128 MB (recommended)' in html
    r = client.post('/set_gpu_mem', data={'gpu_mem': '512'}, follow_redirects=True)
    assert b'Please choose 128 or 256 MB' in r.data and system.get_gpu_mem(open(system.CONFIG_FILE).read()) == 256
    # an old board without the memory in its revision code: from Linux's memory and gpu_mem
    (tmp_path / 'cpuinfo').write_text('Revision\t: 000e\n')
    (tmp_path / 'meminfo').write_text('MemTotal:         190000 kB\n')
    open(system.CONFIG_FILE, 'w').write('gpu_mem=64\n')
    assert system.board_memory_megabytes() == 256 and system.gpu_mem_choices() == (128,)
    open(system.CONFIG_FILE, 'w').write('gpu_mem=256\n')
    (tmp_path / 'meminfo').write_text('MemTotal:         190000 kB\n')
    assert system.board_memory_megabytes() == 512


def test_graphics_memory_lines_in_config(pi, client):
    config = ('# comment\n\ndisable_splash=1\n[pi4]\ngpu_mem=76\ngpu_mem_1024=200\ndtoverlay=x\n'
              '[all]\ngpu_mem=64\ngpu_mem_1024=300\ngpu_mem_512 = 100\n')
    assert system.get_gpu_mem(config) == 64
    # gpu_mem_1024 wins over gpu_mem on boards with 1 GB or more, gpu_mem_512 with 512 MB
    assert system.get_gpu_mem(config, 1024) == 300 and system.get_gpu_mem(config, 4096) == 300
    assert system.get_gpu_mem(config, 512) == 100 and system.get_gpu_mem(config, 256) == 64
    changed = system.set_gpu_mem_in_config(config, 512)
    # the lines that would win over it on some boards, in model sections too, are taken out or
    # set the same; the rest of the Pi 4 section is left alone
    assert changed == ('# comment\n\ndisable_splash=1\n[pi4]\ngpu_mem=512\ndtoverlay=x\n'
                       '[all]\ngpu_mem=512\n')
    assert system.get_gpu_mem(changed, 1024) == 512
    # the page says when a model section sets it differently
    assert system.gpu_mem_in_model_sections(config) and system.gpu_mem_in_model_sections(changed)
    assert not system.gpu_mem_in_model_sections('gpu_mem=64\n[pi4]\ndtoverlay=x\n')
    assert 'have their own setting' not in client.get('/').data.decode()
    open(system.CONFIG_FILE, 'w').write(config)
    assert 'have their own setting' in client.get('/').data.decode()
    # not set: added at the top, where it applies to every Pi
    config = '# comment\n\ndisable_splash=1\n[pi4]\ndtoverlay=x\n'
    changed = system.set_gpu_mem_in_config(config, 256)
    assert changed == '# comment\n\ngpu_mem=256\ndisable_splash=1\n[pi4]\ndtoverlay=x\n' and system.get_gpu_mem(changed) == 256
    # two lines for every Pi: one is left
    assert system.set_gpu_mem_in_config('gpu_mem=64\ngpu_mem=128\n', 256) == 'gpu_mem=256\n'


def test_device_info(pi, client, tmp_path, monkeypatch):
    revision = tmp_path / 'cpuinfo'
    revision.write_text('Hardware\t: BCM2835\nRevision\t: a02082\nSerial\t\t: 00000000deadbeef\nModel\t\t: Raspberry Pi 3\n')
    (tmp_path / 'os-release').write_text('PRETTY_NAME="Raspbian GNU/Linux 10 (buster)"\nNAME="Raspbian GNU/Linux"\n')
    (tmp_path / 'uptime').write_text('11520.42 40000.00\n')
    monkeypatch.setattr(system, 'CPUINFO_FILE', str(revision))
    monkeypatch.setattr(system, 'OS_RELEASE_FILE', str(tmp_path / 'os-release'))
    monkeypatch.setattr(system, 'UPTIME_FILE', str(tmp_path / 'uptime'))
    answers = {'get_mem gpu': 'gpu=256M', 'measure_temp': "temp=48.3'C", 'get_throttled': 'throttled=0x50000'}
    monkeypatch.setattr(system, 'run_command', lambda cmd, timeout=None: (True, answers[' '.join(cmd[1:])]) if cmd[0] == 'vcgencmd'
                        else pi.run_command(cmd))
    info = dict(system.get_device_info())
    assert info['Model'] == 'Raspberry Pi 3 Model B Rev 1.2' and info['Memory'] == '1 GB'
    assert info['Graphics memory'] == '256 MB' and info['Temperature'] == '48.3 °C'
    assert 'Was too low' in info['Power'] and info['Running for'] == '3 h 12 min'
    assert info['Operating system'] == 'Raspbian GNU/Linux 10 (buster)' and info['Serial number'] == '00000000deadbeef'
    assert 'free of' in info['Media partition']
    page = client.get('/').data.decode()
    assert '<dt>Memory</dt><dd>1 GB</dd>' in page and 'Raspbian GNU/Linux 10 (buster)' in page
    # a Pi 4 with 4 GB; power fine
    revision.write_text('Revision\t: c03114\n')
    answers['get_throttled'] = 'throttled=0x0'
    info = dict(system.get_device_info())
    assert info['Memory'] == '4 GB' and info['Power'] == 'OK' and 'Serial number' not in info
    # slowed down by the heat: the power is fine (as seen on a Pi 3 B+ at 89 °C)
    answers['get_throttled'], answers['measure_temp'] = 'throttled=0x60006', "temp=88.7'C"
    info = dict(system.get_device_info())
    assert info['Temperature'] == '88.7 °C (too hot: the Pi is slowing itself down)' and info['Power'] == 'OK'
    # slowed down by low power: that is what the power line says
    answers['get_throttled'] = 'throttled=0x50005'
    info = dict(system.get_device_info())
    assert info['Temperature'] == '88.7 °C' and info['Power'].startswith('Too low now')
    # old boards, no vcgencmd, nothing readable: only what is known
    revision.write_text('Revision\t: 000e\n')
    monkeypatch.setattr(system, 'run_command', lambda cmd, timeout=None: (False, 'not found'))
    (tmp_path / 'model').unlink()
    info = dict(system.get_device_info())
    assert info['Memory'] == '861 MB for programs' and 'Model' not in info and 'Power' not in info
    assert system.format_duration(59) == '0 min' and system.format_duration(3 * 86400 + 7200) == '3 d 2 h'


def test_start_up_settings(pi, client):
    html = client.get('/').data.decode()
    assert '<option value="1" selected>Play once (default)</option>' in html
    assert '<option value="yes" selected>Show (default)</option>' in html and 'Show network address on boot' in html
    r = client.post('/set_boot_video_plays', data={'boot_video_plays': '0'}, follow_redirects=True)
    assert b"boot video won&#39;t play" in r.data
    assert b'will play once' in client.post('/set_boot_video_plays', data={'boot_video_plays': '1'}, follow_redirects=True).data
    client.post('/set_boot_video_plays', data={'boot_video_plays': '0'})
    r = client.post('/set_show_address', data={'show_address': 'no'}, follow_redirects=True)
    assert b"won&#39;t be shown" in r.data
    # saved for the player, keeping the other settings
    client.post('/set_image_duration', data={'seconds': '7'})
    assert sorted(open(system.PLAYER_SETTINGS_FILE).read().split()) == ['boot_video_plays=0', 'image_duration=7', 'show_address=no']
    assert system.get_boot_video_plays() == 0 and system.get_show_address() is False
    html = client.get('/').data.decode()
    assert '<option value="0" selected>' in html and '<option value="no" selected>' in html
    for route, field, bad in (('/set_boot_video_plays', 'boot_video_plays', '3'), ('/set_boot_video_plays', 'boot_video_plays', 'x'),
                              ('/set_show_address', 'show_address', 'maybe')):
        r = client.post(route, data={field: bad}, follow_redirects=True)
        assert b'Please choose' in r.data
    assert system.get_boot_video_plays() == 0 and system.get_show_address() is False
    # a player edited before these settings existed doesn't use them: said so
    assert 'edited before these' not in client.get('/').data.decode()
    with open(system.SCRIPT_FILE, 'w') as f:
        f.write('# edited\nimport vlc\n')
    assert 'edited before these' in client.get('/').data.decode()
    # an edited player from when the boot video played twice by default: shown as it is
    open(system.PLAYER_SETTINGS_FILE, 'w').write('image_duration=7\n')
    with open(system.SCRIPT_FILE, 'w') as f:
        f.write("settings = {'boot_video_plays': 2, 'show_address': True}\n")
    assert system.get_boot_video_plays() == 2
    with open(system.SCRIPT_FILE, 'w') as f:
        f.write("settings = {'boot_video_plays': 1, 'show_address': True}\n")
    assert system.get_boot_video_plays() == 1


def test_switch_files_off_and_on(pi, client):
    (pi.media / 'a.mp4').write_bytes(b'x')
    (pi.media / 'b.mp4').write_bytes(b'x')
    usb = pi.media.parent / 'usb0'
    usb.mkdir()
    (usb / 'c.mp4').write_bytes(b'x')
    a, c = str(pi.media / 'a.mp4'), str(usb / 'c.mp4')
    # off: written for the player, the file itself untouched; USB sticks (read-only) too
    for path in (a, c):
        r = client.post('/switch_file', data={'file': path, 'off': '1'}, follow_redirects=True)
        assert b'switched off' in r.data
    assert open(system.DISABLED_FILE).read() == '%s\n%s\n' % tuple(sorted([a, c]))
    assert (pi.media / 'a.mp4').read_bytes() == b'x' and ['mount', '-o', 'remount,rw', str(pi.boot)] in pi.commands
    entries = {e['name']: e for e in system.get_playlist()}
    assert entries['a.mp4']['disabled'] and not entries['b.mp4']['disabled'] and entries['c.mp4']['disabled']
    html = client.get('/').data.decode()
    assert html.count('switched-off') == 2 and 'Switch on a.mp4' in html and 'Switch off b.mp4' in html
    # not played from the web interface while off
    r = client.post('/player/play', data={'file': a}, headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 409 and 'switched off' in r.get_json()['error']
    # renamed: still off under its new name; deleted: off no more (a new file of that name plays)
    client.post('/rename', data={'filename': 'a.mp4', 'new_name': 'a2.mp4'})
    assert str(pi.media / 'a2.mp4') in system.get_disabled_files() and a not in system.get_disabled_files()
    client.post('/delete', data={'filename': 'a2.mp4'})
    assert system.get_disabled_files() == {c}
    # on again
    assert b'switched on again' in client.post('/switch_file', data={'file': c, 'off': '0'}, follow_redirects=True).data
    assert system.get_disabled_files() == set()
    # only files in the playlist
    r = client.post('/switch_file', data={'file': '/etc/passwd', 'off': '1'}, follow_redirects=True)
    assert b"t in the playlist" in r.data and system.get_disabled_files() == set()


def test_switching_off_the_file_playing_moves_on(pi, client, monkeypatch):
    monkeypatch.setattr(system, '_is_player_process', lambda pid: pid == 4242)
    sent = []
    monkeypatch.setattr(os, 'kill', lambda pid, sig: sent.append((pid, sig)))
    (pi.media / 'loop-a.mp4').write_bytes(b'x')
    (pi.media / 'b.mp4').write_bytes(b'x')
    a = str(pi.media / 'loop-a.mp4')
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'playing', 'file': a, 'since': time.time(), 'pid': 4242, 'play_file': True}, f)
    # (a loop would go on until next): the player is told to move on
    r = client.post('/switch_file', data={'file': a, 'off': '1'}, follow_redirects=True)
    assert b'the player moves on' in r.data and sent == [(4242, signal.SIGUSR1)]
    # sync mode (omxplayer-sync runs until the player stops): from the next start
    sent.clear()
    (pi.media / 'sync.mp4').write_bytes(b'x')
    with open(system.PLAYER_STATUS_FILE, 'w') as f:
        json.dump({'state': 'sync', 'file': str(pi.media / 'sync.mp4'), 'since': time.time(), 'pid': 4242}, f)
    r = client.post('/switch_file', data={'file': str(pi.media / 'sync.mp4'), 'off': '1'}, follow_redirects=True)
    assert b'sync mode stops from the next start' in r.data and sent == []
    # another file: nothing is sent
    sent.clear()
    client.post('/switch_file', data={'file': str(pi.media / 'b.mp4'), 'off': '1'})
    assert sent == []


def test_switching_off_with_an_edited_player_says_it_wont_work(pi, client):
    (pi.media / 'a.mp4').write_bytes(b'x')
    with open(system.SCRIPT_FILE, 'w') as f:
        f.write('# edited before switching files off existed\n')
    r = client.post('/switch_file', data={'file': str(pi.media / 'a.mp4'), 'off': '1'}, follow_redirects=True)
    assert b'edited before switching files off existed' in r.data and b'plays every file' in r.data


def test_disabled_list_from_windows_and_deleting(pi, client, monkeypatch):
    (pi.media / 'a.mp4').write_bytes(b'x')
    a = str(pi.media / 'a.mp4')
    (pi.boot / 'mp4m-disabled.txt').write_bytes(('%s\r\n' % a).encode())
    assert system.get_disabled_files() == {a} and system.get_playlist()[0]['disabled']
    # deleted, but the list can't be written: the delete still reported as done
    def fails(**kwargs):
        raise OSError('read-only')
    monkeypatch.setattr(system, 'update_disabled_files', fails)
    r = client.post('/delete', data={'filename': 'a.mp4'}, follow_redirects=True).data
    assert b'deleted successfully' in r and b'Failed to delete' not in r and b"Couldn&#39;t take it off" in r


def test_reboot_waits_for_a_card_copy_or_update(pi, client):
    # one started (here: by another process) holds the lock: no reboot
    other = system.try_busy_lock()
    r = client.post('/reboot', headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 409 and b'reboot when it' in r.data and ['reboot'] not in pi.commands
    os.close(other)
    # and a reboot under way holds it, so none can start before it; it can be asked for again
    assert client.post('/reboot', headers={'X-Requested-With': 'fetch'}).status_code == 200
    assert ['reboot'] in pi.commands and system.try_busy_lock() is None
    assert client.post('/reboot', headers={'X-Requested-With': 'fetch'}).status_code == 200
    assert pi.commands.count(['reboot']) == 2
    os.close(webservice._reboot_lock['fd'])


def test_reboot_from_the_page_says_when_it_failed(pi, client, monkeypatch):
    fetch = {'X-Requested-With': 'fetch'}
    r = client.post('/reboot', headers=fetch)
    assert r.status_code == 200 and ['reboot'] in pi.commands
    # the page's script gets the failure (a redirect would hide it behind the page it waits for)
    monkeypatch.setattr(system, 'run_command', lambda cmd, timeout=None: (False, 'not allowed') if cmd == ['reboot'] else pi.run_command(cmd))
    r = client.post('/reboot', headers=fetch)
    assert r.status_code == 500 and r.data == b'Failed to reboot: not allowed'
    assert b'Failed to reboot: not allowed' in client.post('/reboot', follow_redirects=True).data


# ----- Copying a file from a USB stick ----- #
@pytest.fixture
def stick(pi, monkeypatch):
    usb = pi.media.parent / 'usb0'
    usb.mkdir()
    (usb / 'film.mp4').write_bytes(b'f' * 5000)
    monkeypatch.setattr(system, '_copies', {})
    monkeypatch.setattr(system, '_copy_results', [])
    return usb


def copied(client, path, wait=True):
    """Copy from the web interface, wait until it's done; the page then."""
    r = client.post('/copy_to_player', data={'file': str(path)}, follow_redirects=True)
    deadline = time.monotonic() + 5
    while wait and system.copies_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    return r.data.decode() + client.get('/').data.decode()


def test_copy_a_file_from_a_usb_stick(pi, client, stick):
    html = client.get('/').data.decode()
    assert 'Copy film.mp4 to this player' in html
    assert 'Copy b.mp4' not in html
    page = copied(client, stick / 'film.mp4')
    assert "Copying &#39;film.mp4&#39; to this player." in page
    assert "&#39;film.mp4&#39; copied to this player" in page
    assert (pi.media / 'film.mp4').read_bytes() == b'f' * 5000
    # written under a temporary name, then renamed: nothing else left behind
    assert sorted(os.listdir(pi.media)) == ['film.mp4']
    assert pi.mounts()[-2:] == [['mount', '-o', 'remount,rw', str(pi.media)], ['mount', '-o', 'remount,ro', str(pi.media)]]
    # said once
    assert 'copied to this player' not in client.get('/').data.decode()


def test_copy_runs_in_the_background(pi, client, stick, monkeypatch):
    go = threading.Event()
    real = system.copy_to_media
    monkeypatch.setattr(system, 'copy_to_media', lambda source: (go.wait(5), real(source))[1])
    html = copied(client, stick / 'film.mp4', wait=False)
    assert 'Copying film.mp4 to this player' in html and 'data-copying' in html
    assert client.get('/copy_status').get_json() == {'copying': [str(stick / 'film.mp4')]}
    # a second copy of the same file, a reboot or an update meanwhile: refused
    assert 'or it&#39;s being copied' in copied(client, stick / 'film.mp4', wait=False)
    r = client.post('/reboot', headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 409 and b'A file is being copied' in r.data
    client.post('/install_update')
    assert "A file is being copied to this player: install the update when it&#39;s done." in client.get('/').data.decode()
    assert ['reboot'] not in pi.commands
    go.set()
    assert 'copied to this player' in copied(client, stick / 'nothing.mp4')
    assert client.get('/copy_status').get_json() == {'copying': []}


def test_copies_hold_the_lock_cards_and_updates_take(pi, client, stick, monkeypatch):
    # (shared: several copies can run at once)
    (stick / 'other.mp4').write_bytes(b'o')
    go = threading.Event()
    real = system.copy_to_media
    monkeypatch.setattr(system, 'copy_to_media', lambda source: (go.wait(5), real(source))[1])
    copied(client, stick / 'film.mp4', wait=False)
    copied(client, stick / 'other.mp4', wait=False)
    assert len(system.copies_running()) == 2
    assert system.try_busy_lock() is None
    go.set()
    copied(client, stick / 'nothing.mp4')
    lock = system.try_busy_lock()
    assert lock is not None and sorted(os.listdir(pi.media)) == ['film.mp4', 'other.mp4']
    # while a card is made or an update installed (another process): refused
    page = copied(client, stick / 'film.mp4')
    assert 'A card is being made or an update installed: copy files when it&#39;s done.' in page
    assert system.copies_running() == []
    os.close(lock)


def test_copy_refuses_to_replace_a_file(pi, client, stick):
    # exFAT doesn't tell upper and lower case apart
    (pi.media / 'FILM.mp4').write_bytes(b'mine')
    assert 'This player already has a file called' in copied(client, stick / 'film.mp4')
    assert (pi.media / 'FILM.mp4').read_bytes() == b'mine' and os.listdir(pi.media) == ['FILM.mp4']


def test_a_file_uploaded_meanwhile_is_kept(pi, stick, monkeypatch):
    real = shutil.copyfileobj

    def upload_meanwhile(src, dst, length):
        real(src, dst, length)
        (pi.media / 'film.mp4').write_bytes(b'uploaded')
    monkeypatch.setattr(system.shutil, 'copyfileobj', upload_meanwhile)
    with pytest.raises(FileExistsError):
        system.copy_to_media(str(stick / 'film.mp4'))
    assert os.listdir(pi.media) == ['film.mp4'] and (pi.media / 'film.mp4').read_bytes() == b'uploaded'


def test_copy_and_upload_rename_under_one_lock(pi, client, stick, monkeypatch):
    # the copy checks for the name and renames in one go: an upload of the same name finishing
    # meanwhile waits, so it isn't overwritten
    renamed = []
    real = os.replace

    def replace(src, dst):
        renamed.append(system.media_rename_lock.locked())
        real(src, dst)
    monkeypatch.setattr(system.os, 'replace', replace)
    system.copy_to_media(str(stick / 'film.mp4'))
    client.post('/upload', data={'file': (io.BytesIO(b'up'), 'upload.mp4')}, content_type='multipart/form-data')
    assert (pi.media / 'upload.mp4').read_bytes() == b'up'
    assert renamed == [True, True]
    # and a rename in the web interface (to the name a copy is about to use)
    held = []
    real_rename = os.rename
    monkeypatch.setattr(system.os, 'rename', lambda src, dst: (held.append(system.media_rename_lock.locked()),
                                                               real_rename(src, dst)))
    client.post('/rename', data={'filename': 'upload.mp4', 'new_name': 'renamed.mp4'})
    assert held == [True] and (pi.media / 'renamed.mp4').exists()


@pytest.mark.parametrize('name', ['b.mp4', '../usb0/film.mp4', '/etc/passwd'])
def test_copy_only_from_usb_sticks(pi, client, stick, name):
    (pi.media / 'b.mp4').write_bytes(b'b')
    path = name if name.startswith('/') else str(pi.media / name)
    assert "That file isn&#39;t on a USB stick any more." in copied(client, path)
    assert sorted(os.listdir(pi.media)) == ['b.mp4']


def test_copy_refuses_links_and_names_exfat_cant_store(pi, client, stick, tmp_path):
    secret = tmp_path / 'secret.txt'
    secret.write_text('secret')
    os.symlink(str(secret), str(stick / 'x.mp4'))
    (stick / 'a:b.mp4').write_bytes(b'x')
    assert "&#39;x.mp4&#39; is a link, not a file." in copied(client, stick / 'x.mp4')
    assert "characters in its name the player can&#39;t store" in copied(client, stick / 'a:b.mp4')
    # and the copy itself never follows a link
    with pytest.raises(OSError):
        system.copy_to_media(str(stick / 'x.mp4'))
    assert os.listdir(pi.media) == []


def test_copy_without_space_leaves_nothing(pi, client, stick, monkeypatch):
    monkeypatch.setattr(system, 'get_free_space', lambda: 1000)
    assert 'Not enough space to copy' in copied(client, stick / 'film.mp4')
    assert os.listdir(pi.media) == []


def test_copy_that_fails_part_way_leaves_nothing(pi, client, stick, monkeypatch):
    def broken(src, dst, length):
        dst.write(b'half')
        raise OSError(5, 'Input/output error')
    monkeypatch.setattr(system.shutil, 'copyfileobj', broken)
    assert 'Input/output error' in copied(client, stick / 'film.mp4')
    assert os.listdir(pi.media) == []


def test_media_types_are_the_same_in_the_player(pi):
    import ast
    tree = ast.parse((REPO_PLAYER).read_text())
    player = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                  and getattr(node.targets[0], 'id', None) == 'MEDIA_TYPES')
    assert set(ast.literal_eval(player)) == set(system.VIDEO_TYPES + system.IMAGE_TYPES + system.AUDIO_TYPES)


def test_playlist_for_a_player_edited_before_media_types(pi, client):
    # it plays every file with an extension: the playlist shows them
    with open(system.SCRIPT_FILE, 'w') as f:
        f.write('# edited\nMEDIA_FILES = "/media/*/*.*"\n')
    usb = pi.media.parent / 'usb0'
    usb.mkdir()
    (usb / 'config.txt').write_text('x')
    (pi.media / 'notes.txt').write_text('x')
    assert [(e['name'], e['plays']) for e in system.get_playlist()] == [('notes.txt', True), ('config.txt', True)]
