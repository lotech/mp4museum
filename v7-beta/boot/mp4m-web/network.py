"""Network settings for the MP4MUSEUM web interface (System tab): a fixed address or DHCP for
each Ethernet port and Wi-Fi, the Wi-Fi networks to join and the Wi-Fi country.

Part of https://github.com/lotech/mp4museum (added 2026), a fork of MP4MUSEUM by Julius
Schmiedel. Licensed under the GNU GPL v3, see LICENSE.

/ is a RAM overlay, so the settings are kept on /boot (mp4m-network.json) and written into
/etc/dhcpcd.conf and wpa_supplicant.conf whenever the web interface starts. Without that file
nothing is changed: the player uses DHCP, and Wi-Fi stays as the image has it (off).

A change made in the web interface is applied straight away, but only saved to /boot when it is
kept (Keep on the page, opened again at the new address): otherwise the previous settings come
back after KEEP_SECONDS, and after a reboot anyway. A wrong fixed address can't lose the player.

Without a country, Wi-Fi only uses 2.4 GHz channels 1-11, which every country allows (the Pi 3 B's
Wi-Fi firmware would otherwise use all channels); choosing the country allows all of its channels.
"""
import hashlib
import ipaddress
import json
import os
import re
import threading
import time

import system

SETTINGS_FILE = os.path.join(system.BOOT_PATH, 'mp4m-network.json')
DHCPCD_CONF = '/etc/dhcpcd.conf'
WPA_CONF_DIR = '/etc/wpa_supplicant'
RESOLV_CONF = '/etc/resolv.conf'
# country codes and names (tzdata), for the Wi-Fi country list
ISO3166_FILE = '/usr/share/zoneinfo/iso3166.tab'
RFKILL_PATH = '/sys/class/rfkill'

BLOCK_START = '# --- mp4museum network settings (set by the web interface, from /boot/mp4m-network.json) ---'
BLOCK_END = '# --- end of mp4museum network settings ---'
WPA_HEADER = '# Written by the MP4MUSEUM web interface from /boot/mp4m-network.json: changes here are lost.\n'
# what the image has (Wi-Fi without networks)
DEFAULT_WPA_CONF = 'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n'

# 2.4 GHz channels 1-11, allowed in every country: all Wi-Fi uses while no country is chosen
SAFE_FREQUENCIES = tuple(2412 + 5 * channel for channel in range(11))
# how long a change waits to be kept before the previous settings come back
KEEP_SECONDS = 300
# the page saying what happens is sent first: the change may cut the browser off
APPLY_DELAY = 2
MAX_WIFI_NETWORKS = 10
# seconds before a network command is stopped (a scan takes a few)
COMMAND_TIMEOUT = 30

INTERFACE_RE = re.compile(r'^[A-Za-z0-9_.-]{1,15}$')
COUNTRY_RE = re.compile(r'^[A-Z]{2}$')


# ----- Settings ----- #
def static_setting(address, router='', dns=''):
    """{'mode': 'static', 'address': '192.168.1.50/24', 'router', 'dns': [...]} from what was typed,
    or ValueError with what's wrong. Without a prefix the address gets /24."""
    address = (address or '').strip()
    if not address:
        raise ValueError("Enter the address, e.g. 192.168.1.50/24.")
    if '/' not in address:
        address += '/24'
    try:
        interface = ipaddress.IPv4Interface(address)
    except ValueError:
        raise ValueError(f"{address} isn't an address like 192.168.1.50/24.")
    network = interface.network
    ip = interface.ip
    if network.prefixlen < 8 or network.prefixlen > 30:
        raise ValueError("The prefix after / should be from 8 to 30 (24 for most networks).")
    if (ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local or ip.is_reserved
            or ip in (network.network_address, network.broadcast_address)):
        raise ValueError(f"{ip} can't be used for the player in {network}.")
    setting = {'mode': 'static', 'address': str(interface), 'router': '', 'dns': []}
    router = (router or '').strip()
    if router:
        try:
            router = ipaddress.IPv4Address(router)
        except ValueError:
            raise ValueError(f"The router {router} isn't an address like 192.168.1.1.")
        if router not in network or router == ip or router in (network.network_address, network.broadcast_address):
            raise ValueError(f"The router has to be another address in {network}.")
        setting['router'] = str(router)
    if not isinstance(dns, (list, tuple)):
        dns = str(dns or '').replace(',', ' ').split()
    for server in dns or []:
        try:
            server = ipaddress.IPv4Address(str(server).strip())
        except ValueError:
            raise ValueError(f"The DNS server {server} isn't an address like 192.168.1.1.")
        if str(server) not in setting['dns']:
            setting['dns'].append(str(server))
    if len(setting['dns']) > 3:
        raise ValueError("Enter up to 3 DNS servers.")
    return setting

