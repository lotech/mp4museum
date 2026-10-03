#!/usr/bin/env python3
# mp4museum web interface
# based on the MP4MUSEUM v7 beta web service by julius schmiedel - http://mp4museum.org
# licensed under the GNU GPL v3, see LICENSE
#
# modified 2026 in https://github.com/lotech/mp4museum (see git history):
# split into modules and templates, login, per-player network name, read-only
# partitions restored after writes, video presets that keep config.txt, player
# controls and playlist, background update check
import os
import re
import signal
import socket
import time
import tempfile
from datetime import timedelta

from flask import Flask, Request, request, redirect, url_for, flash, send_from_directory, render_template, render_template_string, session, g

import system
import updater

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
app.jinja_env.filters['size'] = system.format_size
# The version this process was started with; an update replaces the files but not the running code
RUNNING_VERSION = updater.installed_version()
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
    is_available = system.media_available()
    # Get sound devices from aplay -l
    sound_status, sound_out = system.run_command(["aplay", "-l"])
    current_mode_key, current_mode = system.get_current_video_mode(system.read_config_text())
    free_space = system.get_free_space() if is_available else 0

    return render_template('index.html',
                           playlist=system.get_playlist(),
                           player=player_view(system.get_player_status()),
                           media_path=system.MEDIA_PATH,
                           is_available=is_available,
                           free_space=free_space,
                           free_space_text=system.format_size(free_space),
                           sound_devices_text=sound_out if sound_status else '',
                           sound_cards=system.parse_sound_cards(sound_out) if sound_status else [],
                           current_mode=current_mode,
                           current_mode_key=current_mode_key,
                           video_modes=system.VIDEO_MODES,
                           network_status=system.get_network_status(),
                           display_info=system.get_display_info(),
                           current_sound_card=system.get_current_sound_card(),
                           script_content=system.read_script_file(),
                           script_file=system.SCRIPT_FILE,
                           hostname_is_default=not os.path.exists(system.HOSTNAME_FILE),
                           using_default_password=not system.read_password_hash(),
                           installed=RUNNING_VERSION,
                           update_config=updater.read_config(),
                           update_available=session.get('update'),
                           image_duration=system.get_image_duration(),
                           loop_player=system.get_loop_player(),
                           omxplayer_installed=system.omxplayer_installed())


