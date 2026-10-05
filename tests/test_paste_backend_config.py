"""clipboard.paste_backend (WHISP-119): config field, env override, validation.

"auto" (default) never selects the RemoteDesktop portal; "remote-desktop" is
the explicit opt-in rollback switch (ADR 0001).
"""

import pytest

from whisper_aloud.config import (
    ENV_OVERRIDE_VARS,
    PASTE_BACKENDS,
    ClipboardConfig,
    WhisperAloudConfig,
    detect_config_changes,
)
from whisper_aloud.exceptions import ConfigurationError


def test_default_is_auto():
    assert ClipboardConfig().paste_backend == "auto"
    assert WhisperAloudConfig().clipboard.paste_backend == "auto"


def test_known_values():
    assert PASTE_BACKENDS == ("auto", "extension", "remote-desktop", "clipboard")


@pytest.mark.parametrize("value", ["auto", "extension", "remote-desktop", "clipboard"])
def test_valid_values_pass_validation(value):
    config = WhisperAloudConfig()
    config.clipboard.paste_backend = value
    config.validate()


@pytest.mark.parametrize("value", ["portal", "ydotool", "", "AUTO", None, 1])
def test_invalid_values_rejected(value):
    config = WhisperAloudConfig()
    config.clipboard.paste_backend = value
    with pytest.raises(ConfigurationError, match="paste_backend"):
        config.validate()


def test_round_trips_through_dict():
    config = WhisperAloudConfig()
    config.clipboard.paste_backend = "remote-desktop"
    data = config.to_dict()
    assert data["clipboard"]["paste_backend"] == "remote-desktop"
    assert WhisperAloudConfig.from_dict(data).clipboard.paste_backend == "remote-desktop"


def test_old_config_without_field_gets_auto():
    data = WhisperAloudConfig().to_dict()
    del data["clipboard"]["paste_backend"]
    assert WhisperAloudConfig.from_dict(data).clipboard.paste_backend == "auto"


def test_env_override(monkeypatch):
    assert ENV_OVERRIDE_VARS["clipboard.paste_backend"] == "WHISPER_ALOUD_CLIPBOARD_PASTE_BACKEND"
    monkeypatch.setenv("WHISPER_ALOUD_CLIPBOARD_PASTE_BACKEND", "clipboard")
    config = WhisperAloudConfig()
    config._apply_env_overrides()
    assert config.clipboard.paste_backend == "clipboard"


def test_change_is_detected():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.clipboard.paste_backend = "remote-desktop"
    changes = detect_config_changes(old, new)
    assert "paste_backend" in changes.changed_fields.get("clipboard", set())
