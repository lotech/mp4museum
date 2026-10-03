#!/usr/bin/env python3
# mp4museum web interface
# based on the MP4MUSEUM v7 beta web service by julius schmiedel - http://mp4museum.org
# licensed under the GNU GPL v3, see LICENSE
#
# modified 2026 in https://github.com/lotech/mp4museum (see git history):
# read-only partitions restored after writes, login, per-player network name,
# video presets that keep config.txt, uploads written to the SD card
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
import uuid
from contextlib import contextmanager
from datetime import timedelta

from flask import Flask, Request, request, redirect, url_for, flash, send_from_directory, render_template_string, session
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

PROJECT_URL = 'https://mp4museum.org'
UPSTREAM_REPO_URL = 'https://github.com/JuliusCode/MP4MUSEUM'
REPO_URL = 'https://github.com/lotech/mp4museum'

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


def get_hardware_key():
    """Generate a secret key based on hardware information."""
    hardware_info = []

    # Try to get CPU information
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):  # Raspberry Pi serial number
                    hardware_info.append(line)
                elif line.startswith('processor'):  # CPU info
                    hardware_info.append(line)
    except:
        pass

    # Try to get MAC address
    try:
        hardware_info.append(hex(uuid.getnode()))
    except:
        pass

    # Try to get machine-id
    try:
        with open('/etc/machine-id', 'r') as f:
            hardware_info.append(f.read().strip())
    except:
        pass

    # If we couldn't get any hardware info, use a random fallback
    if not hardware_info:
        return hashlib.sha256(os.urandom(32)).hexdigest()

    # Create a consistent hash from the hardware information
    hardware_string = ''.join(hardware_info)
    return hashlib.sha256(hardware_string.encode()).hexdigest()


class MediaRequest(Request):
    """Write uploads straight to the media partition.

    Flask normally buffers uploads in /tmp, which is in RAM on the read-only
    overlay root and runs out of space for video files.
    """
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.endpoint != 'upload_file':
            return super()._get_file_stream(total_content_length, content_type, filename, content_length)
        stream = tempfile.NamedTemporaryFile('wb+', dir=MEDIA_PATH, prefix=UPLOAD_PREFIX, delete=False)
        # Remembered so they can be cleaned up even if the upload breaks off half way
        self.upload_temp_files.append(stream)
        return stream

    @property
    def upload_temp_files(self):
        if not hasattr(self, '_upload_temp_files'):
            self._upload_temp_files = []
        return self._upload_temp_files


app = Flask(__name__)
app.request_class = MediaRequest
app.secret_key = get_hardware_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # Other web pages can't make the browser send the login cookie along with their requests
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# ----- Utility functions ----- #
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

class _MountState:
    def __init__(self):
        self.lock = threading.Lock()
        self.users = 0
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
            currently_read_only = is_read_only(mount_point)
            # A partition left writable by a failed remount still has to go back to read-only
            state.restore_read_only = currently_read_only or state.restore_failed
            if currently_read_only:
                status, output = run_command(['mount', '-o', 'remount,rw', mount_point])
                if not status:
                    raise RuntimeError(f"Failed to make {mount_point} writable: {output}")
        state.users += 1
    try:
        yield
    finally:
        with state.lock:
            state.users -= 1
            if state.users == 0 and state.restore_read_only:
                state.restore_failed = not make_read_only(mount_point)

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
    # Try ethernet
    try:
        with open('/sys/class/net/eth0/address', 'r') as f:
            mac_info.append(f"Ethernet (eth0): {f.read().strip()}")
    except:
        mac_info.append("Ethernet (eth0): Not available")

    # Try wifi
    try:
        with open('/sys/class/net/wlan0/address', 'r') as f:
            mac_info.append(f"Wireless (wlan0): {f.read().strip()}")
    except:
        mac_info.append("Wireless (wlan0): Not available")

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

def write_script_file(content):
    """Write content to the Python script file."""
    try:
        with writable(BOOT_PATH):
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

def uploaded_bytes(req):
    """Returns a function telling how much of a request's upload is written so far."""
    def bytes_written():
        total = 0
        for stream in list(req.upload_temp_files):
            try:
                total += os.path.getsize(stream.name)
            except OSError:
                # Already moved into place, so no longer an upload in progress
                pass
        return total
    return bytes_written

def discard_upload_temp_files():
    """Delete the request's upload temp files that weren't moved into place."""
    for stream in request.upload_temp_files:
        try:
            stream.close()
            if os.path.exists(stream.name):
                os.remove(stream.name)
        except OSError:
            pass

# ----- Network name ----- #
def default_hostname():
    """mp4museum-xxxx, where xxxx comes from the Pi's serial number (or MAC address).

    The serial is hashed so the name doesn't reveal it.
    """
    unique_id = ''
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    unique_id = line.split(':', 1)[1].strip()
    except OSError:
        pass
    for interface in ('eth0', 'wlan0'):
        if unique_id:
            break
        try:
            with open(f'/sys/class/net/{interface}/address', 'r') as f:
                unique_id = f.read().strip()
        except OSError:
            pass
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

