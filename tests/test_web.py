"""Web interface: login, media files, settings, player controls.

Part of https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.
"""
import io
import json
import os
import signal
import time

import system
import webservice


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
    assert r.status_code == 200 and b'--bg-primary' in r.data
    assert c.get('/static/mp4museum.js').status_code == 200
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
    monkeypatch.setattr(system, 'run_command', lambda cmd: (True, IP_OUTPUT) if cmd[:2] == ['ip', '-o'] else pi.run_command(cmd))
    text = system.get_network_status()
    assert 'enxb827eb4e4fd4 (wired): connected' in text
    assert 'IPv4 192.168.1.120/24' in text and 'MAC address: b8:27:eb:4e:4f:d4' in text
    assert 'wlan0 (wireless): not connected' in text and 'No IP address' in text
    assert '127.0.0.1' not in text and '\nlo ' not in text
    assert '<h3>Network</h3>' in client.get('/').data.decode()


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
    assert 'Playing intro.mp4 (internal) for 2 min' in html and 'Pause / Resume' in html

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
