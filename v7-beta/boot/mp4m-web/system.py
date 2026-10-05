"""System helpers for the MP4MUSEUM web interface.

Based on the MP4MUSEUM v7 beta web service by Julius Schmiedel (http://mp4museum.org),
modified 2026 in https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.

Everything that touches the Pi itself: read-only partitions, config.txt,
sound, network name and password storage. Kept separate from the Flask
routes in webservice.py.
"""
import fcntl
import glob
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import signal
import struct
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager

from werkzeug.security import check_password_hash, generate_password_hash


# Directories and files
MEDIA_PATH = '/media/internal'
BOOT_PATH = '/boot'
ALSA_FILE = os.path.join(BOOT_PATH, "alsa.txt")
CONFIG_FILE = os.path.join(BOOT_PATH, "config.txt")
SCRIPT_FILE = os.path.join(BOOT_PATH, "mp4museum.py")
PASSWORD_FILE = os.path.join(BOOT_PATH, "mp4m-password.txt")
HOSTNAME_FILE = os.path.join(BOOT_PATH, "hostname.txt")
# Player settings and status, shared with /boot/mp4museum.py
PLAYER_SETTINGS_FILE = os.path.join(BOOT_PATH, "mp4m-player.txt")
# files switched off in the web interface (one path per line): the player leaves them out
DISABLED_FILE = os.path.join(BOOT_PATH, "mp4m-disabled.txt")
PLAYER_STATUS_FILE = "/tmp/mp4museum-status.json"
# A file chosen in the web interface, read by the player when it gets SIGUSR1
PLAY_REQUEST_FILE = "/tmp/mp4museum-play.json"
# What the player prints (.bashrc sends it here)
PLAYER_LOG_FILE = "/tmp/mp4museum.log"
# Files that were playing when the player stopped by itself, which it skips until they are replaced
PLAYER_SKIPPED_FILE = "/tmp/mp4museum-skipped.json"
DEFAULT_IMAGE_DURATION = 10

DEFAULT_PASSWORD = 'mp4museum'

# Uploads are written to hidden temp files on the media partition, then renamed
UPLOAD_PREFIX = '.upload-'
# Leave some room so the media partition never fills up completely
UPLOAD_RESERVE = 16 * 1024 * 1024
# Temp files untouched for this long are left over from failed uploads
STALE_UPLOAD_SECONDS = 3600

# Video presets. Only these lines are changed in config.txt, everything else is kept.
VIDEO_MODES = {
    'auto': {
        'description': 'Auto (use what the display reports)',
        'settings': []
    },
    '1080p60': {
        'description': '1920x1080 FullHD 60fps',
        'settings': ['hdmi_ignore_cec=1', 'hdmi_group=1', 'hdmi_mode=16']
    },
    '4k60': {
        'description': '4k 60fps (Raspberry 4)',
        'settings': ['hdmi_enable_4kp60=1', 'hdmi_ignore_cec=1', 'hdmi_group=1', 'hdmi_mode=97']
    },
    'ntsc': {
        'description': 'NTSC (composite)',
        'settings': ['enable_tvout=1', 'sdtv_mode=0']
    },
    'pal': {
        'description': 'PAL (composite)',
        'settings': ['enable_tvout=1', 'sdtv_mode=2']
    }
}
VIDEO_KEYS = {s.split('=', 1)[0] for mode in VIDEO_MODES.values() for s in mode['settings']}
VIDEO_BLOCK_START = '# --- mp4museum video mode (set by web interface) ---'
VIDEO_BLOCK_END = '# --- end of mp4museum video mode ---'

HOSTNAME_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
# Characters exFAT does not allow in file names
INVALID_FILENAME_CHARS = set('/\\:*?"<>|')


