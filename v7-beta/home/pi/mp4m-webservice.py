#!/usr/bin/env python3
import os
import subprocess
import hashlib
import uuid
import re
from flask import Flask, request, redirect, url_for, flash, send_from_directory, render_template_string

app = Flask(__name__)

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

app.secret_key = get_hardware_key()

# Directories and commands
MEDIA_PATH = '/media/internal/'
BOOT_PATH = '/boot/'
ALSA_FILE = os.path.join(BOOT_PATH, "alsa.txt")
CONFIG_FILE = os.path.join(BOOT_PATH, "config.txt")
SCRIPT_FILE = '/boot/mp4museum.py'

# Default Raspberry Pi configuration
DEFAULT_CONFIG = '''# mp4 museum config v7 beta

disable_splash=1
avoid_warnings=1
gpu_mem=128
dtparam=audio=on
disable_overscan=1
bootcode_delay=5

# optional video and audio config:

# Ignore HDMI auto config, force 1080p at 60 hz
# this can fix problems with silly projectors or displays
# remove the # on all three lines below
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=16


# enable 4k with more than 30hz on raspi 4
# remove the # from the next line
#hdmi_enable_4kp60=1


# force UHD (2160p at 60Hz)
# remove the # on all 4 lines below
#hdmi_enable_4kp60=1
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=97

#force audio on hdmi
#hdmi_drive=2


############

# before chaning anything below, please read
# http://rpf.io/configtxt

############

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

[pi4]
# Enable DRM VC4 V3D driver on top of the dispmanx display stack
dtoverlay=vc4-fkms-v3d
max_framebuffers=2

# uncomment if you get no picture on HDMI for a default "safe" mode
#hdmi_safe=1

# uncomment this if your display has a black border of unused pixels visible
# and your display can output without overscan
#disable_overscan=1

# uncomment the following to adjust overscan. Use positive numbers if console
# goes off screen, and negative if there is too much border
#overscan_left=16
#overscan_right=16
#overscan_top=16
#overscan_bottom=16

# uncomment to force a console size. By default it will be display's size minus
# overscan.
#framebuffer_width=1280
#framebuffer_height=720

# uncomment if hdmi display is not detected and composite is being output
#hdmi_force_hotplug=1

# uncomment to force a specific HDMI mode (this will force VGA)
#hdmi_group=1
#hdmi_mode=1

# uncomment to force a HDMI mode rather than DVI. This can make audio work in
# DMT (computer monitor) modes
#hdmi_drive=2

# uncomment to increase signal to HDMI, if you have interference, blanking, or
# no display
#config_hdmi_boost=4

# uncomment for composite PAL
#sdtv_mode=2

[all]
'''

