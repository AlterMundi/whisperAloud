"""WHISP-47 e2e scenarios: second-instance exit code and Quit, sharing one
daemon spawn. Spawns a raw subprocess directly (not via the `daemon`
fixture's factory) for the second-instance case, because `daemon()`'s
readiness check (`bus.get(BUS_NAME)`) would spuriously "pass" immediately --
the name is already owned by the FIRST daemon, so it can't be used to
confirm the SECOND process's own behavior. Only this file's own Popen
objects are ever killed, and only via .wait()/.terminate()/.kill() on those
Popen objects -- no name/pattern-based kills (see
tests/test_e2e_harness_safety.py). Run only via scripts/test_e2e.sh -- see
tests/e2e/conftest.py."""
import subprocess
import sys

import pytest

from tests._private_bus import private_bus_address
from tests.e2e.conftest import _DEATH_PACT
from tests.e2e.env_builder import build_daemon_env

pytestmark = pytest.mark.e2e


def test_second_instance_exit_code_then_quit_releases_bus_name(daemon, tmp_path):
    handle = daemon()  # first instance: owns org.fede.whisperaloud
    client = handle.client()
    assert client.get_status()["state"] == "idle"

    # --- Second instance on the same bus must exit EXIT_ALREADY_RUNNING ---
    second_home = tmp_path / "second-home"
    second_home.mkdir(parents=True, exist_ok=True)
    (second_home / "runtime").mkdir(parents=True, exist_ok=True)
    env2 = build_daemon_env(second_home, private_bus_address())

    proc2 = subprocess.Popen(
        [sys.executable, "-m", "whisper_aloud", "--daemon"],
        env=env2,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        preexec_fn=_DEATH_PACT,
    )
    try:
        returncode = proc2.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc2.terminate()
        try:
            proc2.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc2.kill()
            proc2.wait(timeout=5.0)
        pytest.fail("second daemon instance did not exit on its own within 10s")

    output = proc2.stdout.read() if proc2.stdout else ""
    assert returncode == 3, (
        f"expected EXIT_ALREADY_RUNNING (3), got {returncode}; output:\n{output}"
    )
    assert "another instance owns" in output.lower()

    # The first instance must be completely unaffected.
    assert client.get_status()["state"] == "idle"

    # --- Quit() releases the bus name (not just "the Popen exits") ---
    assert client.quit_daemon() is True
    handle.proc.wait(timeout=5.0)
    assert handle.proc.poll() is not None
    client.disconnect()

    third = daemon()
    assert third.client().get_status()["state"] == "idle"