def wifi_psk(ssid, password):
    """The key wpa_supplicant uses for a WPA password (as wpa_passphrase works it out), so the
    password itself isn't kept on /boot, which anyone with the card can read."""
    return hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()

def wifi_network(ssid, password=None, psk=None, hidden=False):
    """{'ssid', 'psk'} (no psk: an open network; 'hidden': True to look for it by name), or
    ValueError with what's wrong."""
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
        raise ValueError("A Wi-Fi network name has 1 to 32 characters.")
    network = {'ssid': ssid}
    if psk:
        if not re.fullmatch(r'[0-9a-fA-F]{64}', str(psk)):
            raise ValueError(f"The key for {ssid} isn't 64 hexadecimal digits.")
        network['psk'] = psk.lower()
    elif password:
        if re.fullmatch(r'[0-9a-fA-F]{64}', password):
            # (wpa_supplicant takes 64 hexadecimal digits as the key itself)
            network['psk'] = password.lower()
        elif not 8 <= len(password) <= 63 or not all(32 <= ord(c) <= 126 for c in password):
            raise ValueError("A Wi-Fi password has 8 to 63 characters: letters, numbers, spaces and "
                             "punctuation, without accents.")
        else:
            network['psk'] = wifi_psk(ssid, password)
    if hidden:
        network['hidden'] = True
    return network

def normalize(data):
    """Settings with anything unusable left out (said in the log): {'interfaces': {name: static
    setting}, 'wifi': {'enabled', 'country', 'networks'}}, each only if there is one. Interfaces
    not listed use DHCP; without 'wifi', Wi-Fi is left as the system has it. A password written
    into the file by hand (instead of its psk) is turned into its psk."""
    settings = {}
    if not isinstance(data, dict):
        return settings
    interfaces = {}
    found = data.get('interfaces')
    for name, value in (found.items() if isinstance(found, dict) else []):
        if not isinstance(value, dict) or value.get('mode') != 'static':
            continue
        try:
            if not INTERFACE_RE.match(str(name)):
                raise ValueError("not an interface name")
            dns = value.get('dns')
            interfaces[name] = static_setting(str(value.get('address') or ''), str(value.get('router') or ''),
                                              dns if isinstance(dns, list) else str(dns or ''))
        except (ValueError, TypeError, AttributeError) as e:
            print(f"Network settings: left out the address of {name}: {e}", flush=True)
    if interfaces:
        settings['interfaces'] = interfaces
    wifi = data.get('wifi')
    if isinstance(wifi, dict):
        networks = []
        found = wifi.get('networks')
        for network in (found if isinstance(found, list) else []):
            try:
                network = wifi_network(network.get('ssid'), password=str(network.get('password') or ''),
                                       psk=str(network.get('psk') or ''), hidden=network.get('hidden') is True)
            except (ValueError, AttributeError, TypeError) as e:
                print(f"Network settings: left out a Wi-Fi network: {e}", flush=True)
                continue
            # one per name: the last one wins
            networks = [n for n in networks if n['ssid'] != network['ssid']] + [network]
        country = str(wifi.get('country') or '').upper()
        settings['wifi'] = {'enabled': wifi.get('enabled') is not False,
                            'country': country if COUNTRY_RE.match(country) else '',
                            'networks': networks[-MAX_WIFI_NETWORKS:]}
    return settings