# ----- Login ----- #
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

@app.before_request
def require_login():
    if request.endpoint == 'login':
        return None
    if session.get('logged_in') and session.get('password') == password_fingerprint():
        return None
    if request.headers.get('X-Requested-With') == 'fetch':
        # A redirect would look like success to the page's JavaScript
        return "You have been logged out. Please reload the page and log in again.", 401
    return redirect(url_for('login'))

@app.context_processor
def inject_globals():
    return dict(project_url=PROJECT_URL, upstream_repo_url=UPSTREAM_REPO_URL, repo_url=REPO_URL,
                hostname=socket.gethostname())

BASE_CSS = '''
          :root {
            --bg-primary: #121212;
            --bg-secondary: #1e1e1e;
            --text-primary: #ffffff;
            --text-secondary: #a0a0a0;
            --accent-color: #808080;
            --border-color: #333333;
            --error-bg: #2a2a2a;
            --error-border: #ff3333;
            --error-text: #ff3333;
            --success-bg: #2a2a2a;
            --success-border: #404040;
            --hover-color: #2a2a2a;
            --active-color: #404040;
            --info-bg: #2a2a2a;
            --info-border: #404040;
            --info-text: #a0a0a0;
          }
          
          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
          }
          
          .container {
            max-width: 1200px;
            width: 95%;
            margin: 0 auto;
            background: var(--bg-secondary);
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.5);
            padding: 20px;
            box-sizing: border-box;
          }
          
          @media (max-width: 768px) {
            .container {
              width: 100%;
              border-radius: 0;
            }
          }
          
          h1, h2, h3 {
            color: var(--text-primary);
            margin-top: 0;
          }
          
          .tabs {
            display: flex;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 20px;
          }
          
          .tab {
            padding: 12px 24px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 16px;
            color: var(--text-secondary);
            opacity: 0.8;
            transition: all 0.3s ease;
          }
          
          .tab:hover {
            opacity: 1;
            background-color: var(--hover-color);
          }
          
          .tab.active {
            opacity: 1;
            color: var(--text-primary);
            border-bottom: 2px solid var(--accent-color);
            background-color: var(--active-color);
          }
          
          .tab-content {
            display: none;
          }
          
          .tab-content.active {
            display: block;
          }
          
          .flash {
            padding: 12px;
            margin-bottom: 20px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
          }
          
          .error {
            background-color: var(--error-bg);
            border: 1px solid var(--error-border);
            color: var(--error-text);
          }
          
          .success {
            background-color: var(--success-bg);
            border-color: var(--success-border);
          }
          
          .button {
            background-color: var(--accent-color);
            color: var(--text-primary);
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s ease;
            font-weight: 500;
          }
          
          .button:hover {
            background-color: var(--hover-color);
            transform: translateY(-1px);
          }
          
          pre {
            background-color: var(--bg-primary);
            padding: 16px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            overflow-x: auto;
            width: 100%;
            box-sizing: border-box;
          }
          
          .form-group {
            margin-bottom: 24px;
          }
          
          .form-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: var(--text-secondary);
          }
          
          .form-group input[type="text"],
          .form-group select {
            padding: 12px;
            background-color: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            font-size: 14px;
            color: var(--text-primary);
          }
          
          .form-group select {
            width: 300px;
            cursor: pointer;
            appearance: none;
            background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23a0a0a0' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
            background-repeat: no-repeat;
            background-position: right 12px center;
            background-size: 16px;
          }
          
          .file-list {
            list-style: none;
            padding: 0;
          }
          
          .file-list li {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px;
            border-bottom: 1px solid var(--border-color);
          }
          
          .file-list li:last-child {
            border-bottom: none;
          }
          
          .file-actions {
            display: flex;
            gap: 12px;
          }
          
          .file-actions a {
            color: var(--accent-color);
            text-decoration: none;
            font-size: 14px;
          }
          
          .file-actions a:hover {
            text-decoration: underline;
          }
          
          .upload-form {
            display: flex;
            align-items: flex-end;
            gap: 12px;
          }
          
          .sound-form {
            display: flex;
            align-items: flex-end;
            gap: 12px;
          }
          
          .video-form {
            display: flex;
            align-items: flex-end;
            gap: 12px;
          }
          
          .info-box {
            background-color: var(--info-bg);
            border: 1px solid var(--info-border);
            color: var(--info-text);
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 16px;
          }
          
          .reboot-screen {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: var(--bg-primary);
            display: none;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: var(--text-primary);
            z-index: 1000;
          }
          
          .reboot-screen.active {
            display: flex;
          }
          
          .reboot-message {
            text-align: center;
            color: var(--text-primary);
          }
          
          .reboot-message h2 {
            margin-bottom: 20px;
          }
          
          .reboot-message p {
            color: var(--text-secondary);
            margin: 10px 0;
          }

          .file-actions form {
            display: inline;
            margin: 0;
          }

          .link-button {
            background: none;
            border: none;
            padding: 0;
            color: var(--accent-color);
            font-size: 14px;
            font-family: inherit;
            cursor: pointer;
          }

          .link-button:hover {
            text-decoration: underline;
          }

          .form-group input[type="password"] {
            padding: 12px;
            background-color: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            font-size: 14px;
            color: var(--text-primary);
          }

          .stacked-form input[type="text"],
          .stacked-form input[type="password"] {
            display: block;
            width: 300px;
            max-width: 100%;
            box-sizing: border-box;
            margin-bottom: 12px;
          }

          .login {
            max-width: 420px;
            margin-top: 10vh;
          }

          .login input[type="password"] {
            display: block;
            width: 100%;
            box-sizing: border-box;
            margin-bottom: 12px;
          }

          .hint {
            color: var(--text-secondary);
            font-size: 14px;
          }

          .footer {
            max-width: 1200px;
            width: 95%;
            margin: 20px auto 0;
            text-align: center;
            font-size: 13px;
            color: var(--text-secondary);
          }

          .footer a, .hint a {
            color: var(--text-secondary);
          }
'''