# Video mode configurations
VIDEO_MODE_CONFIGS = {
    'auto': {
        'description': 'Auto Mode',
        'config': DEFAULT_CONFIG
    },
    '1080p60': {
        'description': '1920x1080 FullHD 60fps',
        'config': '''# mp4 museum config v7 beta

disable_splash=1
avoid_warnings=1
gpu_mem=128
dtparam=audio=on
disable_overscan=1
bootcode_delay=5

# optional video and audio config:

# Ignore HDMI auto config, force 1080p at 60 hz
# this can fix problems with silly projectors or displays
# remove the # on all three lines below
hdmi_ignore_cec=1
hdmi_group=1
hdmi_mode=16


# enable 4k with more than 30hz on raspi 4
# remove the # from the next line
#hdmi_enable_4kp60=1


# force UHD (2160p at 60Hz)
# remove the # on all 4 lines below
#hdmi_enable_4kp60=1
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=97

#force audio on hdmi
#hdmi_drive=2


############

# before chaning anything below, please read
# http://rpf.io/configtxt

############

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

[pi4]
# Enable DRM VC4 V3D driver on top of the dispmanx display stack
dtoverlay=vc4-fkms-v3d
max_framebuffers=2

# uncomment if you get no picture on HDMI for a default "safe" mode
#hdmi_safe=1

# uncomment this if your display has a black border of unused pixels visible
# and your display can output without overscan
#disable_overscan=1

# uncomment the following to adjust overscan. Use positive numbers if console
# goes off screen, and negative if there is too much border
#overscan_left=16
#overscan_right=16
#overscan_top=16
#overscan_bottom=16

# uncomment to force a console size. By default it will be display's size minus
# overscan.
#framebuffer_width=1280
#framebuffer_height=720

# uncomment if hdmi display is not detected and composite is being output
#hdmi_force_hotplug=1

# uncomment to force a specific HDMI mode (this will force VGA)
#hdmi_group=1
#hdmi_mode=1

# uncomment to force a HDMI mode rather than DVI. This can make audio work in
# DMT (computer monitor) modes
#hdmi_drive=2

# uncomment to increase signal to HDMI, if you have interference, blanking, or
# no display
#config_hdmi_boost=4

# uncomment for composite PAL
#sdtv_mode=2

[all]
'''
    },
    '4k60': {
        'description': '4k 60fps (Raspberry 4)',
        'config': '''# mp4 museum config v7 beta

disable_splash=1
avoid_warnings=1
gpu_mem=128
dtparam=audio=on
disable_overscan=1
bootcode_delay=5

# optional video and audio config:

# Ignore HDMI auto config, force 1080p at 60 hz
# this can fix problems with silly projectors or displays
# remove the # on all three lines below
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=16


# enable 4k with more than 30hz on raspi 4
# remove the # from the next line
hdmi_enable_4kp60=1


# force UHD (2160p at 60Hz)
# remove the # on all 4 lines below
hdmi_enable_4kp60=1
hdmi_ignore_cec=1
hdmi_group=1
hdmi_mode=97

#force audio on hdmi
#hdmi_drive=2


############

# before chaning anything below, please read
# http://rpf.io/configtxt

############

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

[pi4]
# Enable DRM VC4 V3D driver on top of the dispmanx display stack
dtoverlay=vc4-fkms-v3d
max_framebuffers=2

# uncomment if you get no picture on HDMI for a default "safe" mode
#hdmi_safe=1

# uncomment this if your display has a black border of unused pixels visible
# and your display can output without overscan
#disable_overscan=1

# uncomment the following to adjust overscan. Use positive numbers if console
# goes off screen, and negative if there is too much border
#overscan_left=16
#overscan_right=16
#overscan_top=16
#overscan_bottom=16

# uncomment to force a console size. By default it will be display's size minus
# overscan.
#framebuffer_width=1280
#framebuffer_height=720

# uncomment if hdmi display is not detected and composite is being output
#hdmi_force_hotplug=1

# uncomment to force a specific HDMI mode (this will force VGA)
#hdmi_group=1
#hdmi_mode=1

# uncomment to force a HDMI mode rather than DVI. This can make audio work in
# DMT (computer monitor) modes
#hdmi_drive=2

# uncomment to increase signal to HDMI, if you have interference, blanking, or
# no display
#config_hdmi_boost=4

# uncomment for composite PAL
#sdtv_mode=2

[all]
'''
    },
    'ntsc': {
        'description': 'NTSC',
        'config': '''# mp4 museum config v7 beta

disable_splash=1
avoid_warnings=1
gpu_mem=128
dtparam=audio=on
disable_overscan=1
bootcode_delay=5

# optional video and audio config:

# Ignore HDMI auto config, force 1080p at 60 hz
# this can fix problems with silly projectors or displays
# remove the # on all three lines below
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=16


# enable 4k with more than 30hz on raspi 4
# remove the # from the next line
#hdmi_enable_4kp60=1


# force UHD (2160p at 60Hz)
# remove the # on all 4 lines below
#hdmi_enable_4kp60=1
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=97

#force audio on hdmi
#hdmi_drive=2


############

# before chaning anything below, please read
# http://rpf.io/configtxt

############

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

[pi4]
# Enable DRM VC4 V3D driver on top of the dispmanx display stack
dtoverlay=vc4-fkms-v3d
max_framebuffers=2

# uncomment if you get no picture on HDMI for a default "safe" mode
#hdmi_safe=1

# uncomment this if your display has a black border of unused pixels visible
# and your display can output without overscan
#disable_overscan=1

# uncomment the following to adjust overscan. Use positive numbers if console
# goes off screen, and negative if there is too much border
#overscan_left=16
#overscan_right=16
#overscan_top=16
#overscan_bottom=16

# uncomment to force a console size. By default it will be display's size minus
# overscan.
#framebuffer_width=1280
#framebuffer_height=720

# uncomment if hdmi display is not detected and composite is being output
#hdmi_force_hotplug=1

# uncomment to force a specific HDMI mode (this will force VGA)
#hdmi_group=1
#hdmi_mode=1

# uncomment to force a HDMI mode rather than DVI. This can make audio work in
# DMT (computer monitor) modes
#hdmi_drive=2

# uncomment to increase signal to HDMI, if you have interference, blanking, or
# no display
#config_hdmi_boost=4

# uncomment for composite NTSC
sdtv_mode=0

[all]
'''
    },
    'pal': {
        'description': 'PAL',
        'config': '''# mp4 museum config v7 beta

disable_splash=1
avoid_warnings=1
gpu_mem=128
dtparam=audio=on
disable_overscan=1
bootcode_delay=5

# optional video and audio config:

# Ignore HDMI auto config, force 1080p at 60 hz
# this can fix problems with silly projectors or displays
# remove the # on all three lines below
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=16


# enable 4k with more than 30hz on raspi 4
# remove the # from the next line
#hdmi_enable_4kp60=1


# force UHD (2160p at 60Hz)
# remove the # on all 4 lines below
#hdmi_enable_4kp60=1
#hdmi_ignore_cec=1
#hdmi_group=1
#hdmi_mode=97

#force audio on hdmi
#hdmi_drive=2


############

# before chaning anything below, please read
# http://rpf.io/configtxt

############

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

[pi4]
# Enable DRM VC4 V3D driver on top of the dispmanx display stack
dtoverlay=vc4-fkms-v3d
max_framebuffers=2

# uncomment if you get no picture on HDMI for a default "safe" mode
#hdmi_safe=1

# uncomment this if your display has a black border of unused pixels visible
# and your display can output without overscan
#disable_overscan=1

# uncomment the following to adjust overscan. Use positive numbers if console
# goes off screen, and negative if there is too much border
#overscan_left=16
#overscan_right=16
#overscan_top=16
#overscan_bottom=16

# uncomment to force a console size. By default it will be display's size minus
# overscan.
#framebuffer_width=1280
#framebuffer_height=720

# uncomment if hdmi display is not detected and composite is being output
#hdmi_force_hotplug=1

# uncomment to force a specific HDMI mode (this will force VGA)
#hdmi_group=1
#hdmi_mode=1

# uncomment to force a HDMI mode rather than DVI. This can make audio work in
# DMT (computer monitor) modes
#hdmi_drive=2

# uncomment to increase signal to HDMI, if you have interference, blanking, or
# no display
#config_hdmi_boost=4

# uncomment for composite PAL
sdtv_mode=2

[all]
'''
    }
}

