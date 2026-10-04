"""Network settings: fixed addresses, Wi-Fi networks and country (network.py, System tab).

Part of https://github.com/lotech/mp4museum (added 2026). Licensed under the GNU GPL v3, see LICENSE.
"""
import json
import os

import pytest

import network
import system
import webservice


def interface(pi, name, wireless=False, state='up'):
    folder = pi.root / 'net' / name
    folder.mkdir(parents=True)
    if wireless:
        (folder / 'wireless').mkdir()
    (folder / 'operstate').write_text(state + '\n')
    (folder / 'address').write_text('b8:27:eb:00:00:01\n')


@pytest.fixture
def ports(pi):
    """A Pi 3 B: its Ethernet port (named by its MAC address, as on the image) and Wi-Fi."""
    interface(pi, 'enxb827eb4e4fd4')
    interface(pi, 'wlan0', wireless=True, state='down')
    return pi


def commands(pi):
    """What was run, other than remounting /boot to save the settings."""
    return [cmd for cmd in pi.commands if cmd[0] != 'mount']


def keep(pi, client):
    """Keep on the page, once the change has been applied."""
    pi.run_later(network.APPLY_DELAY)
    return client.post('/network/keep')


def dhcpcd(pi):
    return open(network.DHCPCD_CONF).read()


def wpa(pi):
    return open(os.path.join(network.WPA_CONF_DIR, 'wpa_supplicant.conf')).read()


def saved(pi):
    path = network.SETTINGS_FILE
    return json.load(open(path)) if os.path.exists(path) else None


FIXED = {'interfaces': {'enxb827eb4e4fd4': {'mode': 'static', 'address': '192.168.1.50/24',
                                            'router': '192.168.1.1', 'dns': []}}}


# ----- Checking what was typed ----- #
def test_fixed_address_checked():
    assert network.static_setting('10.0.0.5', '10.0.0.1', '1.1.1.1, 8.8.8.8') == {
        'mode': 'static', 'address': '10.0.0.5/24', 'router': '10.0.0.1', 'dns': ['1.1.1.1', '8.8.8.8']}
    # a cable straight to a computer: no router
    assert network.static_setting('192.168.2.2/16')['router'] == ''
    for address, router in (('', ''), ('192.168.1.300/24', ''), ('192.168.1.0/24', ''),
                            ('192.168.1.255/24', ''), ('127.0.0.5/8', ''), ('192.168.1.5/31', ''),
                            ('192.168.1.5/24', '192.168.2.1'), ('192.168.1.5/24', '192.168.1.5'),
                            ('fe80::1/64', ''), ('192.168.1.5/24', 'router')):
        with pytest.raises(ValueError):
            network.static_setting(address, router)
    with pytest.raises(ValueError):
        network.static_setting('192.168.1.5/24', '', 'dns.example.com')


def test_wifi_password_kept_as_its_key():
    # the IEEE 802.11i test vector, as wpa_passphrase works it out
    assert network.wifi_psk('IEEE', 'password') == 'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e'
    assert network.wifi_network('Gallery', password='12345678') == {'ssid': 'Gallery', 'psk': network.wifi_psk('Gallery', '12345678')}
    assert network.wifi_network('Café', hidden=True) == {'ssid': 'Café', 'hidden': True}
    for ssid, password in (('', 'password'), ('x' * 33, 'password'), ('Gallery', 'short'),
                           ('Gallery', 'x' * 64), ('Gallery', 'pässwörd')):
        with pytest.raises(ValueError):
            network.wifi_network(ssid, password=password)


def test_settings_file_written_by_hand(pi):
    """Wi-Fi can be set up on a computer before the first start: the password is used as its key."""
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump({'wifi': {'country': 'gb', 'networks': [{'ssid': 'Gallery', 'password': 'secret-password'},
                                                          {'ssid': '', 'password': 'nothing'}]},
                   'interfaces': {'eth0': {'mode': 'static', 'address': 'nonsense'}, 'eth1': {'mode': 'dhcp'}}}, f)
    assert network.read_settings() == {'wifi': {'enabled': True, 'country': 'GB', 'networks': [
        {'ssid': 'Gallery', 'psk': network.wifi_psk('Gallery', 'secret-password')}]}}
    with open(network.SETTINGS_FILE, 'w') as f:
        f.write('{not json')
    assert network.read_settings() == {}