FOOTER = '''
      <footer class="footer">
        Based on <a href="{{ project_url }}" target="_blank" rel="noopener">MP4MUSEUM</a> by Julius Schmiedel
        (<a href="{{ upstream_repo_url }}" target="_blank" rel="noopener">original source</a>).
        This version: <a href="{{ repo_url }}" target="_blank" rel="noopener">{{ repo_url.split('//')[1] }}</a>
      </footer>
'''

# ----- Routes ----- #

# Home page: List files and show management options
@app.route('/')
def index():
    files = []
    is_available = media_available()
    try:
        # Get all files and filter out hidden files and directories
        all_files = os.listdir(MEDIA_PATH)
        files = sorted(f for f in all_files if not f.startswith('.') and os.path.isfile(os.path.join(MEDIA_PATH, f)))
    except Exception as e:
        flash(f'Error reading directory: {str(e)}', 'error')

    # Get sound devices from aplay -l
    sound_status, sound_out = run_command(["aplay", "-l"])
    sound_devices = sound_out.splitlines() if sound_status else []

    # Read current video mode from config.txt
    current_mode_key, current_mode = get_current_video_mode(read_config_text())

    # Read the Python script
    script_content = read_script_file()

    free_space = get_free_space() if is_available else 0

    # Template with all functionality
    template = '''
    <!doctype html>
    <html>
      <head>
        <title>MP4Museum</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
''' + BASE_CSS + '''
        </style>
        <script>
          function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(content => {
              content.classList.remove('active');
            });
            document.querySelectorAll('.tab').forEach(tab => {
              tab.classList.remove('active');
            });
            document.getElementById(tabId).classList.add('active');
            
            // Find and activate the corresponding tab button
            const tabButton = document.querySelector(`.tab[onclick="showTab('${tabId}')"]`);
            if (tabButton) {
              tabButton.classList.add('active');
            }
            
            // Save the active tab to localStorage
            localStorage.setItem('activeTab', tabId);
          }
          
          // Function to restore the last active tab
          function restoreActiveTab() {
            const lastTab = localStorage.getItem('activeTab') || 'media';
            showTab(lastTab);
          }

          // Call restoreActiveTab when the page loads
          document.addEventListener('DOMContentLoaded', restoreActiveTab);

          function checkUploadSize(form) {
            const file = form.querySelector('input[type="file"]').files[0];
            const freeSpace = {{ free_space }};
            if (file && file.size > freeSpace) {
              alert('Not enough free space: the file is ' + Math.ceil(file.size / 1048576) + ' MB but only {{ free_space_text }} is free.');
              return false;
            }
            return true;
          }

          function handleReboot() {
            if(confirm('Are you sure you want to reboot the system?')) {
              // Show reboot screen
              document.getElementById('rebootScreen').classList.add('active');
              
              // Start checking for server availability
              checkServerAndRedirect();
              
              // Trigger the reboot
              fetch('{{ url_for('reboot_system') }}', {method: 'POST', headers: {'X-Requested-With': 'fetch'}})
                  .then(response => {
                      if (response.status === 401) {
                          alert('You have been logged out. Please log in again.');
                          window.location = '/';
                      }
                      // Server check is already running
                  })
                  .catch(() => {
                      // Even if the fetch fails (which is expected during reboot),
                      // server check will continue
                  });
            }
          }

          function checkServerAndRedirect() {
            // Add error handling for undefined window.location.origin
            const serverUrl = window.location.origin || window.location.protocol + '//' + window.location.host;
            
            // Initial delay of 10 seconds before first check
            setTimeout(() => {
                tryReconnect(serverUrl);
            }, 10000);
          }

          function tryReconnect(serverUrl) {
            fetch(serverUrl, {
                // Add timeout to fetch request
                signal: AbortSignal.timeout(5000)
            })
            .then(response => {
                if (response.ok) {
                    window.location.href = '/';
                } else {
                    setTimeout(() => tryReconnect(serverUrl), 2000);
                }
            })
            .catch(() => {
                setTimeout(() => tryReconnect(serverUrl), 2000);
            });
          }

          function validateSoundDevice() {
            const deviceInput = document.getElementById('device');
            const errorDiv = document.getElementById('deviceError');
            const value = deviceInput.value.trim();
            
            // Allow 'auto' value
            if (value === 'auto') {
                errorDiv.style.display = 'none';
                return true;
            }
            
            // Check if it's a valid integer
            const number = parseInt(value);
            if (isNaN(number)) {
                errorDiv.textContent = 'Please enter a valid number';
                errorDiv.style.display = 'block';
                return false;
            }
            
            // Check if it's within the valid range (the player reads a single digit)
            if (!/^[0-9]$/.test(value)) {
                errorDiv.textContent = 'Please enter a number between 0 and 9';
                errorDiv.style.display = 'block';
                return false;
            }
            
            errorDiv.style.display = 'none';
            return true;
          }

          function loadScript() {
            if(confirm('Discard unsaved changes and reload from disk?')) {
              window.location.reload();
            }
          }

          function saveAndReboot() {
            if(confirm('Save the script and reboot the system?')) {
              // Get the script content from the textarea
              const scriptContent = document.getElementById('script_content').value;
              
              // Save the script via AJAX first
              fetch('{{ url_for('save_script') }}', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/x-www-form-urlencoded',
                  'X-Requested-With': 'fetch',
                },
                body: 'script_content=' + encodeURIComponent(scriptContent)
              })
              .then(response => {
                if (response.ok) {
                  // Script saved successfully, now trigger reboot
                  handleReboot();
                } else {
                  response.text().then(message => alert('Failed to save script: ' + message));
                }
              })
              .catch(error => {
                alert('Error saving script: ' + error);
              });
            }
          }
        </script>
      </head>
      <body>
        <div class="container">
          <div style="display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 12px;">
            <h1>MP4Museum <span class="hint">{{ hostname }}.local</span></h1>
            <form method="post" action="{{ url_for('logout') }}">
              <button type="submit" class="link-button" title="Log out of the web interface">Log out</button>
            </form>
          </div>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, msg in messages %}
              <div class="flash {{ category }}">{{ msg }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}
          
          <div class="tabs">
            <button class="tab active" onclick="showTab('media')" title="Manage media files, upload, download, and delete content">Media</button>
            <button class="tab" onclick="showTab('sound')" title="Configure audio output device and sound settings">Sound</button>
            <button class="tab" onclick="showTab('video')" title="Set display resolution and video output mode">Video</button>
            <button class="tab" onclick="showTab('script')" title="Edit the Python script for custom functionality">Script</button>
            <button class="tab" onclick="showTab('system')" title="Network name, password and network information">System</button>
          </div>
          
          <div id="media" class="tab-content active">
            <h2>Files in {{ media_path }}</h2>
            
            {% if is_available %}
            <div class="form-group">
              <h3>Upload File</h3>
              <form method="post" action="{{ url_for('upload_file') }}" enctype="multipart/form-data" class="upload-form" onsubmit="return checkUploadSize(this)">
                <input type="file" name="file" required title="Select a file from your computer to upload (supported formats: MP4, MP3, WAV, etc.)">
                <input type="submit" value="Upload" class="button" title="Upload the selected file to the media directory">
              </form>
              <p class="hint">{{ free_space_text }} free. Files play in alphabetical order.</p>
            </div>
            {% else %}
            <div class="form-group">
              <h3>Upload File</h3>
              <div class="info-box" title="The media partition is missing">
                The media partition {{ media_path }} is not mounted, so files can't be uploaded.
              </div>
            </div>
            {% endif %}

            <ul class="file-list">
              {% for file in files %}
                <li>
                  <span>{{ file }}</span>
                  <div class="file-actions">
                    <a href="{{ url_for('download_file', filename=file) }}" title="Download this file to your computer">Download</a>
                    <form method="post" action="{{ url_for('delete_file') }}" onsubmit="return confirm('Are you sure you want to delete ' + this.filename.value + '?')">
                      <input type="hidden" name="filename" value="{{ file }}">
                      <button type="submit" class="link-button" title="Permanently delete this file">Delete</button>
                    </form>
                  </div>
                </li>
              {% endfor %}
            </ul>

            <div class="form-group">
              <button class="button" onclick="handleReboot()" title="Restart the system to apply all changes">Reboot System</button>
            </div>
          </div>
          
          <div id="sound" class="tab-content">
            <h2>Sound Device</h2>
            
            <div class="form-group">
              <h3>Select Sound Device</h3>
              <form method="post" action="{{ url_for('set_sound_device') }}" class="sound-form" onsubmit="return validateSoundDevice()">
                <div>
                  <label for="device" title="Enter the card number from the list above">Card Number:</label>
                  <input type="text" name="device" id="device" required title="Enter the number shown in 'card X' from the list above" 
                         value="{{ current_sound_card }}" 
                         onfocus="if(this.value === 'auto') this.value = '';"
                         onblur="if(this.value === '') this.value = 'auto';">
                  <div id="deviceError" style="color: var(--error-text); font-size: 12px; margin-top: 4px; display: none;"></div>
                </div>
                <input type="submit" value="Set Card" class="button" title="Set this card as the default audio output">
                {% if current_sound_card != "auto" %}
                <button type="submit" class="button" formaction="{{ url_for('set_auto_sound') }}" formnovalidate onclick="document.getElementById('device').value = 'auto'" title="Enable automatic sound device selection">Auto Mode</button>
                {% endif %}
              </form>
            </div>

            <div class="form-group">
              <h3>Available Sound Devices</h3>
              <pre title="List of all sound devices connected to the system. Use the card number from this list to set the audio output.">{{ sound_devices_text }}</pre>
            </div>
          </div>
          
          <div id="video" class="tab-content">
            <h2>Video Mode</h2>
            
            <div class="form-group">
              <h3>Display Information</h3>
              <pre title="Current display configuration and status information">{{ display_info }}</pre>
            </div>

            <div class="form-group">
              <h3>Current Video Mode: <span title="The currently active display resolution and refresh rate">{{ current_mode }}</span></h3>
            </div>
            
            <div class="form-group">
              <h3>Select Video Mode</h3>
              <form method="post" action="{{ url_for('set_video_mode') }}" class="video-form">
                <div>
                  <label for="mode" title="Choose the display resolution and refresh rate">Video Mode:</label>
                  <select name="mode" required title="Select the desired video output mode. Changes require a system reboot to take effect.">
                    {% if not current_mode_key %}
                    <option value="" selected disabled>Custom (current settings)</option>
                    {% endif %}
                    {% for key, mode in video_modes.items() %}
                    <option value="{{ key }}" {% if key == current_mode_key %}selected{% endif %}>{{ mode.description }}</option>
                    {% endfor %}
                  </select>
                </div>
                <input type="submit" value="Apply Video Mode" class="button" title="Apply the selected video mode (requires system reboot)">
              </form>
              <p class="hint">Only the video lines in /boot/config.txt are changed, your other settings are kept.
                NTSC and PAL use the composite (3.5mm AV) output, which is only active when no HDMI display is connected.</p>
            </div>
          </div>

          <div id="script" class="tab-content">
            <h2>Python Script Editor</h2>
            
            <div class="form-group">
              <h3>Edit MP4Museum Script</h3>
              <form method="post" action="{{ url_for('save_script') }}" class="script-form">
                <div style="display: flex; justify-content: center;">
                  <label for="script_content" title="Edit the Python script that runs on the MP4Museum system" style="display: none;">Script Content:</label>
                  <textarea name="script_content" id="script_content" rows="20" cols="80" 
                            title="Edit the Python script. This file is located at /boot/mp4museum.py"
                            style="width: 96%; max-width: 1200px; min-width: 300px; margin: 0 auto; font-family: 'Courier New', monospace; font-size: 14px; padding: 12px; background-color: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-primary); resize: vertical; display: block;">{{ script_content }}</textarea>
                </div>
                <div style="margin-top: 12px;">
                  <input type="submit" value="Save Script" class="button" title="Save the script to /boot/mp4museum.py">
                  <button type="button" class="button" onclick="loadScript()" title="Discard unsaved changes and reload from disk">Discard Changes</button>
                  <button type="button" class="button" onclick="saveAndReboot()" title="Save the script and reboot the system">Save and Reboot</button>
                </div>
              </form>
            </div>
            
            <div class="form-group">
              <h3>Script Information</h3>
              <div class="info-box" style="width: 100%;">
                <strong>File:</strong> /boot/mp4museum.py<br>
                <strong>What it does:</strong> This is your custom Python script for adding extra features to your MP4Museum system.<br>
                <strong>Important:</strong> Changes are saved only when you click "Save Script" or "Save and Reboot". You need to reboot the player for the changes to take affect.
              </div>
            </div>
          </div>

          <div id="system" class="tab-content">
            <h2>System</h2>

            <div class="form-group">
              <h3>Network Name</h3>
              <p class="hint">This player is at <strong>http://{{ hostname }}.local</strong>.
                Give each player on the same network a different name.
                {% if hostname_is_default %}This is the default name, made from the Pi's serial number.{% endif %}</p>
              <form method="post" action="{{ url_for('set_hostname') }}" class="stacked-form"
                    onsubmit="return confirm('Change the network name? The page will move to the new address.')">
                <label for="hostname">New name (letters, numbers and -). Leave empty to go back to the default.</label>
                <input type="text" name="hostname" id="hostname" value="{{ hostname }}" maxlength="63"
                       pattern="[A-Za-z0-9]([A-Za-z0-9\\-]{0,61}[A-Za-z0-9])?" title="Letters, numbers and hyphens, not starting or ending with a hyphen">
                <input type="submit" value="Change Name" class="button">
              </form>
            </div>

            <div class="form-group">
              <h3>Password</h3>
              {% if using_default_password %}
              <p class="hint">The password is still the default (mp4museum).</p>
              {% endif %}
              <form method="post" action="{{ url_for('set_password') }}" class="stacked-form">
                <label for="current_password">Current password</label>
                <input type="password" name="current_password" id="current_password" required>
                <label for="new_password">New password</label>
                <input type="password" name="new_password" id="new_password" required>
                <label for="confirm_password">New password again</label>
                <input type="password" name="confirm_password" id="confirm_password" required>
                <input type="submit" value="Change Password" class="button">
              </form>
              <p class="hint">Forgot it? Delete mp4m-password.txt from the SD card's boot partition to reset it to the default.</p>
            </div>

            <div class="form-group">
              <h3>Network Configuration</h3>
              <pre title="Network configuration and MAC addresses">{{ network_status }}</pre>
            </div>
          </div>
        </div>
''' + FOOTER + '''

        <div id="rebootScreen" class="reboot-screen">
          <div class="reboot-message">
            <h2>System is rebooting...</h2>
            <p>Please wait while the system restarts.</p>
            <p>You will be redirected automatically.</p>
          </div>
        </div>
      </body>
      </html>
      '''
    return render_template_string(template, files=files, media_path=MEDIA_PATH,
                                is_available=is_available,
                                free_space=free_space,
                                free_space_text=format_size(free_space),
                                sound_devices_text='\n'.join(sound_devices),
                                current_mode=current_mode,
                                current_mode_key=current_mode_key,
                                video_modes=VIDEO_MODES,
                                network_status=get_network_status(),
                                display_info=get_display_info(),
                                current_sound_card=get_current_sound_card(),
                                script_content=script_content,
                                hostname_is_default=not os.path.exists(HOSTNAME_FILE),
                                using_default_password=not read_password_hash())

