"""WHISP-87 e2e: the GetStatus readiness keys (API 1.7.0) and the
startup.autostart_background config key, against a real daemon on the
private bus. The daemon here is the same `python -m whisper_aloud --daemon`
a login autostart entry runs: no GUI process exists in this harness.
Run only via scripts/test_e2e.sh -- see tests/e2e/conftest.py."""
import pytest

from tests.e2e.conftest import SignalCapture, wait_for_model_state

pytestmark = pytest.mark.e2e


def test_status_readiness_keys_without_any_window(daemon):
    """With only the daemon running, GetStatus carries everything an
    indicator needs for "all OK": model_state, hotkey_bound and autostart.
    The harness runs in test mode (no hotkey backend at all), so
    hotkey_bound must be False, never a missing key."""
    handle = daemon()
    wait_for_model_state(handle.client(), "ready", timeout=10.0)  # disconnects its client
    client = handle.client()
    try:
        status = client.get_status()
        assert status["model_state"] == "ready"
        assert status["hotkey_bound"] is False
        assert status["hotkey_backend"] == "none"
        assert status["hotkey_trigger"] == ""
        assert status["autostart"] is False
        assert client.get_api_version() == "1.7.1"
    finally:
        client.disconnect()


def test_autostart_env_override_is_reported(daemon):
    handle = daemon(config_overrides={"STARTUP_AUTOSTART_BACKGROUND": "1"})
    client = handle.client()
    try:
        assert client.get_status()["autostart"] is True
        assert client.get_config()["startup.autostart_background"] is True
    finally:
        client.disconnect()


def test_set_config_autostart_round_trip(client):
    """The GUI stores the portal's confirmed answer through SetConfig; the
    daemon must persist it, emit ConfigChanged and report it in GetStatus."""
    changed = SignalCapture(client, "ConfigChanged")

    assert client.set_config({"startup.autostart_background": True}) is True
    changes = changed.wait(timeout=5.0)[0]
    assert changes["startup.autostart_background"] is True
    assert client.get_status()["autostart"] is True
    assert client.get_config()["startup.autostart_background"] is True

    assert client.set_config({"startup.autostart_background": False}) is True
    changed.wait(timeout=5.0, predicate=lambda c: c.get("startup.autostart_background") is False)
    assert client.get_status()["autostart"] is False


def test_set_config_rejects_a_non_bool_autostart(client):
    error = SignalCapture(client, "Error")
    assert client.set_config({"startup.autostart_background": "yes"}) is False
    code, _message = error.wait(timeout=5.0)
    assert code == "config_invalid"
    assert client.get_status()["autostart"] is False