def read_settings():
    """The settings saved on /boot ({} without the file: DHCP, Wi-Fi left as it is)."""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
    except OSError:
        return {}
    except ValueError as e:
        print(f"Network settings: couldn't read {SETTINGS_FILE}, so they aren't used: {e}", flush=True)
        return {}
    return normalize(data)

def _save(settings):
    with system.writable(system.BOOT_PATH):
        if settings:
            system.write_file(SETTINGS_FILE, json.dumps(settings, indent=2, ensure_ascii=False) + '\n')
        elif os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)

def without_fixed_addresses(settings):
    """For a card copied from this player: its own address from DHCP, the same Wi-Fi."""
    return {key: value for key, value in settings.items() if key != 'interfaces'}


# ----- Writing them into /etc ----- #
def _read(path):
    try:
        with open(path, 'r') as f:
            return f.read()
    except OSError:
        return None

def _write_etc(path, text, mode):
    # (/etc is in the RAM overlay: written again at every start)
    system.write_file(path, text)
    os.chmod(path, mode)

def dhcpcd_block(settings):
    """The lines for /etc/dhcpcd.conf: one 'interface' section for each fixed address."""
    lines = []
    for name, setting in sorted((settings.get('interfaces') or {}).items()):
        lines += ['interface ' + name, 'static ip_address=' + setting['address']]
        if setting['router']:
            lines.append('static routers=' + setting['router'])
        # without DNS servers given: the router, as a home router answers them
        dns = setting['dns'] or ([setting['router']] if setting['router'] else [])
        if dns:
            lines.append('static domain_name_servers=' + ' '.join(dns))
    if not lines:
        return ''
    return '\n'.join([BLOCK_START] + lines + [BLOCK_END]) + '\n'

def split_dhcpcd_conf(text):
    """(dhcpcd.conf without this block, {interface: its lines in the block})."""
    kept, sections, inside, current = [], {}, False, None
    for line in text.splitlines(True):
        stripped = line.strip()
        if stripped == BLOCK_START:
            inside = True
            continue
        if stripped == BLOCK_END:
            inside, current = False, None
            continue
        if not inside:
            kept.append(line)
            continue
        if stripped.startswith('interface '):
            current = stripped.split()[1]
            sections[current] = []
        elif current and stripped:
            sections[current].append(stripped)
    base = ''.join(kept)
    if base and not base.endswith('\n'):
        base += '\n'
    return base, sections

def wpa_conf_path(interface='wlan0'):
    """The file wpa_supplicant uses (dhcpcd's hook prefers one for the interface, if there is one)."""
    own = os.path.join(WPA_CONF_DIR, f'wpa_supplicant-{interface}.conf')
    return own if os.path.exists(own) else os.path.join(WPA_CONF_DIR, 'wpa_supplicant.conf')

def wpa_conf(wifi):
    """wpa_supplicant.conf for these Wi-Fi settings. Names and keys are written in hexadecimal,
    so no character in them can break the file."""
    lines = [WPA_HEADER.rstrip('\n'), 'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev', 'update_config=1']
    if not wifi['enabled']:
        # (so turning it off or on changes this file, which is how _apply sees a change)
        lines.append('# Wi-Fi is off (rfkill block wifi)')
    frequencies = ' '.join(str(f) for f in SAFE_FREQUENCIES)
    if wifi['country']:
        lines.append('country=' + wifi['country'])
    else:
        # scanning too, not only joining
        lines.append('freq_list=' + frequencies)
    for network in wifi['networks']:
        lines += ['', 'network={', '\tssid=' + network['ssid'].encode().hex()]
        if network.get('hidden'):
            lines.append('\tscan_ssid=1')
        if network.get('psk'):
            lines.append('\tpsk=' + network['psk'])
        else:
            lines.append('\tkey_mgmt=NONE')
        if not wifi['country']:
            lines.append('\tfreq_list=' + frequencies)
        lines.append('}')
    return '\n'.join(lines) + '\n'

def wireless_interfaces():
    return [name for name in system.network_interfaces()
            if os.path.isdir(os.path.join(system.NET_PATH, name, 'wireless'))]

