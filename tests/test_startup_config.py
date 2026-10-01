"""Static and behavioral checks on the daemon's startup configuration
(WHISP-63).

Three mechanisms can try to start the daemon: the systemd user unit, the
XDG autostart fallback, and a manual `whisper-aloud --daemon`. These tests
parse the actual shipped files (not copies) and assert the invariants that
make the systemd unit the single authoritative owner of the D-Bus name,
loop-proof even when something keeps losing the race. The Exec= shell
scripts are also *executed* (tests/test_startup_config.py ::
TestAutostartExecBehavior) against stubbed `systemctl`/daemon binaries --
never the real ones -- to verify the actual branching, since a static
grep can't catch things like `exec` replacing the shell and silently
discarding a fallback.
"""
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Absolute path so the interpreter is never looked up via a (test-controlled)
# PATH -- only the script's own internal `systemctl`/daemon lookups are.
SH = "/bin/sh"


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


def _sh_script_from_exec(exec_line: str) -> str:
    """Pull the single-quoted `sh -lc '...'` body out of an Exec= line."""
    m = re.match(r"Exec=sh -lc '(.*)'$", exec_line)
    assert m, f"unexpected Exec= form: {exec_line!r}"
    return m.group(1)


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

    def test_success_exit_status_covers_already_running_code(self):
        text = _read("data/whisper-aloud.service")
        m = re.search(r"(?m)^SuccessExitStatus=(.+)$", text)
        assert m, "SuccessExitStatus must be set so losing the race doesn't leave the unit failed"
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

    def test_success_exit_status_set(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_user_service()")
        m = re.search(r"(?m)^SuccessExitStatus=(.+)$", body)
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

    def test_checks_the_unit_is_installed_not_just_the_manager(self):
        # A bare "systemctl --user status" only proves a user manager
        # exists, not that whisper-aloud.service is actually installed --
        # "cat <unit>" is the right existence check.
        exec_line = _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
        assert "systemctl --user cat whisper-aloud.service" in exec_line
        assert "systemctl --user status" not in exec_line

    def test_prefers_systemctl_start_over_direct_spawn(self):
        exec_line = _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
        assert "systemctl --user start whisper-aloud.service" in exec_line

    def test_direct_daemon_spawn_is_gated_behind_systemd_check(self):
        exec_line = _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
        assert "whisper-aloud --daemon" in exec_line
        # The systemd check must come before the fallback spawn, not after.
        assert exec_line.index("systemctl") < exec_line.index("whisper-aloud --daemon")

    def test_fallback_is_not_dead_code_behind_exec(self):
        # `exec systemctl ...; exec daemon` replaces the shell on the first
        # exec, so a failing `systemctl --user start` (unit missing: exit 5,
        # start-limit hit: exit 1) would never reach the fallback. The
        # systemctl call must not be `exec`'d; only the final daemon spawn
        # (the one with nothing meaningful left to do afterwards) may be.
        script = _sh_script_from_exec(_exec_line(_read("contrib/whisper-aloud-autostart.desktop")))
        assert "exec systemctl" not in script
        assert script.rstrip().endswith("exec whisper-aloud --daemon")


class TestInstallShAutostartEntry:
    """install.sh's generated ~/.config/autostart entry must use the same
    non-racy pattern as the contrib one."""

    def test_no_racy_is_active_check(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_autostart()")
        exec_line = _exec_line(body)
        assert "is-active" not in exec_line

    def test_checks_the_unit_is_installed_not_just_the_manager(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_autostart()")
        exec_line = _exec_line(body)
        assert "systemctl --user cat whisper-aloud.service" in exec_line
        assert "systemctl --user status" not in exec_line

    def test_prefers_systemctl_start_over_direct_spawn(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_autostart()")
        exec_line = _exec_line(body)
        assert "systemctl --user start whisper-aloud.service" in exec_line
        assert exec_line.index("systemctl") < exec_line.index("whisper-aloud --daemon")

    def test_fallback_is_not_dead_code_behind_exec(self):
        text = _read("install.sh")
        body = _heredoc_body(text, "install_autostart()")
        script = _sh_script_from_exec(_exec_line(body))
        assert "exec systemctl" not in script
        assert script.rstrip().endswith('exec $VENV_PATH/bin/whisper-aloud --daemon')

    def test_comment_no_longer_calls_the_unit_check_racy(self):
        text = _read("install.sh")
        func_start = text.index("install_autostart()")
        func_end = text.index("\n}\n", func_start)
        func_comment = text[func_start:func_end].split("cat >")[0]
        assert "racy" not in func_comment.lower()


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


# ─── Dynamic Exec= behavior: real shell, stubbed systemctl/daemon ─────────

_SYSTEMCTL_STUB = """#!/bin/sh
# Stand-in for the real systemctl: never touches the real systemd user
# manager. Exit codes for the two subcommands these Exec= scripts use are
# driven entirely by env vars so the tests can exercise every branch.
sub="$2"
case "$sub" in
    cat) exit "${STUB_SYSTEMCTL_CAT_EXIT:-0}" ;;
    start) exit "${STUB_SYSTEMCTL_START_EXIT:-0}" ;;
    *) exit 0 ;;
esac
"""

# Uses `:` (a shell builtin) for the marker write and `exit` for its own
# status, so it needs nothing from PATH itself -- it cannot accidentally
# depend on, or shell out to, anything real.
_DAEMON_STUB = """#!/bin/sh
: > "$STUB_SPAWN_MARKER"
exit 0
"""


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def exec_sandbox(tmp_path):
    """A PATH/VENV_PATH sandbox for running Exec= shell scripts for real.

    Two PATH variants are prepared:
      * `with_systemd`: a stub `systemctl` (controllable exit codes) plus a
        stub `whisper-aloud`, *ahead of* /usr/bin:/bin so it shadows the
        real systemctl there.
      * `without_systemd`: only the stub `whisper-aloud` -- no systemctl
        anywhere on PATH, and /usr/bin:/bin deliberately left out so the
        real systemctl at /usr/bin/systemctl cannot be found either.

    The real systemctl is never invoked in either variant.
    """
    with_systemd = tmp_path / "bin_with_systemd"
    without_systemd = tmp_path / "bin_without_systemd"
    venv_bin = tmp_path / "venv" / "bin"
    for d in (with_systemd, without_systemd, venv_bin):
        d.mkdir(parents=True)

    _make_executable(with_systemd / "systemctl", _SYSTEMCTL_STUB)
    _make_executable(with_systemd / "whisper-aloud", _DAEMON_STUB)
    _make_executable(without_systemd / "whisper-aloud", _DAEMON_STUB)
    _make_executable(venv_bin / "whisper-aloud", _DAEMON_STUB)

    spawn_marker = tmp_path / "spawned"

    class Sandbox:
        venv_path = str(tmp_path / "venv")

        def run(self, script: str, *, systemd_available: bool, cat_exit=0, start_exit=0):
            spawn_marker.unlink(missing_ok=True)
            if systemd_available:
                path = f"{with_systemd}:/usr/bin:/bin"
            else:
                path = str(without_systemd)

            env = {
                "PATH": path,
                "VENV_PATH": self.venv_path,
                "STUB_SPAWN_MARKER": str(spawn_marker),
                "STUB_SYSTEMCTL_CAT_EXIT": str(cat_exit),
                "STUB_SYSTEMCTL_START_EXIT": str(start_exit),
                "HOME": str(tmp_path),
            }

            # Prove the resolution the test depends on *before* running the
            # real script, so a wrong PATH fails loudly here, not silently
            # inside the thing under test.
            resolved = subprocess.run(
                [SH, "-c", "command -v systemctl"], env=env, capture_output=True, text=True,
            )
            if systemd_available:
                assert resolved.returncode == 0
                assert resolved.stdout.strip() == str(with_systemd / "systemctl"), (
                    "command -v systemctl must resolve to the stub, never /usr/bin/systemctl"
                )
            else:
                assert resolved.returncode != 0 and not resolved.stdout.strip(), (
                    "systemctl must not be resolvable at all in the no-systemd case"
                )

            result = subprocess.run([SH, "-c", script], env=env, capture_output=True, text=True)
            return result, spawn_marker.exists()

    return Sandbox()


class TestAutostartExecBehavior:
    """Actually run the Exec= scripts (against stubs) for both
    contrib/whisper-aloud-autostart.desktop and install.sh's generated
    entry, covering every branch the audit called out."""

    @pytest.fixture(params=["contrib", "install_sh"])
    def script_and_daemon_check(self, request):
        """Returns the Exec= shell-script body for each of the two
        autostart entries."""
        if request.param == "contrib":
            script = _sh_script_from_exec(
                _exec_line(_read("contrib/whisper-aloud-autostart.desktop"))
            )
        else:
            text = _read("install.sh")
            body = _heredoc_body(text, "install_autostart()")
            script = _sh_script_from_exec(_exec_line(body))
        return script

    def test_no_systemctl_falls_back_to_direct_spawn(self, exec_sandbox, script_and_daemon_check):
        script = script_and_daemon_check
        result, spawned = exec_sandbox.run(script, systemd_available=False)
        assert spawned, f"daemon stub was not spawned; stderr={result.stderr!r}"

    def test_unit_missing_falls_back_to_direct_spawn(self, exec_sandbox, script_and_daemon_check):
        script = script_and_daemon_check
        # `systemctl --user cat <unit>` exits 1 when the unit file can't be found.
        result, spawned = exec_sandbox.run(script, systemd_available=True, cat_exit=1, start_exit=0)
        assert spawned, f"daemon stub was not spawned; stderr={result.stderr!r}"

    def test_unit_present_and_start_succeeds_does_not_spawn_directly(
        self, exec_sandbox, script_and_daemon_check
    ):
        script = script_and_daemon_check
        result, spawned = exec_sandbox.run(script, systemd_available=True, cat_exit=0, start_exit=0)
        assert not spawned, "systemd owns startup here; the script must not also spawn the daemon"
        assert result.returncode == 0

    @pytest.mark.parametrize("start_exit", [1, 5])
    def test_unit_present_but_start_fails_falls_back_to_direct_spawn(
        self, exec_sandbox, script_and_daemon_check, start_exit
    ):
        # 1: generic failure (e.g. StartLimitBurst hit). 5: unit not loaded.
        # Either way `exec systemctl ...` must NOT have replaced the shell,
        # or this fallback would be unreachable dead code.
        script = script_and_daemon_check
        result, spawned = exec_sandbox.run(
            script, systemd_available=True, cat_exit=0, start_exit=start_exit
        )
        assert spawned, f"daemon stub was not spawned; stderr={result.stderr!r}"