# ----- Player ----- #
def describe_player_status(status):
    """One line for the web interface, e.g. 'Playing intro.mp4 (internal) for 2 min'."""
    if not status:
        return "The player is not running."
    state = status.get('state')
    if state == 'idle':
        return "Nothing to play: add files below or plug in a USB stick."
    path = status.get('file') or ''
    name = os.path.basename(path)
    folder = os.path.basename(os.path.dirname(path))
    if folder in ('internal',) or folder.startswith('usb'):
        name += f" ({folder})"
    minutes = int(max(0, time.time() - status.get('since', time.time())) // 60)
    duration = f" for {minutes} min" if minutes else ""
    if state == 'sync':
        return f"Sync mode: playing {name} with omxplayer-sync."
    return f"{'Paused' if state == 'paused' else 'Playing'} {name}{duration}"

def player_view(status):
    """What the web interface shows about the player. position is now, in seconds, so the
    browser doesn't depend on the Pi's clock (which may be wrong without a network)."""
    view = {'running': bool(status), 'state': None, 'text': describe_player_status(status), 'file': None,
            'name': None, 'folder': None, 'kind': None, 'loop': False, 'position': None, 'length': None,
            'play_file': False}
    if not status:
        return view
    path = status.get('file') or None
    view.update(state=status.get('state'), file=path, play_file=status.get('play_file') is True,
                name=os.path.basename(path) if path else None,
                folder=os.path.basename(os.path.dirname(path)) if path else None,
                kind=system.media_kind(path) if path else None, loop='loop.' in (path or ''))
    position, length, since = status.get('position'), status.get('length'), status.get('since')
    if isinstance(position, (int, float)) and isinstance(since, (int, float)):
        if view['state'] == 'playing':
            position += max(0, time.time() - since)
        if isinstance(length, (int, float)) and length > 0:
            view['length'] = length
            position = min(position, length)
        view['position'] = round(position, 1)
    elif isinstance(since, (int, float)) and view['state'] in ('playing', 'paused', 'sync'):
        # an older player script: how long it has been in this state
        view['elapsed'] = round(max(0, time.time() - since))
    return view

@app.route('/player/status')
def player_status():
    return player_view(system.get_player_status())

def control_player(signum, done):
    status = system.get_player_status()
    unavailable = {
        'sync': "Pause and Next don't work in sync mode.",
        'idle': "Nothing is playing.",
    }.get(status.get('state') if status else None)
    if unavailable:
        if is_fetch():
            return dict(player_status(), error=unavailable), 409
        flash(unavailable, "error")
        return redirect(url_for('index'))
    if system.signal_player(signum):
        # Give the player a moment so the status shows the change
        time.sleep(0.5)
        if not is_fetch():
            flash(done, "success")
    elif is_fetch():
        return dict(player_status(), error="The player is not running."), 409
    else:
        flash("The player is not running.", "error")
    if is_fetch():
        return player_status()
    return redirect(url_for('index'))

@app.route('/player/next', methods=['POST'])
def player_next():
    return control_player(signal.SIGUSR1, "Skipped to the next file.")

@app.route('/player/pause', methods=['POST'])
def player_pause():
    return control_player(signal.SIGUSR2, "Paused or resumed playback.")

@app.route('/player/play', methods=['POST'])
def player_play():
    path = request.form.get('file', '')
    status = system.get_player_status()
    entry = next((e for e in system.get_playlist() if e['path'] == path and e['plays']), None)
    if not entry:
        error = "That file isn't in the playlist."
    elif not status:
        error = "The player is not running."
    elif status.get('state') == 'sync':
        error = "Files can't be chosen in sync mode."
    elif status.get('play_file') is not True:
        error = "This player script can't choose a file (it is from an older version). Use Next instead."
    else:
        error = None
    if not error:
        try:
            if system.request_play(path):
                time.sleep(0.5)
            else:
                error = "The player is not running."
        except OSError as e:
            error = f"Couldn't ask the player to play it: {e}"
    if error:
        if is_fetch():
            return dict(player_view(system.get_player_status()), error=error), 409
        flash(error, "error")
    elif is_fetch():
        return player_view(system.get_player_status())
    else:
        flash(f"Playing {entry['name']}.", "success")
    return redirect(url_for('index'))

@app.route('/set_image_duration', methods=['POST'])
def set_image_duration():
    seconds = request.form.get('seconds', '').strip()
    if not seconds.isdecimal() or not 1 <= int(seconds) <= 86400:
        flash("Please enter a number of seconds from 1 to 86400.", "error")
        return redirect(url_for('index'))
    try:
        system.save_image_duration(int(seconds))
        flash(f"Images are now shown for {int(seconds)} seconds.", "success")
    except Exception as e:
        flash(f"Failed to save the image duration: {e}", "error")
    return redirect(url_for('index'))

@app.route('/set_loop_player', methods=['POST'])
def set_loop_player():
    choice = request.form.get('loop_player', '')
    if choice not in system.LOOP_PLAYERS:
        flash("Please choose VLC or omxplayer.", "error")
        return redirect(url_for('index'))
    try:
        system.save_player_setting('loop_player', choice)
        flash(f"Loop videos are now played with {'omxplayer' if choice == 'omxplayer' else 'VLC'}.", "success")
    except Exception as e:
        flash(f"Failed to save the setting: {e}", "error")
    return redirect(url_for('index'))


# ----- Media files ----- #
def upload_result():
    """Back to the page; the page's JavaScript gets {'ok': ...} and reloads it to show the messages."""
    if is_fetch():
        new = session.get('_flashes', [])[g.get('flashes_before', 0):]
        errors = [message for category, message in new if category == 'error']
        return {'ok': not errors, 'error': errors[-1] if errors else None}, 400 if errors else 200
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload_file():
    g.flashes_before = len(session.get('_flashes', []))
    if not system.media_available():
        flash(f"The media partition {system.MEDIA_PATH} is not mounted.", "error")
        return upload_result()
    # Browsers always send the size; without it the space check can't work
    if not request.content_length:
        flash("Upload refused: the browser didn't say how big the file is.", "error")
        return upload_result()
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
    return upload_result()

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
    if not re.fullmatch(r'[0-9]{1,2}', device):
        flash("Please enter a card number from 0 to 99.", "error")
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


# ----- Software update ----- #
# The page checks for updates by itself when it is opened, at most this often (seconds),
# and quietly: a player without internet just doesn't show one
AUTO_CHECK_INTERVAL = 6 * 3600
AUTO_CHECK_RETRY = 3600
_auto_check = {'time': None, 'ok': False, 'latest': None}

@app.route('/check_update', methods=['POST'])
def check_update():
    auto = request.form.get('auto') == '1'
    if auto and _auto_check['time'] is not None:
        age = time.monotonic() - _auto_check['time']
        if age < (AUTO_CHECK_INTERVAL if _auto_check['ok'] else AUTO_CHECK_RETRY):
            return update_answer(_auto_check['latest'])
    config = updater.read_config()
    try:
        latest = updater.latest_commit(config['repo'], config['branch'])
    except updater.UpdateError as e:
        if auto:
            _auto_check.update(time=time.monotonic(), ok=False, latest=None)
            return update_answer(None)
        if is_fetch():
            return {'update': None, 'error': str(e)}, 502
        flash(str(e), "error")
        return redirect(url_for('index'))
    found = find_update(config, latest)
    _auto_check.update(time=time.monotonic(), ok=True, latest=found)
    if is_fetch():
        return update_answer(found)
    if not found:
        flash("The software is up to date.", "success")
    else:
        message = f"An update is available: {latest['commit'][:7]} ({latest['date'][:10]})."
        installed_branch = RUNNING_VERSION.get('branch')
        if installed_branch and installed_branch != config['branch']:
            message += f" Installing it switches from branch {installed_branch} to {config['branch']}."
        elif RUNNING_VERSION.get('date') and latest['date'] < RUNNING_VERSION['date']:
            message += " It is older than the installed version."
        flash(message, "success")
    return redirect(url_for('index'))

def update_answer(latest):
    """JSON for the page's update bar."""
    if latest and latest['commit'] != RUNNING_VERSION.get('commit'):
        session['update'] = latest
        return {'update': {'commit': latest['commit'][:7], 'date': latest['date'][:10],
                           'message': latest.get('message', '')}}
    session.pop('update', None)
    return {'update': None}

def find_update(config, latest):
    """latest, if it isn't the installed version (and remembered for Install Update), else None."""
    global RUNNING_VERSION
    if latest['commit'] != RUNNING_VERSION.get('commit') and RUNNING_VERSION.get('commit') == 'local':
        # Installed with install.sh: find out whether it is this version already
        try:
            if updater.identify_local_copy(latest, config):
                RUNNING_VERSION = updater.installed_version()
        except Exception as e:
            # Not knowing just means the update is offered
            print(f"Couldn't compare the local copy with {latest['commit'][:7]}: {e}", flush=True)
    if latest['commit'] == RUNNING_VERSION.get('commit'):
        session.pop('update', None)
        return None
    session['update'] = latest
    return latest

@app.route('/install_update', methods=['POST'])
def install_update():
    latest = session.get('update')
    if not latest:
        flash("Check for updates first.", "error")
        return redirect(url_for('index'))
    try:
        summary = updater.update(latest)
    except updater.UpdateError as e:
        flash(f"Update failed, nothing was changed: {e}", "error")
        return redirect(url_for('index'))
    except Exception as e:
        flash(f"The update stopped part way: {e}. Try again, or run 'sudo mp4m-update --force' over SSH.", "error")
        return redirect(url_for('index'))
    session.pop('update', None)

    # Running as the systemd service: restart it with the new code once this page is sent
    restarting = False
    if os.environ.get('INVOCATION_ID'):
        restarting, output = system.run_command(['systemd-run', '--on-active=2', 'systemctl', 'restart', 'mp4m-webservice'])
        if not restarting:
            print(f"Failed to schedule a restart of the web interface: {output}", flush=True)
    try:
        # The templates have just been replaced, so this page doesn't use them
        with open(os.path.join(updater.APP_DIR, 'static', 'style.css'), 'r') as f:
            inline_css = f.read()
    except OSError:
        inline_css = ''
    return render_template_string(UPDATED_PAGE, lines=updater.describe(summary), restarting=restarting,
                                  new_commit=summary['version']['commit'], inline_css=inline_css)

@app.route('/version')
def version():
    return {'commit': RUNNING_VERSION.get('commit', '')}

UPDATED_PAGE = """<!doctype html>
<html>
  <head>
    <title>MP4Museum - Updated</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{{ inline_css|safe }}</style>
  </head>
  <body>
    <div class="container">
      <h2>Update installed</h2>
      {% for line in lines %}<p>{{ line }}</p>{% endfor %}
      {% if restarting %}
      <p id="status" class="hint">The web interface is restarting with the new version...</p>
      {% else %}
      <p class="hint">Reboot to use the new version.</p>
      {% endif %}
      <p><a href="{{ url_for('confirm_reboot') }}" class="button button-link">Reboot</a>
        <a href="{{ url_for('index') }}" class="button button-link">Later</a></p>
    </div>
    {% if restarting %}
    <script>
      // Wait for the new version to answer, then offer the reboot
      function waitForNewVersion() {
        fetch('{{ url_for('version') }}', {headers: {'X-Requested-With': 'fetch'}})
          .then(response => response.ok ? response.json() : {})
          .then(data => {
            if (data.commit === {{ new_commit|tojson }}) {
              window.location = '{{ url_for('confirm_reboot') }}';
            } else {
              setTimeout(waitForNewVersion, 2000);
            }
          })
          .catch(() => setTimeout(waitForNewVersion, 2000));
      }
      setTimeout(waitForNewVersion, 3000);
    </script>
    {% endif %}
  </body>
</html>
"""


if __name__ == '__main__':
    # A crash or restart can leave /boot or the media partition writable
    system.restore_read_only_mounts()
    # Keep using the templates this version started with, even after an update replaces the files
    for template_name in app.jinja_env.list_templates():
        app.jinja_env.get_template(template_name)
    # Every player gets its own network name, so several can share a network
    name = system.configured_hostname()
    status, output = system.apply_hostname(name)
    if status:
        print(f"Network name: {name}.local", flush=True)
    else:
        print(f"Failed to set network name {name}: {output}", flush=True)
    # Run on all available IPs on port 80
    app.run(host='0.0.0.0', port=80, debug=False)