def _command(cmd):
    # bounded: a command that hangs mustn't hold up going back to the previous settings
    return system.run_command(cmd, timeout=COMMAND_TIMEOUT)

def _run(cmd, problems):
    ok, output = _command(cmd)
    if not ok:
        problems.append(f"{' '.join(cmd)}: {output or 'failed'}")
    return ok, output

def _apply(settings, at_start=False):
    """Write the settings into /etc and use them now. Returns what went wrong (a list of lines)."""
    problems = []
    current = _read(DHCPCD_CONF)
    if current is None:
        if settings.get('interfaces'):
            problems.append(f"{DHCPCD_CONF} not found: fixed addresses need dhcpcd.")
    else:
        base, old = split_dhcpcd_conf(current)
        block = dhcpcd_block(settings)
        if block:
            text = base.rstrip('\n') + '\n\n' + block
        else:
            # (the blank line put before the block goes with it)
            text = base.rstrip('\n') + '\n' if old else base
        _, new = split_dhcpcd_conf(text)
        if text != current:
            _write_etc(DHCPCD_CONF, text, 0o644)
        for name in sorted(set(old) | set(new)):
            if old.get(name) == new.get(name):
                continue
            # a fixed address dhcpcd might keep: gone before it starts again (the browser must not
            # find the player there and keep settings that don't work)
            for line in old.get(name, []):
                if line.startswith('static ip_address=') and line not in new.get(name, []):
                    _command(['ip', 'addr', 'del', line.split('=', 1)[1], 'dev', name])
            # reads dhcpcd.conf again and starts over on this interface
            _run(['dhcpcd', '-n', name], problems)

    wifi = settings.get('wifi')
    interface = (wireless_interfaces() or [None])[0]
    path = wpa_conf_path(interface or 'wlan0')
    current = _read(path)
    if wifi:
        text = wpa_conf(wifi)
        if current != text:
            _write_etc(path, text, 0o600)
    elif current is not None and current.startswith(WPA_HEADER):
        # Wi-Fi no longer set up (a first setup undone, the settings file deleted with the overlay
        # off): as on the image
        text = DEFAULT_WPA_CONF
        _write_etc(path, text, 0o600)
    else:
        text = current
    if interface and text != current or interface and wifi and at_start:
        if not wifi or not wifi['enabled']:
            _run(['rfkill', 'block', 'wifi'], problems)
        else:
            # Raspberry Pi OS keeps Wi-Fi blocked until a country is set (and /var, where it
            # remembers that, is in RAM)
            _run(['rfkill', 'unblock', 'wifi'], problems)
            _run(['iw', 'reg', 'set', wifi['country'] or '00'], problems)
            ok, output = _command(['wpa_cli', '-i', interface, 'reconfigure'])
            if not ok or 'FAIL' in output:
                # not running for this interface (dhcpcd's hook usually starts it)
                _run(['wpa_supplicant', '-B', '-i', interface, '-c', path, '-D', 'nl80211,wext'], problems)
    for problem in problems:
        print(f"Network settings: {problem}", flush=True)
    return problems


# ----- Changes from the web interface ----- #
# _lock: the change waiting to be kept, held only briefly (the page reads it). _apply_lock: one
# _apply at a time; its commands may take a while, and the page doesn't wait for them
_lock = threading.RLock()
_apply_lock = threading.Lock()
# the change waiting to be kept: settings, previous, deadline (time.monotonic()), applied (being
# put in use), finished (in use), changed (anything other than the saved settings has been used
# since), problems, and the timers
_pending = {}
# the settings in /etc now (None: not known yet, the saved ones)
_in_use = {'settings': None}
# interfaces saved with an address for the next start (Save for next start): {name: the setting
# they use until then, None for DHCP}
_next_start = {}

def _later(seconds, function):
    """Run function after seconds, in another thread. Returns something with cancel()."""
    timer = threading.Timer(seconds, function)
    timer.daemon = True
    timer.start()
    return timer