# --- Login --- #
LOGIN_TEMPLATE = '''
    <!doctype html>
    <html>
      <head>
        <title>MP4Museum - Log in</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>''' + BASE_CSS + '''</style>
      </head>
      <body>
        <div class="container login">
          <h1>MP4Museum</h1>
          <p class="hint">{{ hostname }}.local</p>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% for category, msg in messages %}
            <div class="flash {{ category }}">{{ msg }}</div>
          {% endfor %}
        {% endwith %}
          <form method="post" action="{{ url_for('login') }}" class="form-group">
            <label for="password">Password</label>
            <input type="password" name="password" id="password" autofocus required>
            <input type="submit" value="Log in" class="button">
          </form>
          <p class="hint">
            The default password is <code>mp4museum</code>.
            Forgot it? Delete <code>mp4m-password.txt</code> from the SD card's boot partition to reset it.
          </p>
        </div>
''' + FOOTER + '''
      </body>
    </html>
'''

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if check_password(request.form.get('password', '')):
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['password'] = password_fingerprint()
            return redirect(url_for('index'))
        flash("Wrong password.", "error")
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/set_password', methods=['POST'])
def set_password():
    current = request.form.get('current_password', '')
    new = request.form.get('new_password', '')
    if not check_password(current):
        flash("Current password is wrong.", "error")
    elif not new:
        flash("The new password can't be empty.", "error")
    elif new != request.form.get('confirm_password', ''):
        flash("The new passwords don't match.", "error")
    else:
        try:
            with writable(BOOT_PATH):
                write_file(PASSWORD_FILE, generate_password_hash(new, method='pbkdf2:sha256:50000'))
            session['password'] = password_fingerprint()
            flash("Password changed.", "success")
        except Exception as e:
            flash(f"Failed to change password: {e}", "error")
    return redirect(url_for('index'))

