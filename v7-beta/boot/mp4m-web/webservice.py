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
import threading
import uuid
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
    config_text = system.read_config_text() or ''
    current_mode_key, current_mode = system.get_current_video_mode(config_text)
    free_space = system.get_free_space() if is_available else 0
    board = system.board_memory_megabytes()

    return render_template('index.html',
                           playlist=system.get_playlist(),
                           player_log=system.read_player_log(),
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
                           gpu_mem=system.get_gpu_mem(config_text, board),
                           gpu_mem_in_sections=system.gpu_mem_in_model_sections(config_text),
                           gpu_mem_choices=system.gpu_mem_choices(board),
                           gpu_mem_recommended=system.recommended_gpu_mem(board),
                           memory_mb=system.memory_megabytes(),
                           network_status=system.get_network_status(),
                           device_info=system.get_device_info(),
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
                           boot_video_plays=system.get_boot_video_plays(),
                           show_address=system.get_show_address(),
                           start_up_settings=system.player_has_start_up_settings())


# ----- Player ----- #
def describe_player_status(status):
    """One line for the web interface, e.g. 'Playing intro.mp4 (internal) for 2 min'."""
    if not status:
        return "The player is not running. If it doesn't start again by itself within a minute, reboot."
    state = status.get('state')
    if state == 'stopped':
        return "The player is stopping."
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
            'play_file': False, 'rewind': False, 'previous': False, 'engine': None}
    if not status:
        return view
    path = status.get('file') or None
    view.update(state=status.get('state'), file=path, play_file=status.get('play_file') is True,
                rewind=status.get('rewind') is True, previous=status.get('previous') is True,
                engine=(status.get('engine') if status.get('engine') in ('vlc', 'omxplayer', 'omxplayer-sync')
                        and status.get('state') in ('playing', 'paused', 'sync') else None),
                name=os.path.basename(path) if path else None,
                folder=os.path.basename(os.path.dirname(path)) if path else None,
                kind=system.media_kind(path) if path else None, loop='loop.' in (path or ''))
    position, length, since = status.get('position'), status.get('length'), status.get('since')
    # how long since the status was written; the player also writes the monotonic clock, which
    # doesn't jump when the Pi sets its time from the network after starting
    mono = status.get('mono')
    if isinstance(mono, (int, float)):
        passed = max(0, time.monotonic() - mono)
    elif isinstance(since, (int, float)):
        passed = max(0, time.time() - since)
    else:
        passed = None
    if isinstance(position, (int, float)) and passed is not None:
        if view['state'] == 'playing':
            position += passed
        if isinstance(length, (int, float)) and length > 0:
            view['length'] = length
            position = min(position, length)
        view['position'] = round(position, 1)
    elif passed is not None and view['state'] in ('playing', 'paused', 'sync'):
        # an older player script, or omxplayer: how long it has been in this state
        view['elapsed'] = round(passed)
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
    elif entry['disabled']:
        error = "That file is switched off. Switch it on to play it."
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

@app.route('/player/previous', methods=['POST'])
def player_previous():
    # the player works out which file that is: it knows its files, their order and which it skips
    return player_command('previous', "Playing the previous file.",
                          "There's no previous file during start-up.",
                          "This player script can't go back to the previous file (it is from an older version).")

@app.route('/player/rewind', methods=['POST'])
def player_rewind():
    return player_command('rewind', "Back at the first frame. Press play to start.",
                          "The start-up video can't go back to the start.",
                          "This player script can't go back to the start (it is from an older version).")

def player_command(command, done, during_start_up, older):
    """Send the player a command it says it can do (its status has command: True; False during
    start-up; older players don't have it, and would take the signal for Next)."""
    status = system.get_player_status()
    if not status:
        error = "The player is not running."
    elif status.get('state') not in ('playing', 'paused'):
        error = "Nothing is playing." if status.get('state') != 'sync' else "This doesn't work in sync mode."
    elif status.get(command) is False:
        error = during_start_up
    elif status.get(command) is not True:
        error = older
    else:
        error = None
    if not error:
        try:
            if system.request_command(command):
                time.sleep(0.5)
            else:
                error = "The player is not running."
        except OSError as e:
            error = f"Couldn't ask the player: {e}"
    if error:
        if is_fetch():
            return dict(player_view(system.get_player_status()), error=error), 409
        flash(error, "error")
    elif is_fetch():
        return player_view(system.get_player_status())
    else:
        flash(done, "success")
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

@app.route('/set_boot_video_plays', methods=['POST'])
def set_boot_video_plays():
    value = request.form.get('boot_video_plays', '')
    if not value.isdecimal() or int(value) not in system.BOOT_VIDEO_PLAYS:
        flash("Please choose how often the boot video plays.", "error")
        return redirect(url_for('index'))
    try:
        system.save_player_setting('boot_video_plays', int(value))
        flash("The boot video won't play from the next start." if int(value) == 0 else
              f"The boot video will play {'once' if int(value) == 1 else 'twice'} from the next start.", "success")
    except Exception as e:
        flash(f"Failed to save the setting: {e}", "error")
    return redirect(url_for('index'))