def _use(settings, at_start=False, now_too=()):
    """Apply settings, but an interface saved for the next start keeps what it uses until then,
    unless these settings change it or it is in now_too (Apply chosen for it). Returns what went
    wrong."""
    with _apply_lock:
        if at_start:
            _next_start.clear()
        now = dict(settings)
        if _next_start:
            saved = read_settings().get('interfaces') or {}
            interfaces = dict(settings.get('interfaces') or {})
            for name, current in _next_start.items():
                if interfaces.get(name) == saved.get(name) and name not in now_too:
                    if current:
                        interfaces[name] = current
                    else:
                        interfaces.pop(name, None)
            now.pop('interfaces', None)
            if interfaces:
                now['interfaces'] = interfaces
        problems = _apply(now, at_start=at_start or _in_use['settings'] is None)
        _in_use['settings'] = now
        return problems

def _forget_next_start():
    """Once a change is kept: interfaces saved for the next start that use their saved setting
    now (an undone change must still find what they used before)."""
    saved = read_settings().get('interfaces') or {}
    using = (_in_use['settings'] or {}).get('interfaces') or {}
    for name in list(_next_start):
        if saved.get(name) == using.get(name):
            del _next_start[name]

def apply_at_start():
    """When the web interface starts: the saved settings into /etc (nothing without them)."""
    return _use(read_settings(), at_start=True)

def target_settings():
    """The settings the page changes: the ones waiting to be kept, else the saved ones."""
    with _lock:
        return _pending['settings'] if _pending else read_settings()

def next_start_interfaces():
    """Interfaces whose saved address isn't used until the next start (Save for next start),
    other than those it is being tried on now."""
    with _lock:
        return set(_next_start) - (_pending['now_too'] if _pending else set())

def change(settings, now_too=()):
    """Use these settings in a moment, and go back to the saved ones unless keep() is called
    within KEEP_SECONDS. now_too: interfaces to use them on now even if saved for the next start."""
    settings = normalize(settings)
    with _lock:
        previous = _pending['previous'] if _pending else read_settings()
        changed = bool(_pending) and _pending['changed']
        now_too = set(now_too) | (_pending['now_too'] if _pending else set())
        _cancel_timers()
        _pending.clear()
        token = object()
        _pending.update(settings=settings, previous=previous, token=token, applied=False, finished=False, now_too=now_too,
                        changed=changed, problems=[], deadline=time.monotonic() + APPLY_DELAY + KEEP_SECONDS)
        _pending['apply_timer'] = _later(APPLY_DELAY, lambda: _apply_pending(token))
        _pending['revert_timer'] = _later(APPLY_DELAY + KEEP_SECONDS, lambda: _revert(token))

def _cancel_timers():
    for name in ('apply_timer', 'revert_timer'):
        if _pending.get(name):
            _pending[name].cancel()

def _apply_pending(token):
    with _lock:
        if _pending.get('token') is not token or _pending['applied']:
            return
        _pending['applied'] = _pending['changed'] = True
        settings, now_too = _pending['settings'], _pending['now_too']
    problems = _use(settings, now_too=now_too)
    with _lock:
        if _pending.get('token') is token:
            _pending['problems'] = problems
            _pending['finished'] = True

def _revert(token):
    with _lock:
        if _pending.get('token') is not token:
            return
        print("Network settings: not kept, so the previous ones are used again.", flush=True)
    undo()

class NotApplied(Exception):
    """Keep was asked for before the change had been applied (so before it could be tried)."""

def keep():
    """Save the settings being tried to /boot. False if none are waiting; NotApplied while they
    are still being applied: only settings the page has been reached with are kept."""
    with _lock:
        if not _pending:
            return False
        if not _pending['finished']:
            raise NotApplied()
        _save(_pending['settings'])
        _cancel_timers()
        _pending.clear()
    with _apply_lock:
        _forget_next_start()
    return True

def undo():
    """Back to the saved settings now. False if no change is waiting."""
    with _lock:
        if not _pending:
            return False
        _cancel_timers()
        changed = _pending['changed']
        previous = _pending['previous']
        _pending.clear()
    if changed:
        _use(previous)
    return True