# --- File Management Endpoints --- #
@app.route('/upload', methods=['POST'])
def upload_file():
    if not media_available():
        flash(f"The media partition {MEDIA_PATH} is not mounted.", "error")
        return redirect(url_for('index'))
    # Browsers always send the size; without it the space check can't work
    if not request.content_length:
        flash("Upload refused: the browser didn't say how big the file is.", "error")
        return redirect(url_for('index'))
    try:
        with writable(MEDIA_PATH):
            # Before the space check, so leftovers from a power cut can't block new uploads
            remove_stale_uploads()
            # Checked before receiving the file, it is written straight to the media partition
            with upload_space(request.content_length, uploaded_bytes(request._get_current_object())):
                try:
                    file = request.files.get('file')
                    if not file or not file.filename:
                        flash("No file selected.", "error")
                    elif not is_valid_filename(file.filename):
                        flash("Invalid filename. Names can't start with a dot or contain / \\ : * ? \" < > |", "error")
                    else:
                        file.stream.flush()
                        os.replace(file.stream.name, os.path.join(MEDIA_PATH, file.filename))
                        flash(f"File '{file.filename}' uploaded successfully.", "success")
                finally:
                    # Close the temp files before the partition goes back to read-only
                    discard_upload_temp_files()
    except NotEnoughSpace:
        flash("Not enough free space for this file.", "error")
    except Exception as e:
        flash(f"File upload failed: {e}", "error")
    return redirect(url_for('index'))

