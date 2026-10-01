"""WHISP-47 e2e scenarios: GetConfig/SetConfig/ReloadConfig, ConfigChanged,
and the model-reload path for an engine switch. Run only via
scripts/test_e2e.sh -- see tests/e2e/conftest.py."""
import json

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_get_config_returns_flattened_keys(client):
    config = client.get_config()
    assert config["model.engine"] == "fake"
    assert config["transcription.language"] == "auto"
    assert "audio.max_recording_duration" in config


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: WhisperAloudClient.set_config() (src/whisper_aloud/service/client.py) "
        "forwards a plain Python dict straight to the pydbus proxy's SetConfig(a{sv}) "
        "call without wrapping each value as a GLib.Variant. pydbus validates "
        "outgoing argument types against the real introspected signature, so this "
        "raises 'Expected GLib.Variant, but got str' and the method call fails before "
        "it ever reaches the daemon -- set_config() always returns False against a "
        "real daemon, for both a valid change and an invalid one (both covered here, "
        "sharing one daemon -- they fail identically, before the daemon ever sees the "
        "call). tests/test_dbus_client.py's set_config tests never caught this because "
        "they mock the D-Bus proxy, which skips real argument-type checking. "
        "src/whisper_aloud/ui/main_window.py:1070 calls set_config() the same way, so "
        "this also breaks the GUI's language picker against a real daemon."
    ),
)
def test_set_config_fails_due_to_missing_variant_wrapping(client):
    changed = SignalCapture(client, "ConfigChanged")
    error = SignalCapture(client, "Error")

    # Valid change: should apply and emit ConfigChanged, but doesn't.
    assert client.set_config({"transcription.language": "en"}) is True
    changes = changed.wait(timeout=5.0)[0]
    assert changes.get("transcription.language") == "en" or "transcription.language" in changes
    config = client.get_config()
    assert config["transcription.language"] == "en"

    # Invalid value: should fail *at the daemon* with config_invalid, but
    # instead fails client-side before the call is even sent.
    assert client.set_config({"model.engine": "not-a-real-engine"}) is False
    code, message = error.wait(timeout=5.0)
    assert code == "config_invalid"
    assert message


def test_reload_config_rereads_file_and_emits_empty_changes(daemon):
    handle = daemon()
    client = handle.client()
    changed = SignalCapture(client, "ConfigChanged")

    # config.json is only written on the first save() -- SetConfig({})
    # materializes it (config.py hardcodes Path.home() / ".config", not
    # $XDG_CONFIG_HOME, so with HOME=<xdg_home> the file lands at
    # <xdg_home>/.config/whisper_aloud/config.json).
    assert client.set_config({}) is True
    changed.wait(timeout=5.0)
    config_path = handle.xdg_home / ".config" / "whisper_aloud" / "config.json"
    assert config_path.exists(), f"expected config.json at {config_path}"
    data = json.loads(config_path.read_text())
    data.setdefault("transcription", {})["language"] = "es"
    config_path.write_text(json.dumps(data))

    assert client.reload_config() is True
    changes = changed.wait(timeout=5.0)[0]
    assert changes == {}

    config = client.get_config()
    assert config["transcription.language"] == "es"

    client.disconnect()


def test_reload_config_malformed_file_fails(daemon):
    handle = daemon(config_overrides={"LANGUAGE": "en"})
    client = handle.client()
    error = SignalCapture(client, "Error")

    assert client.set_config({}) is True  # materialize config.json (see above)
    before = client.get_config()
    assert before["transcription.language"] == "en"

    config_path = handle.xdg_home / ".config" / "whisper_aloud" / "config.json"
    assert config_path.exists()
    config_path.write_text("{ not valid json")

    assert client.reload_config() is False
    code, message = error.wait(timeout=5.0)
    assert code == "reload_failed"
    assert message

    # WHISP-71: the live config must be left exactly as it was -- a failed
    # reload must not quietly fall back to defaults -- and the malformed
    # file on disk must not be overwritten either.
    config = client.get_config()
    assert config["transcription.language"] == "en"
    assert config_path.read_text() == "{ not valid json"

    client.disconnect()