def save_for_next_start(settings):
    """Save settings to /boot without using them now (for a network the player isn't on yet)."""
    with _lock:
        if _pending:
            raise RuntimeError("Keep or undo the change being tried first.")
    settings = normalize(settings)
    with _apply_lock:
        using = (_in_use['settings'] if _in_use['settings'] is not None else read_settings()).get('interfaces') or {}
        _save(settings)
        interfaces = settings.get('interfaces') or {}
        for name in set(interfaces) | set(using):
            if interfaces.get(name) != using.get(name):
                _next_start[name] = using.get(name)
            else:
                _next_start.pop(name, None)

def pending():
    """{'seconds': left to keep it, 'problems': [...]} while a change waits to be kept, else None."""
    with _lock:
        if not _pending:
            return None
        return {'seconds': max(0, int(_pending['deadline'] - time.monotonic())),
                'problems': list(_pending['problems']), 'applied': _pending['finished']}


# ----- What the page shows ----- #
def countries():
    """[(code, name)] sorted by name, or [] if the list isn't there."""
    result = []
    for line in (_read(ISO3166_FILE) or '').splitlines():
        if line.startswith('#') or '\t' not in line:
            continue
        code, name = line.split('\t', 1)
        if COUNTRY_RE.match(code):
            result.append((code, name.strip()))
    return sorted(result, key=lambda c: c[1])

def _unescape_iw(text):
    """iw writes a network name's spaces at either end, backslashes and other bytes as \\xNN."""
    data = text
    raw = bytearray()
    i = 0
    while i < len(data):
        if data[i:i + 2] == '\\x' and re.fullmatch(r'[0-9a-fA-F]{2}', data[i + 2:i + 4]):
            raw.append(int(data[i + 2:i + 4], 16))
            i += 4
        else:
            raw += data[i].encode()
            i += 1
    return raw.decode('utf-8', 'replace')

def parse_scan(output):
    """[{'ssid', 'signal' (dBm), 'frequency' (MHz), 'secure'}] from `iw dev wlan0 scan`, the
    strongest first, one per network name, hidden networks left out."""
    found = []
    current = None
    for line in output.splitlines():
        if line.startswith('BSS '):
            current = {'ssid': '', 'signal': None, 'frequency': None, 'secure': False}
            found.append(current)
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith('SSID:'):
            current['ssid'] = _unescape_iw(stripped[5:].lstrip())
        elif stripped.startswith('signal:'):
            try:
                current['signal'] = float(stripped.split()[1])
            except (IndexError, ValueError):
                pass
        elif stripped.startswith('freq:'):
            try:
                current['frequency'] = int(float(stripped.split()[1]))
            except (IndexError, ValueError):
                pass
        elif stripped.startswith(('RSN:', 'WPA:')) or (stripped.startswith('capability:') and 'Privacy' in stripped):
            current['secure'] = True
    best = {}
    for network in found:
        if not network['ssid'] or '\0' in network['ssid']:
            continue
        other = best.get(network['ssid'])
        if other is None or (network['signal'] or -999) > (other['signal'] or -999):
            best[network['ssid']] = network
    return sorted(best.values(), key=lambda n: -(n['signal'] if n['signal'] is not None else -999))

def scan():
    """The Wi-Fi networks in range (see parse_scan), or ValueError saying why not. Without a
    country only on the channels every country allows."""
    interface = (wireless_interfaces() or [None])[0]
    if not interface:
        raise ValueError("This Pi has no Wi-Fi.")
    wifi = target_settings().get('wifi')
    if wifi and not wifi['enabled']:
        raise ValueError("Wi-Fi is off. Turn it on to look for networks.")
    # blocked until a country is set on Raspberry Pi OS: unblocked to scan, and blocked again
    # after it if Wi-Fi isn't set up (wpa_supplicant would scan every channel the firmware allows)
    blocked = wifi_blocked()
    if blocked:
        _command(['rfkill', 'unblock', 'wifi'])
    try:
        _command(['ip', 'link', 'set', interface, 'up'])
        country = wifi['country'] if wifi else ''
        frequencies = [] if country else ['freq'] + [str(f) for f in SAFE_FREQUENCIES]
        ok, output = _command(['iw', 'dev', interface, 'scan'] + frequencies)
        if not ok:
            # e.g. busy: wpa_supplicant is scanning; its last results are there
            ok, output = _command(['iw', 'dev', interface, 'scan', 'dump'])
    finally:
        if blocked and not wifi:
            _command(['rfkill', 'block', 'wifi'])
    if not ok:
        raise ValueError(f"Couldn't look for networks: {output}")
    networks = parse_scan(output)
    if not country:
        networks = [n for n in networks if n['frequency'] in SAFE_FREQUENCIES or n['frequency'] is None]
    return networks

