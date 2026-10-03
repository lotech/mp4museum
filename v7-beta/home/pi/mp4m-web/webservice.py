#!/usr/bin/env python3
# mp4museum web interface
# based on the MP4MUSEUM v7 beta web service by julius schmiedel - http://mp4museum.org
# licensed under the GNU GPL v3, see LICENSE
#
# modified 2026 in https://github.com/lotech/mp4museum (see git history):
# split into modules and templates, login, per-player network name, read-only
# partitions restored after writes, video presets that keep config.txt
import os
import re
import socket
import tempfile
from datetime import timedelta

from flask import Flask, Request, request, redirect, url_for, flash, send_from_directory, render_template, session

import system

PROJECT_URL = 'https://mp4museum.org'
UPSTREAM_REPO_URL = 'https://github.com/JuliusCode/MP4MUSEUM'
REPO_URL = 'https://github.com/lotech/mp4museum'


class MediaRequest(Request):
    """Write uploads straight to the media partition.

    Flask normally buffers uploads in /tmp, which is in RAM on the read-only
    overlay root and runs out of space for video files.
    """
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.endpoint != 'upload_file':
            return super()._get_file_stream(total_content_length, content_type, filename, content_length)
        stream = tempfile.NamedTemporaryFile('wb+', dir=system.MEDIA_PATH, prefix=system.UPLOAD_PREFIX, delete=False)
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
app.secret_key = system.session_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # Other web pages can't make the browser send the login cookie along with their requests
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


def is_fetch():
    """True for requests made by the page's JavaScript rather than a form."""
    return request.headers.get('X-Requested-With') == 'fetch'

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


# ----- Login ----- #
@app.before_request
def require_login():
    # The login page needs its stylesheet
    if request.endpoint in ('login', 'static'):
        return None
    if session.get('logged_in') and session.get('password') == system.password_fingerprint():
        return None
    if is_fetch():
        # A redirect would look like success to the page's JavaScript
        return "You have been logged out. Please reload the page and log in again.", 401
    return redirect(url_for('login'))

@app.context_processor
def inject_globals():
    return dict(project_url=PROJECT_URL, upstream_repo_url=UPSTREAM_REPO_URL, repo_url=REPO_URL,
                hostname=socket.gethostname())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if system.check_password(request.form.get('password', '')):
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['password'] = system.password_fingerprint()
            return redirect(url_for('index'))
        flash("Wrong password.", "error")
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/set_password', methods=['POST'])
def set_password():
    current = request.form.get('current_password', '')
    new = request.form.get('new_password', '')
    if not system.check_password(current):
        flash("Current password is wrong.", "error")
    elif not new:
        flash("The new password can't be empty.", "error")
    elif new != request.form.get('confirm_password', ''):
        flash("The new passwords don't match.", "error")
    else:
        try:
            system.save_password(new)
            # Logs out every other browser; this one gets a cookie signed with the new key
            app.secret_key = system.session_secret()
            session['password'] = system.password_fingerprint()
            flash("Password changed.", "success")
        except Exception as e:
            flash(f"Failed to change password: {e}", "error")
    return redirect(url_for('index'))


# ----- Home page ----- #
@app.route('/')
def index():
    files = []
    is_available = system.media_available()
    try:
        # Get all files and filter out hidden files and directories
        files = sorted(f for f in os.listdir(system.MEDIA_PATH)
                       if not f.startswith('.') and os.path.isfile(os.path.join(system.MEDIA_PATH, f)))
    except Exception as e:
        flash(f'Error reading directory: {str(e)}', 'error')

    # Get sound devices from aplay -l
    sound_status, sound_out = system.run_command(["aplay", "-l"])
    current_mode_key, current_mode = system.get_current_video_mode(system.read_config_text())
    free_space = system.get_free_space() if is_available else 0

    return render_template('index.html',
                           files=files,
                           media_path=system.MEDIA_PATH,
                           is_available=is_available,
                           free_space=free_space,
                           free_space_text=system.format_size(free_space),
                           sound_devices_text=sound_out if sound_status else '',
                           current_mode=current_mode,
                           current_mode_key=current_mode_key,
                           video_modes=system.VIDEO_MODES,
                           network_status=system.get_network_status(),
                           display_info=system.get_display_info(),
                           current_sound_card=system.get_current_sound_card(),
                           script_content=system.read_script_file(),
                           script_file=system.SCRIPT_FILE,
                           hostname_is_default=not os.path.exists(system.HOSTNAME_FILE),
                           using_default_password=not system.read_password_hash())


# ----- Media files ----- #
@app.route('/upload', methods=['POST'])
def upload_file():
    if not system.media_available():
        flash(f"The media partition {system.MEDIA_PATH} is not mounted.", "error")
        return redirect(url_for('index'))
    # Browsers always send the size; without it the space check can't work
    if not request.content_length:
        flash("Upload refused: the browser didn't say how big the file is.", "error")
        return redirect(url_for('index'))
    try:
        with system.writable(system.MEDIA_PATH):
            # Before the space check, so leftovers from a power cut can't block new uploads
            system.remove_stale_uploads()
            # Checked before receiving the file, it is written straight to the media partition
            with system.upload_space(request.content_length, uploaded_bytes(request._get_current_object())):
                try:
                    file = request.files.get('file')
                    if not file or not file.filename:
                        flash("No file selected.", "error")
                    elif not system.is_valid_filename(file.filename):
                        flash("Invalid filename. Names can't start with a dot or contain / \\ : * ? \" < > |", "error")
                    else:
                        file.stream.flush()
                        os.replace(file.stream.name, os.path.join(system.MEDIA_PATH, file.filename))
                        flash(f"File '{file.filename}' uploaded successfully.", "success")
                finally:
                    # Close the temp files before the partition goes back to read-only
                    discard_upload_temp_files()
    except system.NotEnoughSpace:
        flash("Not enough free space for this file.", "error")
    except Exception as e:
        flash(f"File upload failed: {e}", "error")
    return redirect(url_for('index'))