def read_serial():
    """The Pi's serial number, or '' if it can't be read."""
    try:
        with open(CPUINFO_FILE, 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return ''

def read_mac(interface):
    """The MAC address of a network interface, or '' if it doesn't exist."""
    try:
        with open(os.path.join(NET_PATH, interface, 'address'), 'r') as f:
            return f.read().strip()
    except OSError:
        return ''

def session_secret():
    """Key for signing login cookies.

    It includes the salted password hash, which can't be worked out from the
    network, so login cookies can't be forged once a password has been set.
    (The default password is public anyway.)
    """
    parts = [read_serial(), read_mac('eth0'), read_mac('wlan0'), read_password_hash()]
    try:
        with open('/etc/machine-id', 'r') as f:
            parts.append(f.read().strip())
    except OSError:
        pass
    if not any(parts):
        return os.urandom(32).hex()
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


# ----- Files and partitions ----- #
def is_valid_filename(filename):
    # Plain file names only: no paths, hidden files or characters exFAT can't store
    return (bool(filename)
            and not filename.startswith('.')
            # exFAT drops a trailing dot: the file would lose its extension
            and not filename.endswith('.')
            and not any(c in INVALID_FILENAME_CHARS or ord(c) < 32 or ord(c) == 127 for c in filename)
            # exFAT allows 255 UTF-16 characters
            and len(filename.encode('utf-16-le')) <= 510)

def run_command(cmd, timeout=None):
    """(True, its output) or (False, its errors). timeout: seconds before it is stopped."""
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
        return True, completed.stdout.strip()
    except subprocess.CalledProcessError as err:
        return False, err.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"stopped after {timeout} seconds"
    except OSError as err:
        return False, str(err)

def is_read_only(mount_point):
    """Return True if mount_point is mounted read-only."""
    options = []
    try:
        with open('/proc/mounts', 'r') as f:
            for line in f:
                parts = line.split()
                # The last matching entry is the one in effect
                if len(parts) >= 4 and parts[1] == mount_point:
                    options = parts[3].split(',')
    except OSError:
        pass
    return 'ro' in options

# Lock files keep other processes (sudo mp4m-update) from remounting read-only while this one writes
LOCK_DIR = '/run/lock'

def _lock_mount(mount_point):
    """Take this partition's lock across processes; returns the lock's file descriptor or None."""
    try:
        fd = os.open(os.path.join(LOCK_DIR, 'mp4m' + mount_point.replace('/', '-') + '.lock'),
                     os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd

def _unlock_mount(fd):
    if fd is not None:
        os.close(fd)

def try_busy_lock(shared=False):
    """Held while an update is installed (until the web interface has restarted) or an SD card
    is made, across processes, so one doesn't stop the other half way; shared by the files
    being copied from USB sticks (several at once). Its file descriptor (close it to let go),
    or None if something else holds it."""
    fd = os.open(os.path.join(LOCK_DIR, 'mp4m-busy.lock'), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd

class _MountState:
    def __init__(self):
        self.lock = threading.Lock()
        self.users = 0
        self.lock_fd = None
        # Make the partition read-only again when the last writer is done
        self.restore_read_only = False
        # The last attempt to do that failed, so keep trying on later writes
        self.restore_failed = False

_mount_states = {}
_mount_states_lock = threading.Lock()

@contextmanager
def writable(mount_point):
    """Make a read-only partition writable for the duration of the block.

    The partition is made read-only again afterwards, once no other request
    is still writing to it, so a power cut can't corrupt it.
    """
    with _mount_states_lock:
        state = _mount_states.setdefault(mount_point, _MountState())
    with state.lock:
        if state.users == 0:
            state.lock_fd = _lock_mount(mount_point)
            currently_read_only = is_read_only(mount_point)
            # A partition left writable by a failed remount still has to go back to read-only
            state.restore_read_only = currently_read_only or state.restore_failed
            if currently_read_only:
                status, output = run_command(['mount', '-o', 'remount,rw', mount_point])
                if not status:
                    _unlock_mount(state.lock_fd)
                    raise RuntimeError(f"Failed to make {mount_point} writable: {output}")
        state.users += 1
    try:
        yield
    finally:
        with state.lock:
            state.users -= 1
            if state.users == 0:
                if state.restore_read_only:
                    state.restore_failed = not make_read_only(mount_point)
                _unlock_mount(state.lock_fd)
                state.lock_fd = None

def mounted_read_only_in_fstab(mount_point):
    """True if /etc/fstab mounts this partition read-only."""
    try:
        with open('/etc/fstab', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and not parts[0].startswith('#') and parts[1] == mount_point:
                    return 'ro' in parts[3].split(',')
    except OSError:
        pass
    return False

def restore_read_only_mounts():
    """At startup: make partitions read-only again if a crash or restart left them writable."""
    for mount_point in (BOOT_PATH, MEDIA_PATH):
        if not (mounted_read_only_in_fstab(mount_point) and os.path.ismount(mount_point)):
            continue
        # Not while another process (sudo mp4m-update) is writing to it
        fd = _lock_mount(mount_point)
        try:
            if not is_read_only(mount_point):
                make_read_only(mount_point)
        finally:
            _unlock_mount(fd)

def make_read_only(mount_point, attempts=3):
    """Remount a partition read-only, retrying briefly if it is busy."""
    for attempt in range(attempts):
        # Remounting read-only also writes everything to the SD card
        status, output = run_command(['mount', '-o', 'remount,ro', mount_point])
        if status:
            return True
        if attempt < attempts - 1:
            time.sleep(0.5)
    print(f"Failed to make {mount_point} read-only again: {output}", flush=True)
    return False

def write_file(path, content):
    """Replace a file in one step, so a power cut leaves either the old or the new version."""
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.' + os.path.basename(path) + '-')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise

def read_config_text():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return f.read()
    except Exception:
        return ''

def get_video_settings(config_text):
    """Return the active (uncommented) video settings from config.txt."""
    settings = set()
    for line in config_text.splitlines():
        line = line.strip()
        if line.startswith('#') or '=' not in line:
            continue
        key, value = (part.strip() for part in line.split('=', 1))
        if key in VIDEO_KEYS:
            settings.add(f"{key}={value}")
    return settings

# ----- Graphics memory ----- #
# Memory for the graphics chip (video decoding, the screen), set with gpu_mem in config.txt.
# The v7 image has 128 MB.
GPU_MEM_CHOICES = (128, 256, 512)
MEMINFO_FILE = '/proc/meminfo'

def _applies_to_all(config_text):
    """(line, applies to every Pi) for each line: before any [section], or under [all]."""
    everywhere = True
    for line in config_text.splitlines(True):
        stripped = line.strip()
        if stripped.startswith('['):
            everywhere = stripped.lower() == '[all]'
            yield line, False
        else:
            yield line, everywhere

# gpu_mem_256, gpu_mem_512, gpu_mem_1024: for boards with that much memory (1024: or more);
# they win over gpu_mem
GPU_MEM_OVERRIDE_RE = re.compile(r'\s*gpu_mem_(256|512|1024)\s*=\s*(\d*)\s*$')

def get_gpu_mem(config_text, board=None):
    """The gpu_mem set for every Pi in config.txt (MB), or None. board: the board's memory in
    MB, to take a gpu_mem_256/512/1024 line for it into account."""
    value = override = None
    for line, everywhere in _applies_to_all(config_text):
        match = re.match(r'\s*gpu_mem\s*=\s*(\d+)\s*$', line)
        if everywhere and match:
            value = int(match.group(1))
        match = GPU_MEM_OVERRIDE_RE.match(line)
        if everywhere and match and match.group(2) and board and (
                int(match.group(1)) == board or (match.group(1) == '1024' and board >= 1024)):
            override = int(match.group(2))
    return override if override is not None else value

def gpu_mem_in_model_sections(config_text):
    """Whether a model section (e.g. [pi4]) sets the graphics memory: it may differ from
    get_gpu_mem() on some boards. set_gpu_mem_in_config sets those lines too."""
    return any(not everywhere and (GPU_MEM_OVERRIDE_RE.match(line) or re.match(r'\s*gpu_mem\s*=', line))
               for line, everywhere in _applies_to_all(config_text))

def set_gpu_mem_in_config(config_text, megabytes):
    """config.txt with gpu_mem set to megabytes for every Pi. gpu_mem lines in model sections
    (e.g. [pi4]) get the same value and gpu_mem_256/512/1024 lines are taken out, as they would
    win over it on the boards they are for; nothing else changes."""
    lines, done = [], False
    for line, everywhere in _applies_to_all(config_text):
        if GPU_MEM_OVERRIDE_RE.match(line):
            continue
        if re.match(r'\s*gpu_mem\s*=', line):
            if everywhere and done:
                continue
            line = f"gpu_mem={megabytes}\n"
            done = done or everywhere
        lines.append(line)
    if not done:
        # after the comments at the top, before any [section]
        at = next((i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith('#')), len(lines))
        lines.insert(at, f"gpu_mem={megabytes}\n")
    return ''.join(lines)

def memory_megabytes():
    """The memory Linux has (without what the graphics chip has), in MB, or None."""
    try:
        with open(MEMINFO_FILE, 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None

def board_memory_megabytes():
    """All the memory on the board in MB, or None. From the revision code; on old boards
    without one, Linux's memory plus the graphics memory, rounded up to a board size."""
    memory = installed_memory_megabytes()
    if memory:
        return memory
    linux = memory_megabytes()
    if not linux:
        return None
    estimate = linux + (get_gpu_mem(read_config_text() or '') or 64)
    memory = 256
    while memory < estimate:
        memory *= 2
    return memory

def gpu_mem_choices(board=None):
    """The graphics memory choices safe on this board: the Raspberry Pi documentation says at
    most 128 MB on a 256 MB board and 384 MB on a 512 MB board, or Linux may not start."""
    board = board or board_memory_megabytes()
    if board and board <= 256:
        return GPU_MEM_CHOICES[:1]
    if board and board <= 512:
        return GPU_MEM_CHOICES[:2]
    return GPU_MEM_CHOICES

def recommended_gpu_mem(board=None):
    """512 MB with 2 GB or more (a Pi 4), 256 MB with 1 GB (a Pi 3), else 128 MB."""
    board = board or board_memory_megabytes()
    if not board:
        return 256
    return 512 if board >= 2048 else 256 if board >= 1024 else 128

def get_current_video_mode(config_text):
    """Return (mode key, description) for the video mode set in config.txt."""
    settings = get_video_settings(config_text)
    for key, mode in VIDEO_MODES.items():
        if settings == set(mode['settings']):
            return key, mode['description']
    return None, 'Custom: ' + ', '.join(sorted(settings))

def set_video_mode_in_config(config_text, mode):
    """Replace the video settings in config.txt with the preset, keeping all other lines."""
    lines = []
    after_start_marker = False
    for line in config_text.splitlines():
        stripped = line.strip()
        # Remove the previous preset block line by line, so nothing else is lost
        # if the block was edited by hand
        if stripped in (VIDEO_BLOCK_START, VIDEO_BLOCK_END):
            after_start_marker = stripped == VIDEO_BLOCK_START
            continue
        if after_start_marker and stripped == '[all]':
            after_start_marker = False
            continue
        after_start_marker = False
        # Drop video settings from anywhere in the file, they would conflict with the preset
        if not stripped.startswith('#') and '=' in stripped and stripped.split('=', 1)[0].strip() in VIDEO_KEYS:
            continue
        lines.append(line)

    settings = VIDEO_MODES[mode]['settings']
    if settings:
        while lines and not lines[-1].strip():
            lines.pop()
        # [all] makes sure the settings apply to every Pi model, not just the last [section] in the file
        lines += ['', VIDEO_BLOCK_START, '[all]'] + settings + [VIDEO_BLOCK_END]
    return '\n'.join(lines) + '\n'

NET_PATH = '/sys/class/net'

def network_interfaces():
    """Network interfaces other than loopback, whatever they are called (eth0, enx..., wlan0)."""
    try:
        return sorted(name for name in os.listdir(NET_PATH) if name != 'lo')
    except OSError:
        return []

def _read_interface_file(interface, name):
    try:
        with open(os.path.join(NET_PATH, interface, name), 'r') as f:
            return f.read().strip()
    except OSError:
        return ''

def interface_addresses(timeout=None):
    """{interface: [('IPv4' or 'IPv6', '192.168.1.120/24'), ...]}"""
    status, output = run_command(['ip', '-o', 'addr', 'show'], timeout=timeout)
    return parse_interface_addresses(output) if status else {}

def parse_interface_addresses(output):
    """interface_addresses() from what 'ip -o addr show' printed."""
    addresses = {}
    for line in output.splitlines():
        # e.g. "2: enxb827eb4e4fd4    inet 192.168.1.120/24 brd 192.168.1.255 scope global ..."
        parts = line.split()
        if len(parts) >= 4 and parts[2] in ('inet', 'inet6'):
            interface = parts[1].split('@')[0]
            addresses.setdefault(interface, []).append(('IPv4' if parts[2] == 'inet' else 'IPv6', parts[3]))
    return addresses

# ----- Device ----- #
CPUINFO_FILE = '/proc/cpuinfo'
OS_RELEASE_FILE = '/etc/os-release'
UPTIME_FILE = '/proc/uptime'

def _read_text(path):
    try:
        with open(path, 'r', errors='replace') as f:
            return f.read()
    except OSError:
        return ''

def read_model():
    """e.g. 'Raspberry Pi 3 Model B Rev 1.2', or ''."""
    return _read_text(MODEL_FILE).strip('\0 \n')

def installed_memory_megabytes():
    """All the memory on the board in MB (Linux gets it less the graphics memory), or None.
    From the revision code: on newer boards bits 20-22 give the size, 256 MB << n."""
    match = re.search(r'^Revision\s*:\s*([0-9a-fA-F]+)\s*$', _read_text(CPUINFO_FILE), re.M)
    if not match:
        return None
    revision = int(match.group(1), 16)
    if not revision & (1 << 23):
        return None
    return 256 << ((revision >> 20) & 7)

def _vcgencmd(*args):
    """The value of `vcgencmd ...`, e.g. '256M' from 'gpu=256M', or None."""
    ok, output = run_command(['vcgencmd'] + list(args))
    if not ok or '=' not in output:
        return None
    return output.split('=', 1)[1].strip()

def format_megabytes(megabytes):
    return f"{megabytes // 1024} GB" if megabytes >= 1024 and megabytes % 1024 == 0 else f"{megabytes} MB"

def format_duration(seconds):
    minutes = int(seconds) // 60
    days, hours, minutes = minutes // 1440, minutes // 60 % 24, minutes % 60
    if days:
        return f"{days} d {hours} h"
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"

def get_device_info():
    """(label, value) pairs about this Pi for the System tab, leaving out what can't be read."""
    info = []
    model = read_model()
    if model:
        info.append(("Model", model))
    memory = installed_memory_megabytes()
    linux_memory = memory_megabytes()
    if memory:
        info.append(("Memory", format_megabytes(memory)))
    elif linux_memory:
        info.append(("Memory", f"{linux_memory} MB for programs"))
    gpu = _vcgencmd('get_mem', 'gpu')
    if gpu and gpu.endswith('M') and gpu[:-1].isdigit():
        info.append(("Graphics memory", f"{gpu[:-1]} MB"))
    throttled = _vcgencmd('get_throttled')
    try:
        throttled = int(throttled, 16) if throttled else None
    except ValueError:
        throttled = None
    temperature = _vcgencmd('measure_temp')
    if temperature:
        temperature = temperature.replace("'C", " °C")
        # bit 0: power too low now; 2: slowed down now; 3: temperature limit reached now. Slowed
        # down without low power: too hot
        if throttled is not None and throttled & 0xc and not throttled & 0x1:
            temperature += " (too hot: the Pi is slowing itself down)"
        info.append(("Temperature", temperature))
    if throttled is not None:
        if throttled & 0x1:
            info.append(("Power", "Too low now: use a stronger power supply"))
        elif throttled & 0x10000:
            info.append(("Power", "Was too low since the Pi started: use a stronger power supply"))
        else:
            info.append(("Power", "OK"))
    if media_available():
        try:
            usage = shutil.disk_usage(MEDIA_PATH)
            info.append(("Media partition", f"{format_size(usage.free)} free of {format_size(usage.total)}"))
        except OSError:
            pass
    match = re.search(r'^PRETTY_NAME="?([^"\n]*)"?$', _read_text(OS_RELEASE_FILE), re.M)
    if match:
        info.append(("Operating system", match.group(1)))
    info.append(("Linux kernel", platform.release()))
    try:
        info.append(("Running for", format_duration(float(_read_text(UPTIME_FILE).split()[0]))))
    except (IndexError, ValueError):
        pass
    serial = read_serial()
    if serial:
        info.append(("Serial number", serial))
    return info

def get_display_info():
    try:
        result = subprocess.run(['tvservice', '-s'], capture_output=True, text=True)
        return result.stdout
    except:
        return "Error getting display information"

# ----- Player ----- #
def read_player_settings():
    """key=value lines from mp4m-player.txt."""
    settings = {}
    try:
        with open(PLAYER_SETTINGS_FILE, 'r') as f:
            for line in f:
                key, sep, value = line.partition('=')
                if sep and key.strip():
                    settings[key.strip()] = value.strip()
    except OSError:
        pass
    return settings

def get_image_duration():
    value = read_player_settings().get('image_duration', '')
    return max(1, int(value)) if value.isdecimal() else DEFAULT_IMAGE_DURATION

# one save at a time, so two saved together don't each write the file without the other's setting
_player_settings_lock = threading.Lock()

def save_player_setting(key, value):
    """Set one key in mp4m-player.txt, keeping the others. The player reads it before every file."""
    with _player_settings_lock:
        settings = read_player_settings()
        settings[key] = str(value)
        with writable(BOOT_PATH):
            write_file(PLAYER_SETTINGS_FILE, ''.join(f"{key}={value}\n" for key, value in settings.items()))

def save_image_duration(seconds):
    save_player_setting('image_duration', seconds)

# how often the boot video plays at start-up: once by default (twice in the original, as a
# warm-up the Pi 3 B+ didn't need)
BOOT_VIDEO_PLAYS = (1, 2, 0)

def get_boot_video_plays():
    value = read_player_settings().get('boot_video_plays', '')
    if value.isdecimal() and int(value) in BOOT_VIDEO_PLAYS:
        return int(value)
    # not set: the player script's own default (one edited here and kept by updates may be from
    # when it was twice)
    match = re.search(r"'boot_video_plays':\s*(\d)", read_script_file())
    return int(match.group(1)) if match and int(match.group(1)) in BOOT_VIDEO_PLAYS else 1

def get_show_address():
    """Whether the player shows its name and IP address on the logo screen (default yes)."""
    return read_player_settings().get('show_address', '') != 'no'

def player_has_start_up_settings():
    """Whether the player script reads boot_video_plays and show_address: one edited here before
    they existed is kept by updates, and doesn't."""
    script = read_script_file()
    return 'boot_video_plays' in script and 'show_address' in script

LOOP_PLAYERS = ('vlc', 'omxplayer')

def get_loop_player():
    """What plays loop videos: 'omxplayer' (default, if installed) or 'vlc'."""
    value = read_player_settings().get('loop_player', '')
    return value if value in LOOP_PLAYERS else 'omxplayer'

def omxplayer_installed():
    return bool(shutil.which('omxplayer'))

def _is_player_process(pid):
    """True if pid is the running player script (and not some other process that got its number)."""
    try:
        with open(f'/proc/{int(pid)}/cmdline', 'rb') as f:
            arguments = f.read().split(b'\0')
    except (OSError, ValueError, TypeError):
        return False
    return any(argument.endswith(b'mp4museum.py') for argument in arguments)

def get_player_status():
    """{'state': 'playing'|'paused'|'idle'|'sync', 'file', 'since', 'pid'}, or None if the player isn't running."""
    try:
        with open(PLAYER_STATUS_FILE, 'r') as f:
            status = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict) or not _is_player_process(status.get('pid')):
        return None
    return status

# the files the player plays (the same list is in mp4museum.py)
VIDEO_TYPES = ('.mp4', '.m4v', '.mov', '.mkv', '.avi', '.ts', '.mts', '.m2ts', '.h264', '.mpg', '.mpeg',
               '.m2v', '.vob', '.webm', '.wmv', '.flv', '.ogv', '.3gp')
IMAGE_TYPES = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff')
AUDIO_TYPES = ('.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.aif', '.aiff')

def media_kind(name):
    """'video', 'image', 'audio' or 'other', from the extension."""
    name = name.lower()
    for kind, types in (('video', VIDEO_TYPES), ('image', IMAGE_TYPES), ('audio', AUDIO_TYPES)):
        if name.endswith(types):
            return kind
    return 'other'

# A Pi 3 or older can't show images more than this many pixels wide or high: they come out
# scrambled (seen with 3300 x 2550), so its player skips them. Same limit and model check as in
# mp4museum.py; not known for a Pi 4 or 5.
LARGE_IMAGE_SIDE = 2048
MODEL_FILE = '/proc/device-tree/model'

def image_limit():
    """LARGE_IMAGE_SIDE on a Pi 3 or older, else None."""
    model = read_model()
    if any(newer in model for newer in ('Pi 4', 'Pi 5', 'Pi 400', 'Pi 500', 'Compute Module 4', 'Compute Module 5')):
        return None
    return LARGE_IMAGE_SIDE if 'Raspberry Pi' in model else None

def image_size(path):
    """(width, height) of a PNG, JPEG, GIF, BMP or WebP image from its header, or None."""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if head[:8] == b'\x89PNG\r\n\x1a\n' and head[12:16] == b'IHDR':
                return struct.unpack('>II', head[16:24])
            if head[:6] in (b'GIF87a', b'GIF89a'):
                return struct.unpack('<HH', head[6:10])
            if head[:2] == b'BM' and len(head) >= 26:
                width, height = struct.unpack('<ii', head[18:26])
                return abs(width), abs(height)
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                if head[12:16] == b'VP8X':
                    return int.from_bytes(head[24:27], 'little') + 1, int.from_bytes(head[27:30], 'little') + 1
                if head[12:16] == b'VP8 ':
                    width, height = struct.unpack('<HH', head[26:30])
                    return width & 0x3fff, height & 0x3fff
                if head[12:16] == b'VP8L':
                    bits = int.from_bytes(head[21:25], 'little')
                    return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
            if head[:2] == b'\xff\xd8':
                # JPEG: the size is in the start-of-frame segment
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xff:
                        return None
                    if marker[1] == 0xff:
                        # fill byte before a marker
                        f.seek(-1, 1)
                        continue
                    if marker[1] in (0xd8, 0x01) or 0xd0 <= marker[1] <= 0xd7:
                        continue
                    length = struct.unpack('>H', f.read(2))[0]
                    if 0xc0 <= marker[1] <= 0xcf and marker[1] not in (0xc4, 0xc8, 0xcc):
                        height, width = struct.unpack('>xHH', f.read(5))
                        return width, height
                    f.seek(length - 2, 1)
    except (OSError, struct.error):
        pass
    return None

def is_large_image(size, limit=None):
    """True if (width, height) is more than this Pi can show (so its player skips it)."""
    limit = limit or image_limit()
    return bool(size and limit) and max(size) > limit

def read_player_log(lines=40):
    """The end of what the player printed, or '' if there is nothing."""
    try:
        with open(PLAYER_LOG_FILE, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            text = f.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    return ''.join(text.splitlines(True)[-lines:])

# The player skips a file that was playing when it stopped by itself this many times
SKIP_AFTER = 2

def get_skipped_files():
    """{path: [size, mtime]} of the files the player skips because it stopped while playing them."""
    try:
        with open(PLAYER_SKIPPED_FILE, 'r') as f:
            return {path: version for path, version, count in json.load(f) if int(count) >= SKIP_AFTER}
    except (OSError, ValueError, TypeError):
        return {}

_disabled_lock = threading.Lock()

def get_disabled_files():
    """The paths of the files switched off in the web interface."""
    try:
        with open(DISABLED_FILE, 'r') as f:
            return {line.rstrip('\n') for line in f if line.strip()}
    except OSError:
        return set()

def player_reads_disabled_files():
    """Whether the player script leaves out switched-off files (one edited here before that
    existed is kept by updates, and doesn't)."""
    return 'mp4m-disabled.txt' in read_script_file()

def update_disabled_files(add=(), remove=()):
    """Switch files off (add) or on again (remove), keeping the others."""
    with _disabled_lock:
        paths = get_disabled_files()
        changed = (paths - set(remove)) | set(add)
        if changed != paths:
            with writable(BOOT_PATH):
                write_file(DISABLED_FILE, ''.join(path + '\n' for path in sorted(changed)))

# seconds a file the player is showing may take to be let go of before it is deleted
LET_GO_SECONDS = 10

def processes_using(path):
    """The processes (pids) that have this file open."""
    path = os.path.realpath(path)
    pids = set()
    try:
        processes = os.listdir('/proc')
    except OSError:
        return pids
    for pid in processes:
        if not pid.isdigit():
            continue
        fd_dir = os.path.join('/proc', pid, 'fd')
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            # gone, or not ours to look at
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == path:
                    pids.add(int(pid))
                    break
            except OSError:
                pass
    return pids

def let_go_of(path, timeout=None):
    """Before a media file is deleted: the player moves off it if it is showing it, switched off
    meanwhile so it doesn't come back to it (an only file would start again). Deleting a file VLC
    or omxplayer still has open leaves it on the partition until they close it, and then the
    exFAT driver frees it, with the partition read-only again or not. Returns (True if nothing
    has it open any more, True if it was switched off here: switch it on again)."""
    status = get_player_status() or {}
    switched_off = False
    if status.get('file') == path and status.get('state') in ('playing', 'paused'):
        if player_reads_disabled_files() and path not in get_disabled_files():
            update_disabled_files(add=[path])
            switched_off = True
        signal_player(signal.SIGUSR1)
    deadline = time.monotonic() + (LET_GO_SECONDS if timeout is None else timeout)
    while processes_using(path):
        if time.monotonic() > deadline:
            return False, switched_off
        time.sleep(0.1)
    return True, switched_off

def player_plays_media_files_only():
    """Whether the player script leaves out files that aren't media (one edited here before
    that is kept by updates, and plays every file with an extension)."""
    return 'MEDIA_TYPES' in read_script_file()

def get_playlist():
    """Every file the player plays, in its order (media files in /media/*/: the media partition
    and USB sticks), and the media partition's other files, which it doesn't play. (Other files
    on USB sticks aren't listed: an SD card in a reader has a Pi's boot files.)"""
    skipped = get_skipped_files()
    disabled = get_disabled_files()
    media_only = player_plays_media_files_only()
    limit = image_limit()
    media_root = os.path.dirname(MEDIA_PATH)
    paths = set(glob.glob(os.path.join(media_root, '*', '*.*')))
    try:
        paths.update(os.path.join(MEDIA_PATH, name) for name in os.listdir(MEDIA_PATH) if not name.startswith('.'))
    except OSError:
        pass
    entries = []
    for path in sorted(paths):
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        kind = media_kind(name)
        internal = os.path.dirname(path) == MEDIA_PATH
        plays = kind != 'other' if media_only else '.' in name
        if not plays and not internal:
            continue
        try:
            info = os.stat(path)
            size, version = info.st_size, [info.st_size, int(info.st_mtime)]
        except OSError:
            size, version = 0, None
        pixels = image_size(path) if kind == 'image' else None
        entries.append({'path': path, 'name': name, 'folder': os.path.basename(os.path.dirname(path)),
                        'internal': internal, 'kind': kind,
                        'plays': plays, 'loop': 'loop.' in path, 'size': size,
                        'pixels': pixels, 'large': is_large_image(pixels, limit),
                        # the player compares the same way: a replaced file is played again
                        'skipped': path in skipped and skipped[path] == version,
                        'disabled': path in disabled})
    return entries

_play_request_lock = threading.Lock()

def request_play(path):
    """Ask the player to play this file now, then carry on from there. False if it isn't running."""
    return send_player_request({'file': path})

def request_command(command):
    """Ask the player to do 'rewind' (back to the first frame, held until play) or 'previous'
    (the file before). False if it isn't running."""
    return send_player_request({'command': command})

def send_player_request(request_data):
    """Write a request for the player (it reads it when it gets SIGUSR1), then send the signal."""
    with _play_request_lock:
        # A new file with a random name: /tmp is shared with other users, so never a name chosen in advance
        fd, request = tempfile.mkstemp(dir=os.path.dirname(PLAY_REQUEST_FILE), prefix='.mp4museum-play-')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(dict(request_data, id=uuid.uuid4().hex), f)
                # The player runs as pi, the web interface as root
                os.fchmod(f.fileno(), 0o644)
            os.replace(request, PLAY_REQUEST_FILE)
        except BaseException:
            if os.path.exists(request):
                os.remove(request)
            raise
        return signal_player(signal.SIGUSR1)

def signal_player(signum):
    """Send the player a signal (SIGUSR1: next, SIGUSR2: pause/resume). False if it isn't running."""
    status = get_player_status()
    if not status:
        return False
    try:
        os.kill(int(status['pid']), signum)
        return True
    except OSError:
        return False

def parse_sound_cards(aplay_output):
    """[{'number': '0', 'name': 'bcm2835 Headphones', 'devices': [...]}] from the output of aplay -l."""
    cards = []
    for line in aplay_output.splitlines():
        match = re.match(r'card (\d+): (\S+) \[(.*?)\], device (\d+): (.*?)(?: \[.*\])?$', line.strip())
        if not match:
            continue
        number, short_name, name, device, device_name = match.groups()
        if not cards or cards[-1]['number'] != number:
            cards.append({'number': number, 'name': name or short_name, 'devices': []})
        cards[-1]['devices'].append(f"device {device}: {device_name}")
    return cards

def get_current_sound_card():
    """Get the current sound card configuration."""
    try:
        with open(ALSA_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return "auto"

def read_script_file():
    """Read the Python script file."""
    try:
        with open(SCRIPT_FILE, 'r') as f:
            return f.read()
    except FileNotFoundError:
        return "# MP4Museum Python Script\n# This file will be created when you save your first edit.\n"
    except Exception as e:
        return f"Error reading file: {str(e)}"

# Held while the player script is saved, and while an update decides whether to replace it
player_lock = threading.Lock()

def write_script_file(content):
    """Write content to the Python script file."""
    try:
        with writable(BOOT_PATH), player_lock:
            # Browsers send Windows line endings from text areas
            write_file(SCRIPT_FILE, content.replace('\r\n', '\n'))
        return True, "Script saved successfully"
    except Exception as e:
        return False, str(e)

def media_available():
    return os.path.ismount(MEDIA_PATH)

def get_free_space():
    try:
        return max(0, shutil.disk_usage(MEDIA_PATH).free - UPLOAD_RESERVE)
    except OSError:
        return 0

class NotEnoughSpace(Exception):
    pass

class _Upload:
    def __init__(self, size, bytes_written):
        self.size = size
        self.bytes_written = bytes_written

    def still_needed(self):
        # What it has written already is no longer in the free space
        return max(0, self.size - self.bytes_written())

_upload_space_lock = threading.Lock()
_active_uploads = []

@contextmanager
def upload_space(size, bytes_written=lambda: 0):
    """Reserve room for an upload while it runs.

    Uploads can run at the same time, and each one must not count space that
    another one is about to fill. bytes_written tells how much of this upload
    is already on the partition.
    """
    upload = _Upload(size, bytes_written)
    with _upload_space_lock:
        reserved = sum(other.still_needed() for other in _active_uploads)
        if size > get_free_space() - reserved:
            raise NotEnoughSpace()
        _active_uploads.append(upload)
    try:
        yield
    finally:
        with _upload_space_lock:
            _active_uploads.remove(upload)

def format_size(size):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f"{size:.0f} {unit}" if unit == 'bytes' else f"{size:.1f} {unit}"
        size /= 1024

def remove_stale_uploads():
    """Delete temp files left behind by uploads that were interrupted."""
    now = time.time()
    for name in os.listdir(MEDIA_PATH):
        path = os.path.join(MEDIA_PATH, name)
        try:
            if name.startswith(UPLOAD_PREFIX) and now - os.path.getmtime(path) > STALE_UPLOAD_SECONDS:
                os.remove(path)
        except OSError:
            pass

# Held while a file is renamed on the media partition, or a finished upload or copy renamed
# into place
media_rename_lock = threading.Lock()

# Files being copied from USB sticks (lower-case names: exFAT doesn't tell case apart), and how
# the copies that have finished went, for the next page shown
_copies = {}
_copy_results = []
_copies_lock = threading.Lock()

def _media_has(name):
    try:
        return name.lower() in (other.lower() for other in os.listdir(MEDIA_PATH))
    except OSError:
        return False

class Busy(Exception):
    pass

def start_copy(source):
    """Copy a file (from a USB stick) to the media partition in the background. FileExistsError
    if it has a file of that name, or one is being copied; Busy while a card is made or an
    update installed."""
    name = os.path.basename(source)
    busy = try_busy_lock(shared=True)
    if busy is None:
        raise Busy()
    with _copies_lock:
        if name.lower() in _copies or _media_has(name):
            os.close(busy)
            raise FileExistsError(name)
        _copies[name.lower()] = source
    try:
        threading.Thread(target=_copy, args=(source, busy), daemon=True).start()
    except BaseException:
        with _copies_lock:
            del _copies[name.lower()]
        os.close(busy)
        raise

def _copy(source, busy):
    name = os.path.basename(source)
    try:
        copy_to_media(source)
        result = ('success', f"'{name}' copied to this player. While the stick is in, its copy plays "
                             "too: switch that one off, or take the stick out.")
    except FileExistsError:
        result = ('error', f"This player already has a file called '{name}'. Rename one of them first.")
    except NotEnoughSpace:
        result = ('error', f"Not enough space to copy '{name}'.")
    except Exception as e:
        result = ('error', f"Couldn't copy '{name}': {e}")
    with _copies_lock:
        _copy_results.append(result)
        del _copies[name.lower()]
    os.close(busy)

def copies_running():
    """The files being copied (their paths on the stick)."""
    with _copies_lock:
        return sorted(_copies.values())

def take_copy_results():
    """[(category, message)] for the copies that have finished since the last call."""
    with _copies_lock:
        results = list(_copy_results)
        del _copy_results[:]
    return results

def copy_to_media(source):
    """Copy a file to the media partition, under the same name. It's written under a temporary
    name (which the player doesn't play) and renamed when it's all there. FileExistsError if
    the media partition has a file of that name, NotEnoughSpace."""
    name = os.path.basename(source)
    target = os.path.join(MEDIA_PATH, name)
    size = os.path.getsize(source)
    with writable(MEDIA_PATH):
        remove_stale_uploads()
        if _media_has(name):
            raise FileExistsError(name)
        fd, temp = tempfile.mkstemp(dir=MEDIA_PATH, prefix=UPLOAD_PREFIX)
        try:
            with os.fdopen(fd, 'wb') as dst:
                # (not through a link: a stick formatted ext4 could point anywhere)
                src_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                with upload_space(size, lambda: os.path.getsize(temp)), os.fdopen(src_fd, 'rb') as src:
                    shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
            # an upload of the same name may have arrived meanwhile: that one stays (checked and
            # renamed in one go: an upload is renamed into place under the same lock)
            with media_rename_lock:
                if _media_has(name):
                    raise FileExistsError(name)
                os.replace(temp, target)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise
    return target

# ----- Network name ----- #
def default_hostname():
    """mp4museum-xxxx, where xxxx comes from the Pi's serial number (or MAC address).

    The serial is hashed so the name doesn't reveal it.
    """
    unique_id = read_serial() or next((mac for mac in map(read_mac, network_interfaces()) if mac), '')
    if not unique_id:
        return "mp4museum"
    return "mp4museum-" + hashlib.sha256(unique_id.encode()).hexdigest()[:4]

def configured_hostname():
    """The network name from /boot/hostname.txt, or the default one."""
    try:
        with open(HOSTNAME_FILE, 'r') as f:
            name = f.read().strip().lower()
        if HOSTNAME_RE.match(name):
            return name
    except OSError:
        pass
    return default_hostname()

def apply_hostname(name):
    """Set the hostname and announce it on the network as <name>.local."""
    if socket.gethostname() == name:
        return True, ""
    status, output = run_command(['hostname', name])
    if not status:
        return False, output
    # The root filesystem is a RAM overlay, so these are reapplied on every boot
    try:
        with open('/etc/hostname', 'w') as f:
            f.write(name + '\n')
        with open('/etc/hosts', 'r') as f:
            hosts = [line for line in f.read().splitlines() if not line.startswith('127.0.1.1')]
        hosts.append(f"127.0.1.1\t{name}")
        with open('/etc/hosts', 'w') as f:
            f.write('\n'.join(hosts) + '\n')
    except OSError as e:
        print(f"Failed to update /etc/hostname or /etc/hosts: {e}", flush=True)
    # avahi reads the hostname when it starts
    return run_command(['systemctl', 'restart', 'avahi-daemon'])

# ----- Password ----- #
def read_password_hash():
    try:
        with open(PASSWORD_FILE, 'r') as f:
            return f.read().strip()
    except OSError:
        return ''

def check_password(password):
    stored = read_password_hash()
    if stored:
        return check_password_hash(stored, password)
    return hmac.compare_digest(password.encode(), DEFAULT_PASSWORD.encode())

def password_fingerprint():
    # Stored in the session, so changing the password logs out every other browser
    return hashlib.sha256(read_password_hash().encode()).hexdigest()[:16]

def save_password(password):
    with writable(BOOT_PATH):
        write_file(PASSWORD_FILE, generate_password_hash(password, method='pbkdf2:sha256:50000'))
