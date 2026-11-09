# Added 2026 in https://github.com/lotech/mp4museum (see git history)
"""setup.sh, which turns a fresh Raspberry Pi OS Lite into a player. It changes partitions and
installs packages, so it can only really be tried on a Pi; these check what can be checked here."""
import re
import subprocess

from conftest import REPO

V7 = REPO / 'v7-beta'
SETUP = V7 / 'setup.sh'


def test_setup_script_is_valid_bash():
    subprocess.run(['bash', '-n', str(SETUP)], check=True)


def test_setup_installs_the_flask_and_werkzeug_the_tests_use():
    """The tests run with the v7 image's Flask and Werkzeug: a fresh setup must get the same."""
    setup = SETUP.read_text()
    for line in (REPO / 'tests' / 'requirements.txt').read_text().splitlines():
        match = re.match(r'(flask|werkzeug)==(\S+)', line)
        if match:
            assert ' {}=={} '.format(*match.groups()) in setup


def test_files_setup_copies_are_in_the_repository():
    """setup.sh runs from the v7-beta folder and copies these from it."""
    setup = SETUP.read_text()
    copied = re.findall(r'^\s*cp (?:-p )?((?:etc|boot)/\S+) ', setup, re.M)
    assert len(copied) >= 3
    for path in copied:
        assert (V7 / path).is_file(), path
    assert (V7 / 'install.sh').is_file()


def test_install_leaves_the_overlay_to_setup():
    """install.sh says to turn the overlay on, except when setup.sh runs it (it does that itself)."""
    install = (V7 / 'install.sh').read_text()
    assert install.index('[ -n "$MP4M_SETUP" ] && exit 0') < install.index('Now turn the overlay')
    assert 'MP4M_SETUP=1 ./install.sh' in SETUP.read_text()