@app.route('/download/<filename>')
def download_file(filename):
    if not is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    return send_from_directory(MEDIA_PATH, filename, as_attachment=True)

@app.route('/delete', methods=['POST'])
def delete_file():
    filename = request.form.get('filename', '')
    if not is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    file_path = os.path.join(MEDIA_PATH, filename)
    if os.path.isfile(file_path):
        try:
            with writable(MEDIA_PATH):
                os.remove(file_path)
            flash(f"File '{filename}' deleted successfully.", "success")
        except Exception as e:
            flash(f"Failed to delete file: {e}", "error")
    else:
        flash("File not found.", "error")
    return redirect(url_for('index'))

@app.route('/reboot', methods=['POST'])
def reboot_system():
    # Attempt to reboot the system (requires appropriate privileges)
    status, output = run_command(["reboot"])
    if status:
        flash("System is rebooting...", "success")
    else:
        flash("Failed to reboot: " + output, "error")
    # Note: In many cases the command may trigger immediate reboot,
    # so the flash message might not be seen.
    return redirect(url_for('index'))

# --- Sound Device Management Endpoints --- #
@app.route('/set_sound_device', methods=['POST'])
def set_sound_device():
    device = request.form.get('device', '').strip()
    if device == 'auto':
        return set_auto_sound()
    # The player reads a single digit from alsa.txt
    if not re.fullmatch(r'[0-9]', device):
        flash("Please enter a card number from 0 to 9.", "error")
        return redirect(url_for('index'))
    try:
        # Write the device number to alsa.txt in /boot/
        with writable(BOOT_PATH):
            write_file(ALSA_FILE, device)
        flash(f"Sound device set to {device}.", "success")
    except Exception as e:
        flash("Error setting sound device: " + str(e), "error")
        return redirect(url_for('index'))
    # After setting device, redirect to a confirm reboot page
    return redirect(url_for('confirm_reboot'))
