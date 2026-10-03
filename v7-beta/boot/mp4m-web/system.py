"""System helpers for the MP4MUSEUM web interface.

Based on the MP4MUSEUM v7 beta web service by Julius Schmiedel (http://mp4museum.org),
modified 2026 in https://github.com/lotech/mp4museum. Licensed under the GNU GPL v3, see LICENSE.

Everything that touches the Pi itself: read-only partitions, config.txt,
sound, network name and password storage. Kept separate from the Flask
routes in webservice.py.
"""
import fcntl
import hashlib
import hmac
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
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
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return ''

def read_mac(interface):
    """The MAC address of a network interface, or '' if it doesn't exist."""
    try:
        with open(f'/sys/class/net/{interface}/address', 'r') as f:
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
            and not any(c in INVALID_FILENAME_CHARS or ord(c) < 32 or ord(c) == 127 for c in filename)
            # exFAT allows 255 UTF-16 characters
            and len(filename.encode('utf-16-le')) <= 510)

def run_command(cmd):
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True, completed.stdout.strip()
    except subprocess.CalledProcessError as err:
        return False, err.stderr.strip()
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

def get_mac_address():
    mac_info = []
    for interface, label in (('eth0', 'Ethernet'), ('wlan0', 'Wireless')):
        mac_info.append(f"{label} ({interface}): {read_mac(interface) or 'Not available'}")
    return "\n".join(mac_info)

def get_network_status():
    try:
        # Get network configuration
        result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True)
        network_lines = result.stdout.splitlines()
        filtered_lines = []
        found_eth = False

        for line in network_lines:
            # Look for the first line containing ": eth"
            if not found_eth and ": eth" in line:
                found_eth = True

            if found_eth:
                filtered_lines.append(line)

        # Combine MAC addresses and network info
        combined_info = get_mac_address() + "\n\nNetwork Configuration:\n" + "\n".join(filtered_lines)
        return combined_info
    except:
        return "Error getting network information"

def get_display_info():
    try:
        result = subprocess.run(['tvservice', '-s'], capture_output=True, text=True)
        return result.stdout
    except:
        return "Error getting display information"

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

# ----- Network name ----- #
def default_hostname():
    """mp4museum-xxxx, where xxxx comes from the Pi's serial number (or MAC address).

    The serial is hashed so the name doesn't reveal it.
    """
    unique_id = read_serial() or read_mac('eth0') or read_mac('wlan0')
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
