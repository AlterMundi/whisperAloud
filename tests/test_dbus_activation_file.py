"""Structural checks on the D-Bus activation and desktop-entry files (WHISP-60).

Flathub rejects tray-only apps with no window as their launch entry point
(flathub#9368, see docs/research/2026-10-01-sota-stt-offline/notes/
apps_packaging.md and docs/dbus-activation.md). These tests are a small,
dependency-free parser -- not a call into dbus-daemon itself -- asserting the
activation file carries both a SystemdService and an Exec fallback (see
docs/dbus-activation.md for how the SystemdService/Exec fallback behavior was
verified empirically against a real dbus-daemon), that the two agree with the
real systemd unit's filename, and that the desktop entry launches the GUI
window rather than the daemon directly.
"""

import configparser
import shutil
import subprocess
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ACTIVATION_FILE = DATA_DIR / "org.fede.whisperaloud.service"
SYSTEMD_UNIT_FILE = DATA_DIR / "whisper-aloud.service"
DESKTOP_FILE = DATA_DIR / "org.fede.whisperaloud.desktop"

BUS_NAME = "org.fede.whisperaloud"


def _parse_service_file(path: Path) -> configparser.ConfigParser:
    # interpolation=None (WHISP-96): a desktop entry's Exec= legitimately
    # contains literal "%"-field-codes (%f/%F/%u/%U/%%) that are not this
    # ini-style parser's own interpolation syntax -- the default
    # BasicInterpolation would raise InterpolationSyntaxError on "%F" the
    # moment one shows up (as it now does, for the file-open Exec=).
    parser = configparser.ConfigParser(interpolation=None)
    # D-Bus/systemd unit files are case-sensitive key=value under [Section]
    # headers, same shape as .ini; configparser's default optionxform()
    # lowercases keys, which would silently merge e.g. "Name" oddities, so
    # keep keys verbatim.
    parser.optionxform = str
    with path.open() as f:
        parser.read_file(f)
    return parser


class TestDbusActivationFile:
    def test_file_exists(self):
        assert ACTIVATION_FILE.is_file()

    def test_has_required_dbus_service_section(self):
        parser = _parse_service_file(ACTIVATION_FILE)
        assert parser.has_section("D-BUS Service")

    def test_name_matches_the_control_interface_bus_name(self):
        parser = _parse_service_file(ACTIVATION_FILE)
        assert parser.get("D-BUS Service", "Name") == BUS_NAME

    def test_has_systemd_service_key(self):
        parser = _parse_service_file(ACTIVATION_FILE)
        assert parser.has_option("D-BUS Service", "SystemdService")

    def test_systemd_service_matches_the_real_unit_filename(self):
        """SystemdService must name the unit file that actually ships in
        data/ (and gets installed to lib/systemd/user/ -- see
        debian/whisper-aloud.install), not a typo'd or stale name."""
        parser = _parse_service_file(ACTIVATION_FILE)
        assert parser.get("D-BUS Service", "SystemdService") == SYSTEMD_UNIT_FILE.name
        assert SYSTEMD_UNIT_FILE.is_file()

    def test_has_exec_fallback_key(self):
        """Required for activation to work at all when SystemdService can't
        be used (no systemd, no systemd --user session reachable from this
        bus, or the unit fails) -- see docs/dbus-activation.md."""
        parser = _parse_service_file(ACTIVATION_FILE)
        assert parser.has_option("D-BUS Service", "Exec")

    def test_exec_is_an_absolute_path(self):
        parser = _parse_service_file(ACTIVATION_FILE)
        exec_line = parser.get("D-BUS Service", "Exec")
        command = exec_line.split()[0]
        assert command.startswith("/"), f"Exec command must be absolute: {exec_line!r}"

    def test_exec_starts_the_daemon_not_the_gui(self):
        parser = _parse_service_file(ACTIVATION_FILE)
        exec_line = parser.get("D-BUS Service", "Exec")
        assert "--daemon" in exec_line.split()
        assert "gui" not in exec_line.lower()


class TestSystemdUnitFile:
    def test_bus_name_matches(self):
        parser = _parse_service_file(SYSTEMD_UNIT_FILE)
        assert parser.get("Service", "BusName") == BUS_NAME

    def test_type_is_dbus_activated(self):
        parser = _parse_service_file(SYSTEMD_UNIT_FILE)
        assert parser.get("Service", "Type") == "dbus"


class TestDesktopEntryIsWindowFirst:
    """WHISP-60: the desktop entry must launch the GUI window, never the
    daemon directly -- see docs/dbus-activation.md's "Window-first startup"
    section."""

    def test_file_exists(self):
        assert DESKTOP_FILE.is_file()

    def test_main_exec_does_not_pass_daemon_flag(self):
        parser = _parse_service_file(DESKTOP_FILE)
        exec_line = parser.get("Desktop Entry", "Exec")
        assert "--daemon" not in exec_line

    def test_main_exec_launches_the_gui_binary(self):
        parser = _parse_service_file(DESKTOP_FILE)
        exec_line = parser.get("Desktop Entry", "Exec")
        assert "whisper-aloud-gui" in exec_line

    def test_declared_actions_match_action_group_ids(self):
        """Regression: desktop-file-validate flags "action group ... exists,
        but there is no matching action" when a [Desktop Action X] group
        isn't listed in the main group's Actions= key. Both existed; Actions=
        was missing (fixed by this ticket)."""
        parser = _parse_service_file(DESKTOP_FILE)
        declared = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
        action_groups = [
            section[len("Desktop Action ") :]
            for section in parser.sections()
            if section.startswith("Desktop Action ")
        ]
        assert sorted(declared) == sorted(action_groups)


class TestDesktopEntryOpensFiles:
    """WHISP-96: "Open with WhisperAloud" on an audio/video file must launch
    the GUI with that file, both natively and (via flatpak-builder's
    automatic %f/%F -> document-portal rewriting) under Flatpak."""

    _EXPECTED_MIME_TYPES = {
        "audio/mpeg", "audio/x-m4a", "audio/mp4", "audio/wav", "audio/x-wav",
        "audio/ogg", "audio/flac", "audio/webm",
        "video/mp4", "video/webm", "video/x-matroska",
    }

    def test_declares_audio_and_video_mime_types(self):
        parser = _parse_service_file(DESKTOP_FILE)
        declared = {m for m in parser.get("Desktop Entry", "MimeType").split(";") if m}
        assert self._EXPECTED_MIME_TYPES <= declared

    def test_main_exec_passes_file_argument_placeholder(self):
        """%F (multiple local files) -- not %U -- since the GUI opens local/
        document-portal paths, never remote URIs."""
        parser = _parse_service_file(DESKTOP_FILE)
        exec_line = parser.get("Desktop Entry", "Exec")
        assert "%F" in exec_line.split()


@pytest.mark.skipif(
    shutil.which("desktop-file-validate") is None,
    reason="desktop-file-validate not installed",
)
def test_desktop_file_validate_reports_no_errors():
    """Runs the real validator (read-only; does not install anything) and
    asserts no "error:" lines. "hint:"-only output (e.g. multiple main
    Categories) is fine."""
    result = subprocess.run(
        ["desktop-file-validate", str(DESKTOP_FILE)],
        capture_output=True,
        text=True,
    )
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert not errors, f"desktop-file-validate reported errors:\n{result.stdout}"