@app.route('/confirm_reboot')
def confirm_reboot():
    template = '''
    <!doctype html>
    <html>
    <head>
      <title>Confirm Reboot</title>
      <style>
        :root {
          --bg-primary: #121212;
          --bg-secondary: #1e1e1e;
          --text-primary: #ffffff;
          --text-secondary: #a0a0a0;
          --accent-color: #808080;
          --border-color: #333333;
        }
        
        body { 
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          margin: 0;
          padding: 20px;
          background-color: var(--bg-primary);
          color: var(--text-primary);
          display: flex;
          justify-content: center;
          align-items: center;
          min-height: 100vh;
        }
        
        .container {
          background: var(--bg-secondary);
          padding: 30px;
          border-radius: 12px;
          box-shadow: 0 4px 6px rgba(0, 0, 0, 0.5);
          text-align: center;
        }
        
        .button {
          background-color: var(--accent-color);
          color: var(--text-primary);
          border: none;
          padding: 12px 24px;
          border-radius: 8px;
          cursor: pointer;
          font-size: 14px;
          margin: 10px;
          font-weight: 500;
        }
        
        .button:hover {
          background-color: #2a2a2a;
          transform: translateY(-1px);
        }

        .reboot-screen {
          display: none;
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: var(--bg-primary);
          z-index: 1000;
          justify-content: center;
          align-items: center;
        }

        .reboot-screen.active {
          display: flex;
        }

        .reboot-message {
          text-align: center;
          color: var(--text-primary);
        }

        .reboot-message h2 {
          margin-bottom: 20px;
        }

        .reboot-message p {
          color: var(--text-secondary);
          margin: 10px 0;
        }
      </style>
      <script>
        function confirmReboot() {
            if(confirm('Reboot for the changes to take effect?')) {
                // Show reboot screen
                document.getElementById('rebootScreen').classList.add('active');
                
                // Start checking for server availability
                checkServerAndRedirect();
                
                // Trigger the reboot
                fetch('{{ url_for('reboot_system') }}', {method: 'POST', headers: {'X-Requested-With': 'fetch'}})
                    .then(response => {
                        if (response.status === 401) {
                            alert('You have been logged out. Please log in again.');
                            window.location = '/';
                        }
                        // Server check is already running
                    })
                    .catch(() => {
                        // Even if the fetch fails (which is expected during reboot),
                        // server check will continue
                    });
            } else {
                window.location = "{{ url_for('index') }}";
            }
        }

        function checkServerAndRedirect() {
            // Add error handling for undefined window.location.origin
            const serverUrl = window.location.origin || window.location.protocol + '//' + window.location.host;
            
            // Initial delay of 10 seconds before first check
            setTimeout(() => {
                tryReconnect(serverUrl);
            }, 10000);
        }

        function tryReconnect(serverUrl) {
            fetch(serverUrl, {
                // Add timeout to fetch request
                signal: AbortSignal.timeout(5000)
            })
            .then(response => {
                if (response.ok) {
                    window.location.href = '/';
                } else {
                    setTimeout(() => tryReconnect(serverUrl), 2000);
                }
            })
            .catch(() => {
                setTimeout(() => tryReconnect(serverUrl), 2000);
            });
        }
      </script>
    </head>
    <body onload="confirmReboot()">
      <div class="container">
        <h2>Reboot Required</h2>
        <p>Please confirm to reboot the system for changes to take effect.</p>
      </div>

      <div id="rebootScreen" class="reboot-screen">
        <div class="reboot-message">
          <h2>System is rebooting...</h2>
          <p>Please wait while the system restarts.</p>
          <p>You will be redirected automatically.</p>
        </div>
      </div>
    </body>
    </html>
    '''
    return render_template_string(template)