def test_settings_file_with_the_wrong_types(pi, client):
    """A file written by hand can have anything in it: what can't be used is left out."""
    for data in ({'interfaces': ['eth0']}, {'interfaces': {'eth0': {'mode': 'static', 'address': 5, 'router': 5}}},
                 {'wifi': {'networks': 5}}, {'wifi': {'networks': [5, {'ssid': 'Gallery', 'password': 12345678}]}},
                 {'interfaces': {'eth0': {'mode': 'static', 'address': '10.0.0.5/24', 'dns': 5}}}, ['x'], 'x'):
        with open(network.SETTINGS_FILE, 'w') as f:
            json.dump(data, f)
        network.read_settings()
        assert client.get('/').status_code == 200
    assert network.read_settings() == {}
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump({'interfaces': {'eth0': {'mode': 'static', 'address': '10.0.0.5/24', 'dns': 5}},
                   'wifi': {'networks': [{'ssid': 'Gallery', 'password': 12345678}]}}, f)
    # (DNS server 5 isn't an address: that address is left out, the rest is used)
    assert 'interfaces' not in network.read_settings()
    assert network.read_settings()['wifi']['networks'] == [{'ssid': 'Gallery', 'psk': network.wifi_psk('Gallery', '12345678')}]


# ----- At start ----- #
def test_nothing_changes_without_settings(ports):
    before = dhcpcd(ports), wpa(ports)
    network.apply_at_start()
    assert (dhcpcd(ports), wpa(ports)) == before and ports.commands == []


def test_fixed_address_used_at_start(ports):
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump(FIXED, f)
    network.apply_at_start()
    text = dhcpcd(ports)
    # the image's lines are kept, the address added at the end
    assert text.startswith('hostname\nclientid\npersistent\nslaac private\n\n' + network.BLOCK_START)
    assert ('interface enxb827eb4e4fd4\nstatic ip_address=192.168.1.50/24\nstatic routers=192.168.1.1\n'
            'static domain_name_servers=192.168.1.1\n') in text
    assert ['dhcpcd', '-n', 'enxb827eb4e4fd4'] in ports.commands
    # Wi-Fi isn't set up: left as it is
    assert wpa(ports) == network.DEFAULT_WPA_CONF and not [c for c in ports.commands if c[0] in ('rfkill', 'wpa_cli')]

    # with the overlay off, the next start finds it there already: nothing to do
    ports.commands.clear()
    network.apply_at_start()
    assert dhcpcd(ports) == text and ports.commands == []


def test_settings_file_deleted_with_the_overlay_off(ports):
    """Deleting mp4m-network.json goes back to DHCP, even if /etc kept the settings."""
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump(dict(FIXED, wifi={'networks': [{'ssid': 'Gallery', 'password': 'secret-password'}]}), f)
    network.apply_at_start()
    os.remove(network.SETTINGS_FILE)
    ports.commands.clear()
    network.apply_at_start()
    assert dhcpcd(ports) == 'hostname\nclientid\npersistent\nslaac private\n'
    assert ['ip', 'addr', 'del', '192.168.1.50/24', 'dev', 'enxb827eb4e4fd4'] in ports.commands
    assert ['dhcpcd', '-n', 'enxb827eb4e4fd4'] in ports.commands
    assert wpa(ports) == network.DEFAULT_WPA_CONF


