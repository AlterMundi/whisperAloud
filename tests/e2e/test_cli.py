"""WHISP-47 e2e scenario: the `python -m whisper_aloud` CLI talking to a real
daemon over the private bus, exercising __main__.py's
check_service_running()/call_service_method() for real (design doc scenario
15). Run only via scripts/test_e2e.sh -- see tests/e2e/conftest.py."""
import subprocess
import sys
import time

import pytest

from tests._private_bus import private_bus_address
from tests.e2e.env_builder import build_daemon_env

pytestmark = pytest.mark.e2e


def _run_cli(*args: str, home) -> subprocess.CompletedProcess:
    env = build_daemon_env(home, private_bus_address())
    return subprocess.run(
        [sys.executable, "-m", "whisper_aloud", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_status_against_running_daemon(daemon, tmp_path):
    handle = daemon()
    client = handle.client()
    assert client.get_status()["state"] == "idle"

    result = _run_cli("status", home=handle.xdg_home)
    assert result.returncode == 0, result.stderr
    assert "WhisperAloud v" in result.stdout
    assert "State:    idle" in result.stdout

    client.disconnect()


def test_cli_start_stop_and_toggle_against_running_daemon(daemon):
    """start/stop/toggle share one daemon spawn (each is a cheap, independent
    CLI subprocess call; only the daemon itself is expensive to start)."""
    handle = daemon(fake_audio="tone")
    client = handle.client()

    result = _run_cli("start", home=handle.xdg_home)
    assert result.returncode == 0, result.stderr
    assert "Recording started." in result.stdout
    assert client.get_status()["state"] == "recording"

    time.sleep(0.1)

    result = _run_cli("stop", home=handle.xdg_home)
    assert result.returncode == 0, result.stderr
    assert "Recording stopped, transcribing..." in result.stdout

    # Wait for the (near-instant) fake transcription to finish so the
    # daemon is back to idle before exercising ToggleRecording via the CLI.
    deadline = time.monotonic() + 5.0
    while client.get_status()["state"] != "idle" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client.get_status()["state"] == "idle"

    result = _run_cli("toggle", home=handle.xdg_home)
    assert result.returncode == 0, result.stderr
    assert "State: recording" in result.stdout
    assert client.get_status()["state"] == "recording"

    client.disconnect()


def test_cli_against_daemon_not_running_exits_nonzero(tmp_path):
    """No daemon fixture spawned here at all -- the CLI must detect the
    absence of org.fede.whisperaloud on this run's private bus and fail
    cleanly rather than hang or crash."""
    home = tmp_path / "no-daemon-home"
    home.mkdir(parents=True, exist_ok=True)
    result = _run_cli("status", home=home)
    assert result.returncode == 1
    assert "daemon is not running" in result.stderr
