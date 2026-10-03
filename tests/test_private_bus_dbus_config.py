"""Static checks on the dedicated dbus-daemon config used for every
"private" test bus (tests/dbus/session-test.conf) and on the wrapper
scripts that must reference it.

Incident: scripts/test_private_bus.sh used to call plain `dbus-run-session`
(no --config-file), which pulls in the system default session.conf's
<standard_session_servicedirs/>. On a machine with the real WhisperAloud
Flatpak installed, that directive makes the "private" bus scan XDG_DATA_DIRS
and discover
~/.local/share/flatpak/exports/share/dbus-1/services/org.fede.whisperaloud.service,
so the harness's first `bus.get("org.fede.whisperaloud")` D-Bus-activates the
real, maintainer's installed app instead of anything the test harness
spawned. These tests are purely static (string/XML parsing of files already
on disk) -- they never start a bus themselves.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "tests" / "dbus" / "session-test.conf"

_WRAPPER_SCRIPTS = (
    REPO_ROOT / "scripts" / "test_private_bus.sh",
    REPO_ROOT / "scripts" / "test_e2e.sh",
    REPO_ROOT / "scripts" / "test_gui.sh",
)


def _read_busconfig_root() -> ET.Element:
    # The file carries a DOCTYPE with an external DTD reference that
    # ElementTree cannot (and must not, offline) fetch; strip it before
    # parsing, same approach as any DOCTYPE-stripping XML-in-tests helper.
    text = CONFIG_FILE.read_text()
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.DOTALL)
    return ET.fromstring(text)


class TestSessionTestConfFile:
    def test_file_exists(self):
        assert CONFIG_FILE.is_file()

    def test_is_well_formed_xml(self):
        _read_busconfig_root()  # raises ParseError if malformed

    def test_is_session_type(self):
        root = _read_busconfig_root()
        assert root.find("type").text == "session"

    def test_has_no_standard_session_servicedirs(self):
        """This is the exact directive that caused the incident: it makes
        dbus-daemon scan XDG_DATA_DIRS (which includes the Flatpak exports
        dir) for activatable .service files."""
        root = _read_busconfig_root()
        assert root.find("standard_session_servicedirs") is None

    def test_has_no_servicedir(self):
        """No directory on this bus is activatable -- every daemon the
        harness talks to is spawned directly via subprocess.Popen."""
        root = _read_busconfig_root()
        assert root.findall("servicedir") == []

    def test_has_no_includedir_or_include(self):
        """<includedir>/<include> are how the real session.conf pulls in
        /etc/dbus-1/session.d and friends; none of that belongs on a
        throwaway test bus."""
        root = _read_busconfig_root()
        assert root.findall("includedir") == []
        assert root.findall("include") == []

    def test_has_listen_and_auth(self):
        root = _read_busconfig_root()
        assert root.find("listen") is not None
        assert root.find("auth") is not None

    def test_default_policy_allows_everything(self):
        """The harness (DaemonHandle, SignalCapture, the e2e `client`
        fixture, fake-portal tests) relies on being able to own names, send,
        and eavesdrop freely on its own throwaway bus -- same policy shape as
        the system default session.conf and the nested bus config in
        tests/e2e/test_dbus_activation.py."""
        root = _read_busconfig_root()
        policies = root.findall("policy")
        assert any(p.get("context") == "default" for p in policies)
        default_policy = next(p for p in policies if p.get("context") == "default")
        allows = default_policy.findall("allow")
        assert any(a.get("own") == "*" for a in allows)
        assert any(a.get("eavesdrop") == "true" for a in allows)


class TestWrapperScriptsUseDedicatedConfig:
    def test_test_private_bus_sh_passes_config_file(self):
        text = _WRAPPER_SCRIPTS[0].read_text()
        assert "dbus-run-session" in text
        assert "--config-file=" in text
        assert "tests/dbus/session-test.conf" in text

    def test_test_private_bus_sh_does_not_call_bare_dbus_run_session(self):
        """Regression guard for the incident: every `dbus-run-session`
        invocation in this script must carry --config-file on the same
        logical command, never a bare call that falls back to the system
        default session.conf."""
        text = _WRAPPER_SCRIPTS[0].read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if "dbus-run-session" not in line or stripped.startswith("#"):
                continue
            assert "--config-file=" in line, (
                f"bare dbus-run-session call (no --config-file) found: {line!r}"
            )

    def test_test_private_bus_sh_sanitizes_xdg_data_dirs(self):
        """Defense in depth: even if --config-file is ever dropped or
        misconfigured, XDG_DATA_DIRS must not carry the Flatpak exports dir
        (or anything else from the real environment) into the private bus's
        environment."""
        text = _WRAPPER_SCRIPTS[0].read_text()
        assert "XDG_DATA_DIRS" in text

    def test_test_e2e_and_test_gui_delegate_to_test_private_bus(self):
        # Both of these are thin wrappers; they must not start their own,
        # separately-configured dbus-run-session that could drift from the
        # dedicated config. test_gui.sh starts its own bus directly (it needs
        # its own disposable env -i / Xvfb wiring) but must still never call
        # dbus-run-session bare.
        e2e_text = _WRAPPER_SCRIPTS[1].read_text()
        assert "test_private_bus.sh" in e2e_text

        gui_text = _WRAPPER_SCRIPTS[2].read_text()
        for line in gui_text.splitlines():
            stripped = line.strip()
            if "dbus-run-session" not in line or stripped.startswith("#"):
                continue
            assert "--config-file=" in line, (
                f"bare dbus-run-session call (no --config-file) found in "
                f"test_gui.sh: {line!r}"
            )