def test_wifi_without_a_country_uses_the_channels_allowed_everywhere(ports):
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump({'wifi': {'networks': [{'ssid': 'Gallery "1"\nnetwork={', 'password': 'secret-password'},
                                         {'ssid': 'Open', 'hidden': True}]}}, f)
    network.apply_at_start()
    text = wpa(ports)
    channels = 'freq_list=2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462'
    assert text.startswith(network.WPA_HEADER) and 'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev' in text
    assert 'country=' not in text and text.count(channels) == 3
    # names in hexadecimal: no character in them can break the file; only the key, not the password
    assert '\tssid=' + 'Gallery "1"\nnetwork={'.encode().hex() in text and 'secret-password' not in text
    assert '\tpsk=' + network.wifi_psk('Gallery "1"\nnetwork={', 'secret-password') in text
    assert '\tssid=' + b'Open'.hex() + '\n\tscan_ssid=1\n\tkey_mgmt=NONE' in text
    # Raspberry Pi OS blocks Wi-Fi until a country is set: unblocked, on the world's channels
    assert ports.commands[:3] == [['rfkill', 'unblock', 'wifi'], ['iw', 'reg', 'set', '00'],
                                  ['wpa_cli', '-i', 'wlan0', 'reconfigure']]
    assert oct(os.stat(network.wpa_conf_path()).st_mode & 0o777) == '0o600'


def test_wifi_with_a_country(ports):
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump({'wifi': {'country': 'DE', 'networks': [{'ssid': 'Gallery', 'password': 'secret-password'}]}}, f)
    network.apply_at_start()
    assert 'country=DE\n' in wpa(ports) and 'freq_list' not in wpa(ports)
    assert ['iw', 'reg', 'set', 'DE'] in ports.commands


def test_wifi_off(ports):
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump({'wifi': {'enabled': False, 'networks': []}}, f)
    network.apply_at_start()
    assert ports.commands == [['rfkill', 'block', 'wifi']]


def test_wifi_turned_off_and_on_again(ports, client):
    with open(network.SETTINGS_FILE, 'w') as f:
        json.dump({'wifi': {'networks': [{'ssid': 'Gallery', 'password': 'secret-password'}]}}, f)
    network.apply_at_start()
    ports.commands.clear()
    client.post('/network/wifi/power', data={'enabled': 'off'})
    ports.run_later(network.APPLY_DELAY)
    assert commands(ports) == [['rfkill', 'block', 'wifi']]
    client.post('/network/wifi/power', data={'enabled': 'on'})
    ports.run_later(network.APPLY_DELAY)
    assert ['rfkill', 'unblock', 'wifi'] in commands(ports)
    assert ports.commands[-1] == ['wpa_cli', '-i', 'wlan0', 'reconfigure']


def test_first_wifi_setup_undone(ports):
    blocked(ports)
    network.change({'wifi': {'networks': [{'ssid': 'Gallery', 'password': 'secret-password'}]}})
    ports.run_later(network.APPLY_DELAY)
    assert ['rfkill', 'unblock', 'wifi'] in ports.commands
    ports.commands.clear()
    network.undo()
    # blocked again, as on the image
    assert wpa(ports) == network.DEFAULT_WPA_CONF and ports.commands[0] == ['rfkill', 'block', 'wifi']


def test_wifi_set_up_some_other_way_comes_back(ports):
    """e.g. by hand with the overlay off: undoing the first Wi-Fi change here puts it back."""
    by_hand = network.DEFAULT_WPA_CONF + 'country=DE\nnetwork={\n\tssid="Studio"\n\tpsk="studio-password"\n}\n'
    with open(network.wpa_conf_path(), 'w') as f:
        f.write(by_hand)
    blocked(ports, soft='0')
    network.change({'wifi': {'networks': [{'ssid': 'Gallery', 'password': 'secret-password'}]}})
    ports.run_later(network.APPLY_DELAY)
    assert wpa(ports).startswith(network.WPA_HEADER)
    ports.commands.clear()
    network.undo()
    assert wpa(ports) == by_hand and not os.path.exists(network.WIFI_BEFORE_FILE)
    # it wasn't blocked: it isn't now
    assert ['rfkill', 'block', 'wifi'] not in ports.commands and ['rfkill', 'unblock', 'wifi'] in ports.commands


def test_nothing_changed(ports, client):
    client.post('/network/address', data={'interface': 'enxb827eb4e4fd4', 'mode': 'dhcp'})
    assert network.pending() is None and 'Nothing changed.' in client.get('/').data.decode()


