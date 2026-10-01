"""Static checks on the daemon's startup configuration (WHISP-63).

Three mechanisms can try to start the daemon: the systemd user unit, the
XDG autostart fallback, and a manual `whisper-aloud --daemon`. These tests
parse the actual shipped files (not copies) and assert the invariants that
make the systemd unit the single authoritative owner of the D-Bus name,
loop-proof even when something keeps losing the race.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text()


def _exec_line(desktop_text: str) -> str:
    for line in desktop_text.splitlines():
        if line.startswith("Exec="):
            return line
    raise AssertionError("no Exec= line found")


def _heredoc_body(script_text: str, after_marker: str, heredoc_tag: str = "EOF") -> str:
    """Extract the content of the first heredoc appearing after `after_marker`."""
    idx = script_text.index(after_marker)
    tail = script_text[idx:]
    start = tail.index(f"<< {heredoc_tag}") + len(f"<< {heredoc_tag}")
    rest = tail[start:]
    end = rest.index(f"\n{heredoc_tag}\n")
    return rest[:end]


class TestSystemdUnitFile:
    """data/whisper-aloud.service: installed by both install_gnome_integration.sh
    and Debian packaging (debian/whisper-aloud.install)."""

    def test_type_is_dbus(self):
        text = _read("data/whisper-aloud.service")
        assert re.search(r"(?m)^Type=dbus$", text)

    def test_bus_name_matches(self):
        text = _read("data/whisper-aloud.service")
        assert "BusName=org.fede.whisperaloud" in text

    def test_restart_prevent_exit_status_covers_already_running_code(self):
        text = _read("data/whisper-aloud.service")
        m = re.search(r"(?m)^RestartPreventExitStatus=(.+)$", text)
        assert m, "RestartPreventExitStatus must be set so EXIT_ALREADY_RUNNING stops the restart loop"
        assert "3" in m.group(1).split()

    def test_start_limit_configured_in_unit_section(self):
        text = _read("data/whisper-aloud.service")
        unit_section = text.split("[Service]")[0]
        assert re.search(r"(?m)^StartLimitIntervalSec=\d+$", unit_section)
        assert re.search(r"(?m)^StartLimitBurst=\d+$", unit_section)


class TestInstallShEmbeddedUnit:
    """install.sh writes its own copy of the unit (with PassEnvironment
    added); it must carry the same restart-loop protections."""

    def test_type_is_dbus(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_user_service()")
        assert re.search(r"(?m)^Type=dbus$", body)

    def test_restart_prevent_exit_status_set(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_user_service()")
        m = re.search(r"(?m)^RestartPreventExitStatus=(.+)$", body)
        assert m and "3" in m.group(1).split()

    def test_start_limit_configured(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_user_service()")
        unit_section = body.split("[Service]")[0]
        assert re.search(r"(?m)^StartLimitIntervalSec=\d+$", unit_section)
        assert re.search(r"(?m)^StartLimitBurst=\d+$", unit_section)


class TestDbusActivationFile:
    """data/org.fede.whisperaloud.service must hand off to the systemd unit
    instead of letting a client call spawn a second process."""

    def test_points_to_systemd_unit(self):
        text = _read("data/org.fede.whisperaloud.service")
        assert "SystemdService=whisper-aloud.service" in text
        assert "Name=org.fede.whisperaloud" in text


class TestContribAutostartDesktop:
    """contrib/whisper-aloud-autostart.desktop is the non-systemd fallback;
    on a systemd session it must defer to the unit instead of racing it."""

    def test_no_racy_is_active_check(self):
        exec_line = _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
        assert "is-active" not in exec_line

    def test_prefers_systemctl_start_over_direct_spawn(self):
        exec_line = _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
        assert "systemctl --user start" in exec_line
        assert "whisper-aloud.service" in exec_line

    def test_direct_daemon_spawn_is_gated_behind_systemd_check(self):
        exec_line = _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
        assert "whisper-aloud --daemon" in exec_line
        # The systemd check must come before the fallback spawn, not after.
        assert exec_line.index("systemctl") < exec_line.index("whisper-aloud --daemon")


class TestInstallShAutostartEntry:
    """install.sh's generated ~/.config/autostart entry must use the same
    non-racy pattern as the contrib one."""

    def test_no_racy_is_active_check(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_autostart()")
        exec_line = _exec_line(body)
        assert "is-active" not in exec_line

    def test_prefers_systemctl_start_over_direct_spawn(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_autostart()")
        exec_line = _exec_line(body)
        assert "systemctl --user start" in exec_line
        assert "whisper-aloud.service" in exec_line
        assert exec_line.index("systemctl") < exec_line.index("whisper-aloud --daemon")


class TestLegacyAutostartCleanup:
    """Install scripts must remove the pre-WHISP-63 autostart entry that
    spawned the daemon directly with no coordination at all."""

    def test_install_gnome_integration_removes_legacy_autostart(self):
        text = _read("scripts/install_gnome_integration.sh")
        assert "whisperaloud-daemon.desktop" in text
        assert "rm " in text.split("whisperaloud-daemon.desktop")[1][:40] or \
               "rm \"$LEGACY_AUTOSTART\"" in text

    def test_install_sh_removes_legacy_autostart(self):
        text = _read("install.sh")
        func_start = text.index("install_autostart()")
        func_end = text.index("\n}\n", func_start)
        func_body = text[func_start:func_end]
        assert "whisperaloud-daemon.desktop" in func_body
        assert re.search(r"rm\s+\"\$legacy_autostart\"", func_body)