@app.route('/set_show_address', methods=['POST'])
def set_show_address():
    value = request.form.get('show_address', '')
    if value not in ('yes', 'no'):
        flash("Please choose whether to show the address.", "error")
        return redirect(url_for('index'))
    try:
        system.save_player_setting('show_address', value)
        flash("The address will be shown on the logo screen from the next start." if value == 'yes'
              else "The address won't be shown on the logo screen from the next start.", "success")
    except Exception as e:
        flash(f"Failed to save the setting: {e}", "error")
    return redirect(url_for('index'))

@app.route('/set_loop_player', methods=['POST'])
def set_loop_player():
    choice = request.form.get('loop_player', '')
    if choice not in system.LOOP_PLAYERS:
        flash("Please choose VLC or omxplayer.", "error")
        return redirect(url_for('index'))
    name = 'omxplayer' if choice == 'omxplayer' else 'VLC'
    try:
        system.save_player_setting('loop_player', choice)
    except Exception as e:
        flash(f"Failed to save the setting: {e}", "error")
        return redirect(url_for('index'))
    # a loop video playing now starts again if another program will loop it (VLC is used if
    # omxplayer isn't installed). loop_player: the program looping it (while an omxplayer loop's
    # first frame is held, VLC shows it); older players only say which program shows it
    status = system.get_player_status() or {}
    playing = status.get('file') or ''
    # (loop_omx_ok: whether omxplayer could loop this file at all: file type, codec)
    will_use = ('omxplayer' if choice == 'omxplayer' and system.omxplayer_installed()
                and status.get('loop_omx_ok') is not False else 'vlc')
    if (status.get('state') in ('playing', 'paused') and 'loop.' in playing and status.get('play_file') is True
            and (status.get('loop_player') or status.get('engine')) in ('vlc', 'omxplayer')
            and (status.get('loop_player') or status.get('engine')) != will_use
            and any(entry['path'] == playing for entry in system.get_playlist())):
        try:
            if system.request_play(playing):
                flash(f"Loop videos are now played with {name}; {os.path.basename(playing)} has started again.", "success")
                return redirect(url_for('index'))
        except OSError:
            pass
    flash(f"Loop videos are now played with {name}.", "success")
    return redirect(url_for('index'))


# ----- Media files ----- #
def upload_message(message, category):
    """For the page's JavaScript, which shows the messages itself (kept out of the session cookie,
    which would grow with every file); a plain form gets them on the page as usual."""
    if is_fetch():
        g.setdefault('upload_messages', []).append([category, message])
    else:
        flash(message, category)

def upload_result():
    if is_fetch():
        messages = g.get('upload_messages', [])
        errors = [message for category, message in messages if category == 'error']
        return ({'ok': not errors, 'error': errors[-1] if errors else None, 'messages': messages},
                400 if errors else 200)
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload_file():
    if not system.media_available():
        upload_message(f"The media partition {system.MEDIA_PATH} is not mounted.", "error")
        return upload_result()
    # Browsers always send the size; without it the space check can't work
    if not request.content_length:
        upload_message("Upload refused: the browser didn't say how big the file is.", "error")
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
                        upload_message("No file selected.", "error")
                    elif not system.is_valid_filename(file.filename):
                        upload_message("Invalid filename. Names can't start with a dot or contain / \\ : * ? \" < > |", "error")
                    else:
                        file.stream.flush()
                        path = os.path.join(system.MEDIA_PATH, file.filename)
                        os.replace(file.stream.name, path)
                        upload_message(f"File '{file.filename}' uploaded successfully.", "success")
                        pixels = system.image_size(path) if system.media_kind(path) == 'image' else None
                        if system.is_large_image(pixels):
                            upload_message(f"'{file.filename}' is {pixels[0]}×{pixels[1]} pixels: a Pi 3 can't show images "
                                           f"over {system.LARGE_IMAGE_SIDE} pixels wide or high (they come out scrambled), "
                                           "so it skips it. Resize it to the screen's size, e.g. 1920×1080.", "warning")
                finally:
                    # Close the temp files before the partition goes back to read-only
                    discard_upload_temp_files()
    except system.NotEnoughSpace:
        upload_message("Not enough free space for this file.", "error")
    except Exception as e:
        upload_message(f"File upload failed: {e}", "error")
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
            return redirect(url_for('index'))
        # a file of the same name added later is played
        try:
            system.update_disabled_files(remove=[file_path])
        except Exception as e:
            flash(f"Couldn't take it off the list of switched-off files: {e}", "error")
    else:
        flash("File not found.", "error")
    return redirect(url_for('index'))


