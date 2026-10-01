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


def test_set_config_applies_valid_and_rejects_invalid(client):
    changed = SignalCapture(client, "ConfigChanged")
    error = SignalCapture(client, "Error")

    # Valid change applies and emits ConfigChanged (WHISP-70).
    assert client.set_config({"transcription.language": "en"}) is True
    changes = changed.wait(timeout=5.0)[0]
    assert changes.get("transcription.language") == "en" or "transcription.language" in changes
    config = client.get_config()
    assert config["transcription.language"] == "en"

    # Invalid value is rejected by the daemon with config_invalid.
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