# ----- Utility functions ----- #
def is_valid_filename(filename):
    # Basic check to avoid directory traversal attacks.
    return "/" not in filename and "\\" not in filename

def run_command(cmd):
    try:
        completed = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        return True, completed.stdout.strip()
    except subprocess.CalledProcessError as err:
        return False, err.stderr.strip()

def read_config_file():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return f.readlines()
    except Exception as e:
        return []

def write_config_file(lines):
    try:
        with open(CONFIG_FILE, 'w') as f:
            f.writelines(lines)
        return True, "Config file updated successfully"
    except Exception as e:
        return False, str(e)

def get_current_video_mode(config_lines):
    current_config = {}
    for line in config_lines:
        if '=' in line:
            key, value = line.strip().split('=', 1)
            current_config[key] = value
    
    if 'sdtv_mode' in current_config:
        return 'PAL' if current_config['sdtv_mode'] == '2' else 'NTSC'
    elif 'hdmi_enable_4kp60' in current_config and current_config['hdmi_enable_4kp60'] == '1':
        return '4K @ 60Hz'
    elif 'hdmi_mode' in current_config and 'hdmi_group' in current_config:
        mode = current_config['hdmi_mode']
        group = current_config['hdmi_group']
        if group == '2' and mode == '95':
            return '4K @ 30Hz'
        elif group == '1' and mode == '16':
            return 'Full HD @ 60Hz'
        elif group == '1' and mode == '33':
            return 'Full HD @ 30Hz'
        elif group == '1' and mode == '4':
            return 'HD Ready (720p)'
    return 'Unknown'

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
        # Get MAC addresses
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
        combined_info = "\n".join(mac_info) + "\n\nNetwork Configuration:\n" + "\n".join(filtered_lines)
        return combined_info
    except:
        return "Error getting network information"