def _wifi_link(interface):
    """(network name, signal in dBm) the interface is connected to, or (None, None)."""
    ok, output = _command(['iw', 'dev', interface, 'link'])
    if not ok or not output.startswith('Connected'):
        return None, None
    ssid = signal = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith('SSID:'):
            ssid = _unescape_iw(stripped[5:].lstrip())
        elif stripped.startswith('signal:'):
            try:
                signal = int(float(stripped.split()[1]))
            except (IndexError, ValueError):
                pass
    return ssid, signal

def wifi_blocked():
    """True if Wi-Fi is switched off (rfkill soft or hard block)."""
    try:
        names = os.listdir(RFKILL_PATH)
    except OSError:
        return False
    for name in names:
        folder = os.path.join(RFKILL_PATH, name)
        if (_read(os.path.join(folder, 'type')) or '').strip() == 'wlan':
            if '1' in ((_read(os.path.join(folder, 'soft')) or '').strip(), (_read(os.path.join(folder, 'hard')) or '').strip()):
                return True
    return False

def routers():
    """{interface: router} from the default routes in use."""
    ok, output = _command(['ip', '-4', 'route', 'show', 'default'])
    found = {}
    for line in output.splitlines() if ok else []:
        parts = line.split()
        if 'via' in parts and 'dev' in parts:
            found.setdefault(parts[parts.index('dev') + 1], parts[parts.index('via') + 1])
    return found

def dns_servers():
    return [line.split()[1] for line in (_read(RESOLV_CONF) or '').splitlines()
            if line.startswith('nameserver') and len(line.split()) > 1]

def view():
    """What the Network and Wi-Fi cards show."""
    settings = target_settings()
    addresses = system.interface_addresses()
    in_use = routers()
    present = system.network_interfaces()
    wireless = [name for name in present if os.path.isdir(os.path.join(system.NET_PATH, name, 'wireless'))]
    next_start = next_start_interfaces()
    # set up for an adapter that isn't plugged in now
    names = present + [name for name in sorted(settings.get('interfaces') or {}) if name not in present]
    interfaces = []
    for name in names:
        kind = 'wireless' if name in wireless else 'wired'
        state = system._read_interface_file(name, 'operstate') if name in present else 'absent'
        interfaces.append({
            'name': name, 'kind': kind, 'label': 'Wi-Fi' if kind == 'wireless' else 'Ethernet',
            'state': state or 'unknown', 'connected': state == 'up',
            'addresses': [address for family, address in addresses.get(name, []) if family == 'IPv4'],
            'router': in_use.get(name), 'next_start': name in next_start,
            'setting': (settings.get('interfaces') or {}).get(name) or {'mode': 'dhcp', 'address': '', 'router': '', 'dns': []},
        })
    wifi = settings.get('wifi') or {'enabled': True, 'country': '', 'networks': []}
    interface = wireless[0] if wireless else None
    ssid, signal = _wifi_link(interface) if interface else (None, None)
    return {
        'available': os.path.exists(DHCPCD_CONF),
        'interfaces': interfaces,
        'wired': [i for i in interfaces if i['kind'] == 'wired'],
        'wifi_interface': next((i for i in interfaces if i['name'] == interface), None),
        'wifi': dict(wifi, managed='wifi' in settings, blocked=wifi_blocked() if interface else False,
                     ssid=ssid, signal=signal),
        'addresses': addresses,
        'dns': dns_servers(),
        'countries': countries(),
        'pending': pending(),
    }