@app.route('/switch_file', methods=['POST'])
def switch_file():
    """Switch a file off (the player leaves it out) or on again, without changing the file."""
    path = request.form.get('file', '')
    entry = next((e for e in system.get_playlist() if e['path'] == path and e['plays']), None)
    if not entry and path not in system.get_disabled_files():
        flash("That file isn't in the playlist.", "error")
        return redirect(url_for('index'))
    off = request.form.get('off') == '1'
    try:
        system.update_disabled_files(add=[path] if off else [], remove=[] if off else [path])
    except Exception as e:
        flash(f"Failed to save the setting: {e}", "error")
        return redirect(url_for('index'))
    name = os.path.basename(path)
    status = system.get_player_status() or {}
    if not system.player_reads_disabled_files():
        flash(f"{name} is marked {'off' if off else 'on'}, but the player script on this Pi was edited before "
              "switching files off existed, so it plays every file. Updates keep an edited script and save "
              "the new one as /boot/mp4museum.py.new.", "warning")
    elif off and status.get('file') == path and status.get('state') == 'sync':
        # omxplayer-sync runs until the player stops
        flash(f"{name} is switched off: sync mode stops from the next start. Reboot to stop it now.", "success")
    elif off and status.get('file') == path and status.get('state') in ('playing', 'paused'):
        # playing now (a loop would go on until next): the player moves on
        system.signal_player(signal.SIGUSR1)
        flash(f"{name} is switched off: the player moves on and leaves it out.", "success")
    else:
        flash(f"{name} is switched off: the player leaves it out." if off else f"{name} is switched on again.", "success")
    return redirect(url_for('index'))


_rename_lock = threading.Lock()

@app.route('/rename', methods=['POST'])
def rename_file():
    filename = request.form.get('filename', '')
    new_name = request.form.get('new_name', '').strip()
    if not system.media_available():
        flash(f"The media partition {system.MEDIA_PATH} is not mounted.", "error")
        return redirect(url_for('index'))
    if not system.is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    if not system.is_valid_filename(new_name):
        flash("Invalid filename. Names can't start with a dot or contain / \\ : * ? \" < > |", "error")
        return redirect(url_for('index'))
    old_path = os.path.join(system.MEDIA_PATH, filename)
    new_path = os.path.join(system.MEDIA_PATH, new_name)
    # one at a time, so two renames to the same name can't overwrite a file
    with _rename_lock:
        if not os.path.isfile(old_path):
            flash("File not found.", "error")
            return redirect(url_for('index'))
        # the media partition (exFAT) ignores case: a name differing only in case is the same file,
        # and renaming it straight to that name changes nothing, so it goes through another name
        case_only = os.path.exists(new_path) and os.path.samefile(old_path, new_path)
        if os.path.exists(new_path) and not case_only:
            flash(f"There is already a file called '{new_name}'.", "error")
            return redirect(url_for('index'))
        try:
            with system.writable(system.MEDIA_PATH):
                if case_only:
                    between = os.path.join(system.MEDIA_PATH, '.rename-' + uuid.uuid4().hex)
                    os.rename(old_path, between)
                    try:
                        os.rename(between, new_path)
                    except OSError:
                        os.rename(between, old_path)
                        raise
                else:
                    os.rename(old_path, new_path)
        except Exception as e:
            flash(f"Failed to rename the file: {e}", "error")
            return redirect(url_for('index'))
        # switched off: it stays off under its new name
        if old_path in system.get_disabled_files():
            try:
                system.update_disabled_files(add=[new_path], remove=[old_path])
            except Exception as e:
                flash(f"Couldn't keep it switched off: {e}", "error")
    flash(f"Renamed '{filename}' to '{new_name}'.", "success")
    if '.' not in new_name:
        flash(f"'{new_name}' has no extension, so the player won't play it.", "warning")
    return redirect(url_for('index'))


# ----- Reboot ----- #
@app.route('/reboot', methods=['POST'])
def reboot_system():
    status, output = system.run_command(["reboot"])
    if is_fetch():
        # the page's script shows a failure (a redirect would hide it)
        return ("Rebooting.", 200) if status else (f"Failed to reboot: {output}", 500)
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