@app.route('/set_auto_sound', methods=['POST'])
def set_auto_sound():
    try:
        if os.path.exists(ALSA_FILE):
            with writable(BOOT_PATH):
                os.remove(ALSA_FILE)
            flash("Auto sound detection enabled.", "success")
        else:
            flash("Auto sound detection was already enabled.", "info")
    except Exception as e:
        flash("Error setting auto sound: " + str(e), "error")
    return redirect(url_for('index'))

@app.route('/set_wifi', methods=['POST'])
def set_wifi():
    """Configure WiFi settings and reboot"""
    flash('WiFi configuration is not available.', 'error')
    return redirect(url_for('index'))

# --- Video Mode Management Endpoints --- #
@app.route('/set_video_mode', methods=['POST'])
def set_video_mode():
    mode = request.form.get('mode')
    if not mode:
        flash("No video mode provided.", "error")
        return redirect(url_for('index'))

    if mode not in VIDEO_MODES:
        flash("Invalid video mode selected.", "error")
        return redirect(url_for('index'))

    try:
        config_text = read_config_text()
        if not config_text:
            flash(f"Could not read {CONFIG_FILE}.", "error")
            return redirect(url_for('index'))
        with writable(BOOT_PATH):
            write_file(CONFIG_FILE, set_video_mode_in_config(config_text, mode))

        flash(f"Video mode set to {VIDEO_MODES[mode]['description']}.", "success")
        return redirect(url_for('confirm_reboot'))
    except Exception as e:
        flash(f"Error setting video mode: {str(e)}", "error")
        return redirect(url_for('index'))

@app.route('/save_script', methods=['POST'])
def save_script():
    script_content = request.form.get('script_content', '')
    success, message = write_script_file(script_content)
    if request.headers.get('X-Requested-With') == 'fetch':
        # "Save and Reboot" only reboots when saving worked
        return message, 200 if success else 500
    flash(message, 'success' if success else 'error')
    return redirect(url_for('index'))

# --- Network name --- #
@app.route('/set_hostname', methods=['POST'])
def set_hostname():
    name = request.form.get('hostname', '').strip().lower()
    try:
        if name:
            if not HOSTNAME_RE.match(name):
                flash("Network names can only use letters, numbers and hyphens (-), up to 63 characters, "
                      "and can't start or end with a hyphen.", "error")
                return redirect(url_for('index'))
            with writable(BOOT_PATH):
                write_file(HOSTNAME_FILE, name + '\n')
        else:
            # Empty name: go back to the default
            if os.path.exists(HOSTNAME_FILE):
                with writable(BOOT_PATH):
                    os.remove(HOSTNAME_FILE)
            name = default_hostname()
    except Exception as e:
        flash(f"Failed to save network name: {e}", "error")
        return redirect(url_for('index'))

    status, output = apply_hostname(name)
    if not status:
        flash(f"Network name saved, but applying it failed: {output}. It will be used after a reboot.", "error")
        return redirect(url_for('index'))

    if request.host.split(':')[0].endswith('.local'):
        # The old address no longer works, so send the browser to the new one
        return render_template_string(HOSTNAME_CHANGED_TEMPLATE, new_url=f"http://{name}.local/")
    flash(f"Network name changed to {name}.local", "success")
    return redirect(url_for('index'))

HOSTNAME_CHANGED_TEMPLATE = '''
    <!doctype html>
    <html>
      <head>
        <title>MP4Museum - Network name changed</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>''' + BASE_CSS + '''</style>
      </head>
      <body>
        <div class="container login">
          <h2>Network name changed</h2>
          <p>This player is now at <a href="{{ new_url }}">{{ new_url }}</a></p>
          <p class="hint">You will need to log in again there. It can take a few seconds before the new name works.</p>
        </div>
''' + FOOTER + '''
      </body>
    </html>
'''

if __name__ == '__main__':
    # Every player gets its own network name, so several can share a network
    name = configured_hostname()
    status, output = apply_hostname(name)
    if status:
        print(f"Network name: {name}.local", flush=True)
    else:
        print(f"Failed to set network name {name}: {output}", flush=True)
    # Run on all available IPs on port 80
    app.run(host='0.0.0.0', port=80, debug=False)