def test_wpa_supplicant_started_if_it_isnt_running(ports, monkeypatch):
    def run(cmd, timeout=None):
        ports.commands.append(cmd)
        return (False, 'Failed to connect') if cmd[0] == 'wpa_cli' else (True, '')
    monkeypatch.setattr(system, 'run_command', run)
    network._apply({'wifi': {'enabled': True, 'country': '', 'networks': []}})
    assert ports.commands[-1] == ['wpa_supplicant', '-B', '-i', 'wlan0', '-c', network.wpa_conf_path(), '-D', 'nl80211,wext']


# ----- Changes from the web interface ----- #
def test_change_is_tried_then_kept(ports):
    network.change(FIXED)
    # the page is sent first; nothing saved yet
    assert ports.commands == [] and saved(ports) is None and network.pending()['seconds'] > 290
    ports.run_later(network.APPLY_DELAY)
    assert ['dhcpcd', '-n', 'enxb827eb4e4fd4'] in ports.commands and 'ip_address=192.168.1.50/24' in dhcpcd(ports)
    assert saved(ports) is None
    assert network.keep() and network.pending() is None
    assert saved(ports) == FIXED and ports.read_only
    # the timer that would have gone back is gone
    assert ports.later == []


def test_kept_only_once_applied(ports, client, monkeypatch):
    """Keep from another tab before the new settings are in use (or while they're being
    applied) would save settings nobody has reached the player with."""
    network.change(FIXED)
    with pytest.raises(network.NotApplied):
        network.keep()
    # being applied: the commands haven't finished
    def run(cmd, timeout=None):
        if cmd[0] == 'dhcpcd':
            client.post('/network/keep')
        return ports.run_command(cmd)
    monkeypatch.setattr(system, 'run_command', run)
    ports.run_later(network.APPLY_DELAY)
    assert saved(ports) is None and network.pending()['applied']
    assert 'still being applied' in client.get('/').data.decode()
    assert network.keep() and saved(ports) == FIXED


def test_change_not_kept_goes_back(ports):
    network.change(FIXED)
    ports.run_later()
    assert network.pending() is None and saved(ports) is None
    assert 'ip_address' not in dhcpcd(ports)
    assert ['ip', 'addr', 'del', '192.168.1.50/24', 'dev', 'enxb827eb4e4fd4'] in ports.commands
    assert ports.commands[-1] == ['dhcpcd', '-n', 'enxb827eb4e4fd4']


def test_second_change_goes_back_to_the_saved_settings(ports):
    network.change(FIXED)
    ports.run_later(network.APPLY_DELAY)
    other = {'interfaces': {'enxb827eb4e4fd4': dict(FIXED['interfaces']['enxb827eb4e4fd4'], address='192.168.1.60/24')}}
    network.change(other)
    assert len(ports.later) == 2
    ports.run_later(network.APPLY_DELAY)
    assert 'ip_address=192.168.1.60/24' in dhcpcd(ports)
    assert network.undo()
    assert 'ip_address' not in dhcpcd(ports) and saved(ports) is None


def test_undo_right_after_a_second_change(ports):
    """The first change is in use while the second one waits to be applied: undo goes back."""
    network.change(FIXED)
    ports.run_later(network.APPLY_DELAY)
    network.change({})
    network.undo()
    assert 'ip_address' not in dhcpcd(ports) and network.pending() is None


def test_an_address_for_the_next_start_waits_for_it(ports, client):
    network.apply_at_start()
    network.save_for_next_start(FIXED)
    page = client.get('/').data.decode()
    assert 'From the next start: 192.168.1.50/24' in page
    # another change: the saved address isn't used yet, nor when that change is undone or kept
    network.change({'interfaces': FIXED['interfaces'], 'wifi': {'country': 'GB', 'networks': []}})
    ports.run_later(network.APPLY_DELAY)
    assert 'ip_address' not in dhcpcd(ports) and 'country=GB' in wpa(ports)
    network.undo()
    assert 'ip_address' not in dhcpcd(ports)
    network.change({'interfaces': FIXED['interfaces'], 'wifi': {'country': 'GB', 'networks': []}})
    ports.run_later(network.APPLY_DELAY)
    network.keep()
    assert 'ip_address' not in dhcpcd(ports) and saved(ports)['interfaces'] == FIXED['interfaces']
    assert network.next_start_interfaces() == {'enxb827eb4e4fd4'}
    # the next start uses it (/run is cleared)
    ports.commands.clear()
    os.remove(network.NEXT_START_FILE)
    network.apply_at_start()
    assert 'ip_address=192.168.1.50/24' in dhcpcd(ports) and network.next_start_interfaces() == set()


