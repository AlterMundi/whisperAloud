"""Config section `input_gain` (input normalization, docs/design/input-gain-loop.md)."""

import pytest

from whisper_aloud.config import (
    InputGainConfig,
    WhisperAloudConfig,
    detect_config_changes,
)
from whisper_aloud.exceptions import ConfigurationError


def test_defaults():
    cfg = InputGainConfig()
    assert cfg.enabled is True
    assert cfg.target_dbfs == pytest.approx(-20.0)
    assert cfg.max_gain_db == pytest.approx(30.0)
    assert cfg.max_output_noise_dbfs == pytest.approx(-40.0)
    assert cfg.peak_ceiling_dbfs == pytest.approx(-3.0)
    assert cfg.max_gain_change_db_per_s == pytest.approx(10.0)
    # The user's mic gain is theirs: hardware control is opt-in.
    assert cfg.hardware_control is False


def test_main_config_has_section_and_leaves_existing_sections_alone():
    config = WhisperAloudConfig()
    assert isinstance(config.input_gain, InputGainConfig)
    # Scope: the existing chain's defaults are untouched.
    assert config.audio_processing.noise_gate_threshold_db == pytest.approx(-40.0)
    assert config.audio_processing.agc_enabled is True
    assert config.recording_flow.raise_mic_gain is True


def test_round_trip():
    config = WhisperAloudConfig()
    config.input_gain.target_dbfs = -24.0
    config.input_gain.enabled = False
    config.input_gain.hardware_control = True
    data = config.to_dict()
    assert data["input_gain"]["target_dbfs"] == pytest.approx(-24.0)
    restored = WhisperAloudConfig.from_dict(data)
    assert restored.input_gain == config.input_gain


def test_old_config_without_section_gets_defaults():
    restored = WhisperAloudConfig.from_dict({"audio": {"vad_enabled": True}})
    assert restored.input_gain == InputGainConfig()


@pytest.mark.parametrize(
    "env,value,attr,expected",
    [
        ("WHISPER_ALOUD_INPUT_GAIN_ENABLED", "false", "enabled", False),
        ("WHISPER_ALOUD_INPUT_GAIN_TARGET_DBFS", "-25", "target_dbfs", -25.0),
        ("WHISPER_ALOUD_INPUT_GAIN_MAX_GAIN_DB", "18", "max_gain_db", 18.0),
        (
            "WHISPER_ALOUD_INPUT_GAIN_MAX_OUTPUT_NOISE_DBFS",
            "-55",
            "max_output_noise_dbfs",
            -55.0,
        ),
        ("WHISPER_ALOUD_INPUT_GAIN_HARDWARE_CONTROL", "1", "hardware_control", True),
    ],
)
def test_env_overrides(monkeypatch, env, value, attr, expected):
    monkeypatch.setenv(env, value)
    config = WhisperAloudConfig.load()
    assert getattr(config.input_gain, attr) == expected


@pytest.mark.parametrize(
    "attr,value",
    [
        ("target_dbfs", -3.0),
        ("target_dbfs", -60.0),
        ("max_gain_db", -1.0),
        ("max_gain_db", 61.0),
        ("peak_ceiling_dbfs", 0.5),
        ("peak_ceiling_dbfs", -30.0),
        ("max_output_noise_dbfs", 0.0),
        ("max_gain_change_db_per_s", 0.0),
    ],
)
def test_validation_rejects_out_of_range(attr, value):
    config = WhisperAloudConfig()
    setattr(config.input_gain, attr, value)
    with pytest.raises(ConfigurationError, match="input_gain"):
        config.validate()


def test_target_must_sit_below_peak_ceiling():
    config = WhisperAloudConfig()
    config.input_gain.target_dbfs = -6.0
    config.input_gain.peak_ceiling_dbfs = -6.0
    with pytest.raises(ConfigurationError, match="input_gain"):
        config.validate()


def test_change_detection_reports_input_gain():
    old = WhisperAloudConfig()
    new = old.copy()
    new.input_gain.target_dbfs = -22.0
    changes = detect_config_changes(old, new)
    assert "input_gain" in changes.changed_sections
    assert changes.changed_fields["input_gain"] == {"target_dbfs"}