@app.route('/set_gpu_mem', methods=['POST'])
def set_gpu_mem():
    value = request.form.get('gpu_mem', '')
    choices = system.gpu_mem_choices()
    if not value.isdecimal() or int(value) not in choices:
        # too much leaves Linux too little to start
        flash(f"Please choose {' or '.join(str(c) for c in choices)} MB.", "error")
        return redirect(url_for('index'))
    try:
        config_text = system.read_config_text()
        if not config_text:
            flash(f"Could not read {system.CONFIG_FILE}.", "error")
            return redirect(url_for('index'))
        with system.writable(system.BOOT_PATH):
            system.write_file(system.CONFIG_FILE, system.set_gpu_mem_in_config(config_text, int(value)))
        flash(f"Graphics memory set to {int(value)} MB.", "success")
        return redirect(url_for('confirm_reboot'))
    except Exception as e:
        flash(f"Error setting the graphics memory: {e}", "error")
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
# one check at a time: pages opened together wait for its answer instead of all asking GitHub
_auto_check_lock = threading.Lock()
# longer than a check takes (updater.TIMEOUT for each request to GitHub)
AUTO_CHECK_WAIT = 90

@app.route('/check_update', methods=['POST'])
def check_update():
    if request.form.get('auto') != '1':
        return check_for_update(auto=False)
    if not _auto_check_lock.acquire(timeout=AUTO_CHECK_WAIT):
        # still checking: the page shows nothing now, and asks again next time it is opened
        return {'update': None}
    try:
        if _auto_check['time'] is not None:
            age = time.monotonic() - _auto_check['time']
            if age < (AUTO_CHECK_INTERVAL if _auto_check['ok'] else AUTO_CHECK_RETRY):
                return update_answer(_auto_check['latest'])
        return check_for_update(auto=True)
    finally:
        _auto_check_lock.release()

def check_for_update(auto):
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
    <div class="container narrow-page">
      <h2>Update installed</h2>
      {% for line in lines %}<p>{{ line }}</p>{% endfor %}
      {# One page: while the web interface restarts with the new version it says so, then it offers
         the reboot here (not on another page); the reboot is done from here too #}
      <p id="status" class="update-status">
        {% if restarting %}<span class="spinner"></span>Restarting the web interface with the new version...
        {% else %}Reboot the player to finish the update.{% endif %}</p>
      <div id="rebootButtons" class="button-row" {% if restarting %}hidden{% endif %}>
        <button type="button" class="button primary" onclick="rebootNow()">Reboot now</button>
        <a href="{{ url_for('index') }}" class="button button-link">Later</a>
      </div>
      {% if restarting %}
      <p id="laterOnly"><a href="{{ url_for('index') }}" class="button button-link">Later</a></p>
      {% endif %}
    </div>
    <script>
      const statusLine = document.getElementById('status');
      function offerReboot(text) {
        statusLine.textContent = text;
        document.getElementById('rebootButtons').hidden = false;
        const later = document.getElementById('laterOnly');
        if (later) later.hidden = true;
      }
      function rebootNow() {
        document.getElementById('rebootButtons').hidden = true;
        statusLine.innerHTML = '<span class="spinner"></span>Rebooting... This page goes back to the web interface when the player has started again.';
        fetch('{{ url_for('reboot_system') }}', {method: 'POST', headers: {'X-Requested-With': 'fetch'}})
          .then(response => {
            if (response.status === 401) {
              // logged out (e.g. the password was changed): nothing was rebooted
              stopWaiting = true;
              statusLine.textContent = 'You have been logged out, so the player was not rebooted. Log in again and reboot it.';
            } else if (!response.ok) {
              // the reboot command failed: say so, and offer it again
              stopWaiting = true;
              response.text().then(text => offerReboot(text || "The player couldn't reboot."));
            }
          })
          .catch(() => {});
        // give it time to go down, then wait for it to answer again
        setTimeout(waitForPlayer, 15000);
      }
      let stopWaiting = false;
      function waitForPlayer() {
        if (stopWaiting) {
          return;
        }
        fetch('{{ url_for('version') }}', {cache: 'no-store', signal: AbortSignal.timeout(5000)})
          .then(response => { if (response.ok) { window.location = '{{ url_for('index') }}'; } else { throw 0; } })
          .catch(() => setTimeout(waitForPlayer, 2000));
      }
      {% if restarting %}
      let offered = false;
      function waitForNewVersion() {
        if (offered) {
          return;
        }
        // (a request that never answers gives up, so the page doesn't wait on it)
        fetch('{{ url_for('version') }}', {headers: {'X-Requested-With': 'fetch'}, cache: 'no-store',
                                           signal: AbortSignal.timeout(5000)})
          .then(response => response.ok ? response.json() : {})
          .then(data => {
            if (data.commit === {{ new_commit|tojson }}) {
              offered = true;
              offerReboot('Ready. Reboot the player to finish the update.');
            } else {
              setTimeout(waitForNewVersion, 2000);
            }
          })
          .catch(() => setTimeout(waitForNewVersion, 2000));
      }
      setTimeout(waitForNewVersion, 3000);
      // a minute on the clock without an answer: the reboot starts the new version anyway
      setTimeout(() => {
        if (!offered) {
          offered = true;
          offerReboot("The web interface hasn't come back yet. Reboot the player to finish the update.");
        }
      }, 60000);
      {% endif %}
    </script>
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