def restart_web_interface():
    network._next_start.clear()
    network._in_use['settings'] = None
    network._pending.clear()


def test_an_address_for_the_next_start_waits_while_the_web_interface_restarts(ports):
    """e.g. after an update: the Pi hasn't started again, so the address isn't used yet."""
    network.apply_at_start()
    network.save_for_next_start(FIXED)
    restart_web_interface()
    network.apply_at_start()
    assert 'ip_address' not in dhcpcd(ports) and network.next_start_interfaces() == {'enxb827eb4e4fd4'}
    # the Pi starting again clears /run
    os.remove(network.NEXT_START_FILE)
    restart_web_interface()
    network.apply_at_start()
    assert 'ip_address=192.168.1.50/24' in dhcpcd(ports) and network.next_start_interfaces() == set()


def test_an_address_for_the_next_start_changed_now(ports):
    network.apply_at_start()
    network.save_for_next_start(FIXED)
    other = {'interfaces': {'enxb827eb4e4fd4': dict(FIXED['interfaces']['enxb827eb4e4fd4'], address='192.168.1.60/24')}}
    network.change(other)
    ports.run_later(network.APPLY_DELAY)
    assert 'ip_address=192.168.1.60/24' in dhcpcd(ports)
    network.keep()
    assert network.next_start_interfaces() == set() and not os.path.exists(network.NEXT_START_FILE)


def test_apply_an_address_saved_for_the_next_start(ports, client):
    """Apply on the address shown (saved for the next start) uses it now."""
    network.apply_at_start()
    network.save_for_next_start(FIXED)
    form = {'interface': 'enxb827eb4e4fd4', 'mode': 'static', 'address': '192.168.1.50/24',
            'router': '192.168.1.1', 'when': 'now'}
    client.post('/network/address', data=form)
    ports.run_later(network.APPLY_DELAY)
    assert 'ip_address=192.168.1.50/24' in dhcpcd(ports)
    assert 'From the next start' not in client.get('/').data.decode()
    # not kept: still for the next start only
    network.undo()
    assert 'ip_address' not in dhcpcd(ports) and network.next_start_interfaces() == {'enxb827eb4e4fd4'}
    client.post('/network/address', data=form)
    keep(ports, client)
    assert 'ip_address=192.168.1.50/24' in dhcpcd(ports) and network.next_start_interfaces() == set()
    assert saved(ports) == FIXED


def test_network_commands_have_a_time_limit(ports, monkeypatch):
    """A command that hangs mustn't keep the previous settings from coming back."""
    limits = []
    monkeypatch.setattr(system, 'run_command', lambda cmd, timeout=None: (limits.append(timeout), (True, ''))[1])
    network.change(FIXED)
    ports.run_later()
    assert limits and all(limit == network.COMMAND_TIMEOUT for limit in limits)


def test_run_command_stops_a_command_that_hangs():
    ok, output = system.run_command(['sleep', '5'], timeout=0.2)
    assert not ok and 'stopped after 0.2 seconds' in output


def test_save_for_the_next_start_fails_without_its_list(ports, monkeypatch):
    """Without the list in /run, a restart of the web interface would use the address now."""
    network.apply_at_start()
    monkeypatch.setattr(network, 'NEXT_START_FILE', str(ports.root / 'missing' / 'next-start.json'))
    with pytest.raises(OSError):
        network.save_for_next_start(FIXED)
    assert saved(ports) is None and network.next_start_interfaces() == set()