def get_display_info():
    try:
        result = subprocess.run(['tvservice', '-s'], capture_output=True, text=True)
        return result.stdout
    except:
        return "Error getting display information"

def get_alsa_config():
    try:
        with open('/boot/alsa.txt', 'r') as f:
            content = f.read().strip()
            if content:
                return f"Card {content}"
    except FileNotFoundError:
        return "Auto sound config"
    except Exception:
        return "Error reading sound config"

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
        # Remount /boot as read-write first
        status, output = run_command("mount -o remount,rw /boot")
        if not status:
            return False, f"Failed to remount /boot as read-write: {output}"
        
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(SCRIPT_FILE), exist_ok=True)
            
            with open(SCRIPT_FILE, 'w') as f:
                f.write(content)
            return True, "Script saved successfully"
        finally:
            # Always try to remount as read-only when done
            run_command("mount -o remount,ro /boot")
    except Exception as e:
        return False, str(e)

# ----- Routes ----- #

# Home page: List files and show management options
@app.route('/')
def index():
    files = []
    is_writable = os.access(MEDIA_PATH, os.W_OK)
    try:
        # Get all files and filter out hidden files and directories
        all_files = os.listdir(MEDIA_PATH)
        files = [f for f in all_files if not f.startswith('.') and os.path.isfile(os.path.join(MEDIA_PATH, f))]
    except Exception as e:
        flash(f'Error reading directory: {str(e)}', 'error')

    # Get sound devices from aplay -l
    sound_status, sound_out = run_command("aplay -l")
    sound_devices = sound_out.splitlines() if sound_status else []

    # Read current video mode from config.txt
    config_lines = read_config_file()
    current_mode = get_current_video_mode(config_lines)

    # Read the Python script
    script_content = read_script_file()

    hostname = subprocess.check_output(['hostname']).decode().strip()

    # Template with all functionality
    template = '''
    <!doctype html>
    <html>
      <head>
        <title>MP4Museum</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
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

          function confirmAction(message, url) {
            if(confirm(message)) {
              window.location = url;
            }
          }

          function handleReboot() {
            if(confirm('Are you sure you want to reboot the system?')) {
              // Show reboot screen
              document.getElementById('rebootScreen').classList.add('active');
              
              // Start checking for server availability
              checkServerAndRedirect();
              
              // Trigger the reboot
              fetch('{{ url_for('reboot_system') }}')
                  .then(() => {
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
            
            // Check if it's within the valid range
            if (number < 0 || number > 99) {
                errorDiv.textContent = 'Please enter a number between 0 and 99';
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
                },
                body: 'script_content=' + encodeURIComponent(scriptContent)
              })
              .then(response => {
                if (response.ok) {
                  // Script saved successfully, now trigger reboot
                  handleReboot();
                } else {
                  alert('Failed to save script. Please try again.');
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
          <h1>MP4Museum</h1>
          
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
            <button class="tab" onclick="showTab('system')" title="View network information and system settings">Network</button>
          </div>
          
          <div id="media" class="tab-content active">
            <h2>Files in {{ media_path }}</h2>
            
            {% if is_writable %}
            <div class="form-group">
              <h3>Upload File</h3>
              <form method="post" action="{{ url_for('upload_file') }}" enctype="multipart/form-data" class="upload-form">
                <input type="file" name="file" title="Select a file from your computer to upload (supported formats: MP4, MP3, WAV, etc.)">
                <input type="submit" value="Upload" class="button" title="Upload the selected file to the media directory">
              </form>
            </div>
            {% else %}
            <div class="form-group">
              <h3>Upload File</h3>
              <div class="info-box" title="The system is currently in read-only mode">
                Directory is not writable. Please enable write access first.
              </div>
            </div>
            {% endif %}
            
            <ul class="file-list">
              {% for file in files %}
                <li>
                  <span>{{ file }}</span>
                  <div class="file-actions">
                    <a href="{{ url_for('download_file', filename=file) }}" title="Download this file to your computer">Download</a>
                    {% if is_writable %}
                    <a href="#" onclick="confirmAction('Are you sure you want to delete {{ file }}?', '{{ url_for('delete_file', filename=file) }}')" title="Permanently delete this file">Delete</a>
                    {% endif %}
                  </div>
                </li>
              {% endfor %}
            </ul>
            
            <div class="form-group">
              {% if not is_writable %}
              <button class="button" onclick="window.location='{{ url_for('remount_rw') }}'" title="Enable write access to allow file management and system changes">Enable Write Access</button>
              {% endif %}
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
                <button type="button" class="button" onclick="window.location='{{ url_for('set_auto_sound') }}'" title="Enable automatic sound device selection">Auto Mode</button>
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
                    <option value="auto" title="Use the default video configuration">Auto Mode</option>
                    <option value="1080p60" title="Full HD resolution at 60 frames per second">1920x1080 FullHD 60fps</option>
                    <option value="4k60" title="Ultra HD resolution at 60 frames per second">4k 60fps (Raspberry 4)</option>
                    <option value="ntsc" title="NTSC composite video output">NTSC</option>
                    <option value="pal" title="PAL composite video output">PAL</option>
                  </select>
                </div>
                <input type="submit" value="Apply Video Mode" class="button" title="Apply the selected video mode (requires system reboot)">
              </form>
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
            <h2>Network Information</h2>
            
            <div class="form-group">
              <h3>Network Configuration</h3>
              <pre title="Network configuration and MAC addresses">{{ network_status }}</pre>
            </div>
          </div>
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
    return render_template_string(template, files=files, media_path=MEDIA_PATH,
                                is_writable=is_writable,
                                sound_devices_text='\n'.join(sound_devices),
                                current_mode=current_mode,
                                mac_address=get_mac_address(),
                                network_status=get_network_status(),
                                display_info=get_display_info(),
                                current_sound_card=get_current_sound_card(),
                                script_content=script_content)

# --- File Management Endpoints --- #
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash("No file part in the request.", "error")
        return redirect(url_for('index'))
    file = request.files['file']
    if file.filename == '':
        flash("No file selected.", "error")
        return redirect(url_for('index'))
    if file:
        filename = file.filename
        if not is_valid_filename(filename):
            flash("Invalid filename.", "error")
            return redirect(url_for('index'))
        save_path = os.path.join(MEDIA_PATH, filename)
        try:
            # Remount media directory as read-write
            status, output = run_command("mount -o remount,rw " + MEDIA_PATH)
            if not status:
                flash("Failed to remount media directory as read-write: " + output, "error")
                return redirect(url_for('index'))
            file.save(save_path)
            flash(f"File '{filename}' uploaded successfully.", "success")
        except Exception as e:
            flash(f"File upload failed: {e}", "error")
    return redirect(url_for('index'))

@app.route('/download/<filename>')
def download_file(filename):
    if not is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    return send_from_directory(MEDIA_PATH, filename, as_attachment=True)

@app.route('/delete/<filename>')
def delete_file(filename):
    if not is_valid_filename(filename):
        flash("Invalid filename.", "error")
        return redirect(url_for('index'))
    file_path = os.path.join(MEDIA_PATH, filename)
    if os.path.exists(file_path):
        try:
            # Remount media directory as read-write
            status, output = run_command("mount -o remount,rw " + MEDIA_PATH)
            if not status:
                flash("Failed to remount media directory as read-write: " + output, "error")
                return redirect(url_for('index'))
            os.remove(file_path)
            flash(f"File '{filename}' deleted successfully.", "success")
        except Exception as e:
            flash(f"Failed to delete file: {e}", "error")
    else:
        flash("File not found.", "error")
    return redirect(url_for('index'))

@app.route('/remount')
def remount_rw():
    # Remount both media and boot directories as read-write
    media_cmd = "mount -o remount,rw " + MEDIA_PATH
    boot_cmd = "mount -o remount,rw " + BOOT_PATH
    
    # Try to remount media directory
    media_status, media_output = run_command(media_cmd)
    
    # Try to remount boot directory
    boot_status, boot_output = run_command(boot_cmd)
    
    if media_status and boot_status:
        flash("All directories remounted successfully as read-write.", "success")
    elif media_status:
        flash("Media directory remounted, but failed to remount boot: " + boot_output, "error")
    elif boot_status:
        flash("Boot directory remounted, but failed to remount media: " + media_output, "error")
    else:
        flash("Failed to remount directories: Media: " + media_output + ", Boot: " + boot_output, "error")
    
    return redirect(url_for('index'))

@app.route('/reboot')
def reboot_system():
    # Attempt to reboot the system (requires appropriate privileges)
    status, output = run_command("reboot")
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
    device = request.form.get('device')
    if not device:
        flash("No sound device provided.", "error")
        return redirect(url_for('index'))
    try:
        # Remount /boot as read-write
        status, output = run_command("mount -o remount,rw " + BOOT_PATH)
        if not status:
            flash("Failed to remount /boot as read-write: " + output, "error")
            return redirect(url_for('index'))
        # Write the device number to alsa.txt in /boot/
        with open(ALSA_FILE, "w") as f:
            f.write(device)
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
                fetch('{{ url_for('reboot_system') }}')
                    .then(() => {
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

@app.route('/set_auto_sound')
def set_auto_sound():
    try:
        # Remount /boot as read-write
        status, output = run_command("mount -o remount,rw " + BOOT_PATH)
        if not status:
            flash("Failed to remount /boot as read-write: " + output, "error")
            return redirect(url_for('index'))
            
        if os.path.exists(ALSA_FILE):
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
    
    if mode not in VIDEO_MODE_CONFIGS:
        flash("Invalid video mode selected.", "error")
        return redirect(url_for('index'))
    
    try:
        # Remount /boot as read-write
        status, output = run_command("mount -o remount,rw " + BOOT_PATH)
        if not status:
            flash("Failed to remount /boot as read-write: " + output, "error")
            return redirect(url_for('index'))
        
        # Get the configuration for the selected mode
        config_content = VIDEO_MODE_CONFIGS[mode]['config']
        
        # Write the complete config file
        with open(CONFIG_FILE, 'w') as f:
            f.write(config_content)
        
        flash(f"Video mode set to {VIDEO_MODE_CONFIGS[mode]['description']}.", "success")
        return redirect(url_for('confirm_reboot'))
    except Exception as e:
        flash(f"Error setting video mode: {str(e)}", "error")
        return redirect(url_for('index'))

@app.route('/save_script', methods=['POST'])
def save_script():
    script_content = request.form.get('script_content', '')
    success, message = write_script_file(script_content)
    flash(message, 'success' if success else 'error')
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Run on all available IPs on port 80
    app.run(host='0.0.0.0', port=80, debug=False)

