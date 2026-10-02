"""Behavioral test for D-Bus activation of the real daemon (WHISP-60).

Unlike every other test in tests/e2e/, this one does not use the `daemon`
fixture (which spawns the daemon itself via subprocess.Popen and talks to it
on the *outer* private bus scripts/test_e2e.sh already provides -- see
conftest.py). The whole point here is to prove the daemon can be started
*without* anyone ever calling Popen on it: by a D-Bus client's first method
call against org.fede.whisperaloud, the same way service/client.py's
WhisperAloudClient and the real GUI do it.

That needs its own, second, nested dbus-run-session with a custom
<servicedir> pointing at a temporary activation file -- a different bus from
the outer one conftest.py already set up, so it does not touch
DBUS_SESSION_BUS_ADDRESS or anything else conftest.py owns. See
docs/dbus-activation.md for how dbus-run-session --config-file was confirmed
to support this (empirically, against a throwaway probe service, outside
this repo's own bus name).

Safety: the activated daemon process is a grandchild of dbus-daemon, which is
itself a child of dbus-run-session, which this test starts in its own new
process session (start_new_session=True) so it has a process *group* of its
own. The primary shutdown path is the daemon's own Quit() over D-Bus -- same
as every other e2e test (tests/test_e2e_harness_safety.py forbids
name/pattern-based kills). The process-group signal below is a bounded
defense-in-depth fallback for a hang, scoped to the exact group this test
itself created, never a name or pattern match against real processes.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import textwrap

import pytest

from tests.e2e.env_builder import build_daemon_env

pytestmark = pytest.mark.e2e

BUS_NAME = "org.fede.whisperaloud"

_HAS_CONFIG_FILE_SUPPORT = False
if shutil.which("dbus-run-session"):
    _help_result = subprocess.run(
        ["dbus-run-session", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )
    # dbus-run-session prints --help's usage to stderr, not stdout.
    _HAS_CONFIG_FILE_SUPPORT = "--config-file" in _help_result.stdout

skip_reason = (
    "dbus-run-session is not installed, or this build does not support --config-file "
    "(checked via `dbus-run-session --help`); a .service-file-based activation test "
    "needs a nested bus configured with a custom <servicedir>, which requires that flag."
)


_PROBE_SCRIPT = textwrap.dedent(
    """
    import sys
    import time

    import pydbus

    BUS_NAME = {bus_name!r}

    def main():
        bus = pydbus.SessionBus()
        # Same call shape as WhisperAloudClient._try_connect(): the first
        # method call against the not-yet-running name, with pydbus's default
        # auto-start-allowed flags -- this is what activates it. dbus-daemon
        # reports activation as done once the process has claimed the bus
        # name (see service/daemon.py's acquire_bus_name(), which claims the
        # name *before* publishing the object on purpose, so a losing
        # instance fails fast without paying init cost) -- there is then a
        # short real window before the object itself is registered, so a
        # bounded retry here is load-bearing, not cosmetic.
        proxy = None
        last_error = None
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                proxy = bus.get(BUS_NAME)
                status = proxy.GetStatus()
                break
            except Exception as e:
                last_error = e
                time.sleep(0.1)
        else:
            print("ACTIVATION_FAILED", repr(last_error))
            return 1
        print("ACTIVATED", dict(status))

        try:
            proxy.Quit()
        except Exception as e:
            print("QUIT_CALL_FAILED", repr(e))
            return 1

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                bus.dbus.GetNameOwner(BUS_NAME)
            except Exception:
                print("SHUTDOWN_OK")
                return 0
            time.sleep(0.1)
        print("SHUTDOWN_TIMED_OUT")
        return 1

    sys.exit(main())
    """
).format(bus_name=BUS_NAME)


def _write_activation_fixture(tmp_path):
    """Write a temp <servicedir> with one Exec-only .service file (no
    SystemdService -- this nested bus has no systemd attached at all) that
    starts the real daemon in TEST_MODE, plus a session.conf wiring that
    servicedir into a fresh dbus-daemon."""
    servicedir = tmp_path / "servicedir"
    servicedir.mkdir()

    service_file = servicedir / f"{BUS_NAME}.service"
    service_file.write_text(
        f"[D-BUS Service]\nName={BUS_NAME}\nExec={sys.executable} -m whisper_aloud --daemon\n"
    )

    session_conf = tmp_path / "session.conf"
    session_conf.write_text(
        "<!DOCTYPE busconfig PUBLIC "
        '"-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" '
        '"http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">\n'
        "<busconfig>\n"
        "  <type>session</type>\n"
        "  <keep_umask/>\n"
        "  <listen>unix:tmpdir=/tmp</listen>\n"
        "  <auth>EXTERNAL</auth>\n"
        f"  <servicedir>{servicedir}</servicedir>\n"
        '  <policy context="default">\n'
        '    <allow send_destination="*" eavesdrop="true"/>\n'
        '    <allow eavesdrop="true"/>\n'
        '    <allow own="*"/>\n'
        "  </policy>\n"
        '  <limit name="service_start_timeout">20000</limit>\n'
        "</busconfig>\n"
    )

    probe_script = tmp_path / "probe.py"
    probe_script.write_text(_PROBE_SCRIPT)

    return session_conf, probe_script


@pytest.mark.skipif(not _HAS_CONFIG_FILE_SUPPORT, reason=skip_reason)
def test_first_method_call_activates_the_daemon(tmp_path):
    session_conf, probe_script = _write_activation_fixture(tmp_path)

    xdg_home = tmp_path / "home"
    xdg_home.mkdir()
    # dbus_address is unused -- the nested dbus-run-session below assigns its
    # own fresh address and exports it to everything it runs (dbus-daemon and
    # the probe script alike), overriding whatever we put here. Only the
    # allowlisted HOME/XDG/WHISPER_ALOUD_* variables from build_daemon_env
    # matter for this test.
    env = build_daemon_env(xdg_home, dbus_address="unix:path=/unused")
    del env["DBUS_SESSION_BUS_ADDRESS"]

    proc = subprocess.Popen(
        [
            "dbus-run-session",
            f"--config-file={session_conf}",
            "--",
            sys.executable,
            str(probe_script),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group, see module docstring
    )

    try:
        stdout, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        pgid = os.getpgid(proc.pid)
        # start_new_session made proc its own group leader; never signal any
        # other group (e.g. pytest's or the user's session).
        assert pgid == proc.pid, f"refusing to signal foreign process group {pgid}"
        os.killpg(pgid, signal.SIGTERM)
        try:
            stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            stdout, _ = proc.communicate(timeout=5)
        pytest.fail(f"nested dbus-run-session timed out; output so far:\n{stdout}")

    assert "ACTIVATED" in stdout, f"daemon was not activated; probe output:\n{stdout}"
    assert "SHUTDOWN_OK" in stdout, f"daemon did not shut down cleanly; probe output:\n{stdout}"
    assert proc.returncode == 0, f"probe script failed; output:\n{stdout}"