def test_save_for_the_next_start(ports):
    network.save_for_next_start(FIXED)
    assert saved(ports) == FIXED and commands(ports) == [] and 'ip_address' not in dhcpcd(ports)
    network.change({})
    with pytest.raises(RuntimeError):
        network.save_for_next_start({})
    # back to DHCP: no file
    ports.run_later(network.APPLY_DELAY)
    network.keep()
    assert saved(ports) is None


# ----- The page ----- #
def test_network_cards(ports, client):
    page = client.get('/').data.decode()
    assert 'Ethernet</strong> <span class="hint">enxb827eb4e4fd4' in page
    assert 'Wi-Fi</h3>' in page and 'Not set: channels allowed everywhere' in page
    assert '<option value="GB">Britain (UK)</option>' in page and 'networkBar' not in page


def test_fixed_address_from_the_page(ports, client):
    r = client.post('/network/address', data={'interface': 'enxb827eb4e4fd4', 'mode': 'static',
                                              'address': '192.168.1.50', 'router': '192.168.1.1', 'when': 'now'},
                    base_url='http://192.168.1.120')
    page = r.data.decode()
    # the old address may stop working: inline CSS, and where to find the player
    assert '<style>' in page and '/static/style.css' not in page
    for address in ('http://192.168.1.120/', 'http://%s.local/' % webservice.socket.gethostname(), 'http://192.168.1.50/'):
        assert address in page
    ports.run_later(network.APPLY_DELAY)
    page = client.get('/').data.decode()
    assert 'networkBar' in page and 'come back in <strong id="networkSeconds">5:0' in page
    keep(ports, client)
    assert saved(ports) == FIXED
    assert 'networkBar' not in client.get('/').data.decode()


def test_wrong_address_refused(ports, client):
    client.post('/network/address', data={'interface': 'enxb827eb4e4fd4', 'mode': 'static',
                                          'address': '192.168.1.50/24', 'router': '10.0.0.1'})
    assert network.pending() is None
    assert 'has to be another address in 192.168.1.0/24' in client.get('/').data.decode()
    client.post('/network/address', data={'interface': 'eth0\nstatic', 'mode': 'dhcp'})
    assert network.pending() is None


def test_save_for_the_next_start_from_the_page(ports, client):
    client.post('/network/address', data={'interface': 'enxb827eb4e4fd4', 'mode': 'static',
                                          'address': '192.168.1.50/24', 'router': '192.168.1.1', 'when': 'later'})
    assert saved(ports) == FIXED and network.pending() is None and commands(ports) == []
    client.post('/network/address', data={'interface': 'enxb827eb4e4fd4', 'mode': 'dhcp', 'when': 'later'})
    assert saved(ports) is None


def test_wifi_from_the_page(ports, client):
    client.post('/network/wifi/add', data={'ssid': 'Gallery', 'password': 'secret-password'})
    ports.run_later(network.APPLY_DELAY)
    assert 'secret-password' not in wpa(ports) and network.wifi_psk('Gallery', 'secret-password') in wpa(ports)
    keep(ports, client)
    assert saved(ports) == {'wifi': {'enabled': True, 'country': '', 'networks': [
        {'ssid': 'Gallery', 'psk': network.wifi_psk('Gallery', 'secret-password')}]}}
    assert 'secret-password' not in open(network.SETTINGS_FILE).read()

    # changing it without typing the password again keeps it
    client.post('/network/wifi/add', data={'ssid': 'Gallery', 'hidden': '1'})
    keep(ports, client)
    assert saved(ports)['wifi']['networks'] == [{'ssid': 'Gallery', 'psk': network.wifi_psk('Gallery', 'secret-password'),
                                                 'hidden': True}]
    # a new network needs a password, or No password
    client.post('/network/wifi/add', data={'ssid': 'Open'})
    assert network.pending() is None
    client.post('/network/wifi/add', data={'ssid': 'Open', 'open': '1'})
    keep(ports, client)
    assert [n['ssid'] for n in saved(ports)['wifi']['networks']] == ['Gallery', 'Open']

    client.post('/network/wifi/country', data={'country': 'XX'})
    assert network.pending() is None
    client.post('/network/wifi/country', data={'country': 'GB'})
    keep(ports, client)
    assert saved(ports)['wifi']['country'] == 'GB' and 'country=GB' in wpa(ports)

    client.post('/network/wifi/forget', data={'ssid': 'Gallery'})
    client.post('/network/wifi/power', data={'enabled': 'off'})
    keep(ports, client)
    assert saved(ports)['wifi'] == {'enabled': False, 'country': 'GB', 'networks': [{'ssid': 'Open'}]}
    assert ['rfkill', 'block', 'wifi'] in ports.commands


