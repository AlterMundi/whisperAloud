"""Config section `startup` (WHISP-87: start in background at login)."""

import json

import pytest

from whisper_aloud.config import StartupConfig, WhisperAloudConfig, detect_config_changes


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("WHISPER_ALOUD_STARTUP_AUTOSTART_BACKGROUND", raising=False)


def test_default_is_off():
    config = WhisperAloudConfig.load()
    assert isinstance(config.startup, StartupConfig)
    assert config.startup.autostart_background is False


def test_round_trips_through_dict():
    config = WhisperAloudConfig()
    config.startup.autostart_background = True
    data = config.to_dict()
    assert data["startup"] == {"autostart_background": True}
    assert WhisperAloudConfig.from_dict(data).startup.autostart_background is True


def test_missing_section_in_old_file_keeps_default():
    restored = WhisperAloudConfig.from_dict({"model": {"name": "base"}})
    assert restored.startup.autostart_background is False


def test_persists_to_file(tmp_path):
    config = WhisperAloudConfig()
    config.startup.autostart_background = True
    path = config.save()
    assert json.loads(path.read_text())["startup"]["autostart_background"] is True
    assert WhisperAloudConfig.load().startup.autostart_background is True


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("0", False)])
def test_env_override(monkeypatch, raw, expected):
    monkeypatch.setenv("WHISPER_ALOUD_STARTUP_AUTOSTART_BACKGROUND", raw)
    assert WhisperAloudConfig.load().startup.autostart_background is expected


def test_non_bool_value_is_rejected():
    config = WhisperAloudConfig()
    config.startup.autostart_background = "yes"
    with pytest.raises(Exception, match="startup"):
        config.validate()


def test_change_detection_reports_startup():
    old = WhisperAloudConfig()
    new = old.copy()
    new.startup.autostart_background = True
    changes = detect_config_changes(old, new)
    assert "startup" in changes.changed_sections
    assert changes.changed_fields["startup"] == {"autostart_background"}
    assert not changes.requires_model_reload


def test_flat_config_key_is_exposed_for_setconfig():
    """GetConfig/SetConfig flatten to "<section>.<field>"; the GUI persists the
    portal's answer through SetConfig("startup.autostart_background")."""
    flat = {
        f"{section}.{key}"
        for section, values in WhisperAloudConfig().to_dict().items()
        for key in values
    }
    assert "startup.autostart_background" in flat