@app.route('/download/<filename>')
def download_file(filename):
    if not system.is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    return send_from_directory(system.MEDIA_PATH, filename, as_attachment=True)

@app.route('/delete', methods=['POST'])
def delete_file():
    filename = request.form.get('filename', '')
    if not system.media_available():
        flash(f"The media partition {system.MEDIA_PATH} is not mounted.", "error")
        return redirect(url_for('index'))
    if not system.is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    file_path = os.path.join(system.MEDIA_PATH, filename)
    if os.path.isfile(file_path):
        try:
            with system.writable(system.MEDIA_PATH):
                os.remove(file_path)
            flash(f"File '{filename}' deleted successfully.", "success")
        except Exception as e:
            flash(f"Failed to delete file: {e}", "error")
    else:
        flash("File not found.", "error")
    return redirect(url_for('index'))


# ----- Reboot ----- #
@app.route('/reboot', methods=['POST'])
def reboot_system():
    status, output = system.run_command(["reboot"])
    if status:
        flash("System is rebooting...", "success")
    else:
        flash("Failed to reboot: " + output, "error")
    # The reboot usually happens before this message can be seen
    return redirect(url_for('index'))

@app.route('/confirm_reboot')
def confirm_reboot():
    return render_template('confirm_reboot.html')


# ----- Sound ----- #
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
        with system.writable(system.BOOT_PATH):
            system.write_file(system.ALSA_FILE, device)
        flash(f"Sound device set to {device}.", "success")
    except Exception as e:
        flash("Error setting sound device: " + str(e), "error")
        return redirect(url_for('index'))
    return redirect(url_for('confirm_reboot'))

@app.route('/set_auto_sound', methods=['POST'])
def set_auto_sound():
    try:
        if os.path.exists(system.ALSA_FILE):
            with system.writable(system.BOOT_PATH):
                os.remove(system.ALSA_FILE)
            flash("Auto sound detection enabled.", "success")
        else:
            flash("Auto sound detection was already enabled.", "info")
    except Exception as e:
        flash("Error setting auto sound: " + str(e), "error")
    return redirect(url_for('index'))


# ----- Video ----- #
@app.route('/set_video_mode', methods=['POST'])
def set_video_mode():
    mode = request.form.get('mode')
    if mode not in system.VIDEO_MODES:
        flash("Invalid video mode selected.", "error")
        return redirect(url_for('index'))
    try:
        config_text = system.read_config_text()
        if not config_text:
            flash(f"Could not read {system.CONFIG_FILE}.", "error")
            return redirect(url_for('index'))
        with system.writable(system.BOOT_PATH):
            system.write_file(system.CONFIG_FILE, system.set_video_mode_in_config(config_text, mode))
        flash(f"Video mode set to {system.VIDEO_MODES[mode]['description']}.", "success")
        return redirect(url_for('confirm_reboot'))
    except Exception as e:
        flash(f"Error setting video mode: {str(e)}", "error")
        return redirect(url_for('index'))


# ----- Player script ----- #
@app.route('/save_script', methods=['POST'])
def save_script():
    success, message = system.write_script_file(request.form.get('script_content', ''))
    if is_fetch():
        # "Save and Reboot" only reboots when saving worked
        return message, 200 if success else 500
    flash(message, 'success' if success else 'error')
    return redirect(url_for('index'))


# ----- Network name ----- #
@app.route('/set_hostname', methods=['POST'])
def set_hostname():
    name = request.form.get('hostname', '').strip().lower()
    try:
        if name:
            if not system.HOSTNAME_RE.match(name):
                flash("Network names can only use letters, numbers and hyphens (-), up to 63 characters, "
                      "and can't start or end with a hyphen.", "error")
                return redirect(url_for('index'))
            with system.writable(system.BOOT_PATH):
                system.write_file(system.HOSTNAME_FILE, name + '\n')
        else:
            # Empty name: go back to the default
            if os.path.exists(system.HOSTNAME_FILE):
                with system.writable(system.BOOT_PATH):
                    os.remove(system.HOSTNAME_FILE)
            name = system.default_hostname()
    except Exception as e:
        flash(f"Failed to save network name: {e}", "error")
        return redirect(url_for('index'))

    status, output = system.apply_hostname(name)
    if not status:
        flash(f"Network name saved, but applying it failed: {output}. It will be used after a reboot.", "error")
        return redirect(url_for('index'))

    if request.host.split(':')[0].endswith('.local'):
        # The old address no longer works, so send the browser to the new one
        with open(os.path.join(app.static_folder, 'style.css'), 'r') as f:
            inline_css = f.read()
        return render_template('hostname_changed.html', new_url=f"http://{name}.local/", inline_css=inline_css)
    flash(f"Network name changed to {name}.local", "success")
    return redirect(url_for('index'))


if __name__ == '__main__':
    # Every player gets its own network name, so several can share a network
    name = system.configured_hostname()
    status, output = system.apply_hostname(name)
    if status:
        print(f"Network name: {name}.local", flush=True)
    else:
        print(f"Failed to set network name {name}: {output}", flush=True)
    # Run on all available IPs on port 80
    app.run(host='0.0.0.0', port=80, debug=False)