SCAN = """BSS aa:bb:cc:dd:ee:01(on wlan0)
\tfreq: 2437
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -71.00 dBm
\tSSID: Gallery
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:02(on wlan0)
\tfreq: 2412
\tsignal: -48.00 dBm
\tSSID: Gallery
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:03(on wlan0)
\tfreq: 2462
\tcapability: ESS ShortSlotTime (0x0401)
\tsignal: -60.00 dBm
\tSSID: Caf\\xc3\\xa9\\x20
BSS aa:bb:cc:dd:ee:04(on wlan0)
\tfreq: 2422
\tsignal: -30.00 dBm
\tSSID:
"""


def test_scan(ports, client, monkeypatch):
    def run(cmd, timeout=None):
        ports.commands.append(cmd)
        return (True, SCAN) if cmd[:2] == ['iw', 'dev'] else (True, '')
    monkeypatch.setattr(system, 'run_command', run)
    answer = client.get('/network/scan', headers={'X-Requested-With': 'fetch'}).get_json()
    # one per name, the strongest first, hidden networks left out
    assert answer['networks'] == [{'ssid': 'Gallery', 'signal': -48.0, 'frequency': 2412, 'secure': True},
                                  {'ssid': 'Café ', 'signal': -60.0, 'frequency': 2462, 'secure': False}]
    # no country: only on the channels allowed everywhere
    scan = next(cmd for cmd in ports.commands if cmd[:4] == ['iw', 'dev', 'wlan0', 'scan'])
    assert scan[4:] == ['freq'] + [str(f) for f in network.SAFE_FREQUENCIES]


def blocked(pi, soft='1'):
    folder = pi.root / 'rfkill' / 'rfkill0'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'type').write_text('wlan\n')
    (folder / 'soft').write_text(soft + '\n')
    (folder / 'hard').write_text('0\n')


def test_scan_leaves_wifi_blocked_if_it_isnt_set_up(ports, client):
    blocked(ports)
    client.get('/network/scan')
    assert ports.commands[0] == ['rfkill', 'unblock', 'wifi'] and ports.commands[-1] == ['rfkill', 'block', 'wifi']


def test_wifi_off_on_the_image(ports, client):
    blocked(ports)
    page = client.get('/').data.decode()
    assert 'Off: add a network to turn it on' in page and 'Turn Wi-Fi on' in page
    blocked(ports, soft='0')
    assert 'Turn Wi-Fi off' in client.get('/').data.decode()


def test_no_wifi(pi, client):
    interface(pi, 'eth0')
    assert 'This Pi has no Wi-Fi.' in client.get('/').data.decode()
    assert client.get('/network/scan').status_code == 409


# ----- Copies ----- #
def test_clone_leaves_fixed_addresses_out(tmp_path):
    import clone
    path = tmp_path / 'mp4m-network.json'
    path.write_text(json.dumps(dict(FIXED, wifi={'networks': [{'ssid': 'Gallery', 'password': 'secret-password'}]})))
    clone._without_fixed_addresses(str(path))
    assert json.loads(path.read_text()) == {'wifi': {'enabled': True, 'country': '', 'networks': [
        {'ssid': 'Gallery', 'psk': network.wifi_psk('Gallery', 'secret-password')}]}}
    path.write_text(json.dumps(FIXED))
    clone._without_fixed_addresses(str(path))
    assert not path.exists()
