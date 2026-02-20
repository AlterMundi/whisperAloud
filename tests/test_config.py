"""Tests for configuration management."""

import os

import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import ConfigurationError


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Isolate tests from real config by using a temp HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))


def test_default_config():
    """Test default configuration values."""
    config = WhisperAloudConfig.load()
    assert config.model.name == "base"
    assert config.model.device == "auto"
    assert config.model.compute_type == "int8"
    assert config.model.download_root is None
    assert config.transcription.language == "es"
    assert config.transcription.beam_size == 5
    assert config.transcription.vad_filter is True
    assert config.transcription.task == "transcribe"
    assert config.notifications.enabled is True
    assert config.notifications.recording_started is True
    assert config.notifications.recording_stopped is True
    assert config.notifications.transcription_completed is True
    assert config.notifications.error is True


def test_env_override():
    """Test environment variable overrides."""
    # Set environment variables
    os.environ['WHISPER_ALOUD_MODEL_NAME'] = 'medium'
    os.environ['WHISPER_ALOUD_MODEL_DEVICE'] = 'cpu'
    os.environ['WHISPER_ALOUD_LANGUAGE'] = 'en'
    os.environ['WHISPER_ALOUD_BEAM_SIZE'] = '3'
    os.environ['WHISPER_ALOUD_VAD_FILTER'] = 'false'

    try:
        config = WhisperAloudConfig.load()
        assert config.model.name == "medium"
        assert config.model.device == "cpu"
        assert config.transcription.language == "en"
        assert config.transcription.beam_size == 3
        assert config.transcription.vad_filter is False
    finally:
        # Clean up environment
        for key in ['WHISPER_ALOUD_MODEL_NAME', 'WHISPER_ALOUD_MODEL_DEVICE',
                   'WHISPER_ALOUD_LANGUAGE', 'WHISPER_ALOUD_BEAM_SIZE',
                   'WHISPER_ALOUD_VAD_FILTER']:
            os.environ.pop(key, None)


def test_invalid_model_name():
    """Test validation catches invalid model name."""
    os.environ['WHISPER_ALOUD_MODEL_NAME'] = 'invalid-model'
    try:
        with pytest.raises(ConfigurationError, match="Invalid model name"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_MODEL_NAME', None)


def test_invalid_device():
    """Test validation catches invalid device."""
    os.environ['WHISPER_ALOUD_MODEL_DEVICE'] = 'invalid-device'
    try:
        with pytest.raises(ConfigurationError, match="Invalid device"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_MODEL_DEVICE', None)


def test_invalid_compute_type():
    """Test validation catches invalid compute type."""
    os.environ['WHISPER_ALOUD_MODEL_COMPUTE_TYPE'] = 'invalid-type'
    try:
        with pytest.raises(ConfigurationError, match="Invalid compute type"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_MODEL_COMPUTE_TYPE', None)


def test_invalid_beam_size():
    """Test validation catches invalid beam size."""
    os.environ['WHISPER_ALOUD_BEAM_SIZE'] = '15'
    try:
        with pytest.raises(ConfigurationError, match="Invalid beam size"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_BEAM_SIZE', None)


def test_invalid_task():
    """Test validation catches invalid task."""
    os.environ['WHISPER_ALOUD_TASK'] = 'invalid-task'
    try:
        with pytest.raises(ConfigurationError, match="Invalid task"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_TASK', None)


def test_language_validation_edge_cases():
    """Test language validation edge cases - invalid languages are sanitized to default."""
    # Test empty language - sanitized to default 'es'
    os.environ['WHISPER_ALOUD_LANGUAGE'] = ''
    try:
        config = WhisperAloudConfig.load()
        # Empty string is treated as invalid and defaults to 'es'
        assert config.transcription.language == 'es'
    finally:
        os.environ.pop('WHISPER_ALOUD_LANGUAGE', None)

    # Test single character language - should be sanitized to default
    os.environ['WHISPER_ALOUD_LANGUAGE'] = 'a'
    try:
        config = WhisperAloudConfig.load()
        # Invalid language codes are sanitized to 'es' (default)
        assert config.transcription.language == 'es'
    finally:
        os.environ.pop('WHISPER_ALOUD_LANGUAGE', None)


def test_language_auto_is_preserved():
    """'auto' language mode should be accepted and persisted."""
    os.environ['WHISPER_ALOUD_LANGUAGE'] = 'auto'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.language == "auto"
        config.save()
        loaded = WhisperAloudConfig.load()
        assert loaded.transcription.language == "auto"
    finally:
        os.environ.pop('WHISPER_ALOUD_LANGUAGE', None)


def test_validate_rejects_invalid_transcription_language():
    """validate() should reject unsupported language tokens."""
    config = WhisperAloudConfig.load()
    config.transcription.language = "english"
    with pytest.raises(ConfigurationError, match="Invalid language"):
        config.validate()


def test_beam_size_bounds():
    """Test beam size validation at boundaries."""
    # Test minimum valid beam size
    os.environ['WHISPER_ALOUD_BEAM_SIZE'] = '1'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.beam_size == 1
    finally:
        os.environ.pop('WHISPER_ALOUD_BEAM_SIZE', None)

    # Test maximum valid beam size
    os.environ['WHISPER_ALOUD_BEAM_SIZE'] = '10'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.beam_size == 10
    finally:
        os.environ.pop('WHISPER_ALOUD_BEAM_SIZE', None)


def test_env_var_type_conversion():
    """Test environment variable type conversion."""
    # Test boolean conversion
    os.environ['WHISPER_ALOUD_VAD_FILTER'] = 'false'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.vad_filter is False
    finally:
        os.environ.pop('WHISPER_ALOUD_VAD_FILTER', None)

    # Test integer conversion
    os.environ['WHISPER_ALOUD_BEAM_SIZE'] = '7'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.beam_size == 7
    finally:
        os.environ.pop('WHISPER_ALOUD_BEAM_SIZE', None)


def test_notifications_env_override():
    """Test notification-related environment variable overrides."""
    os.environ['WHISPER_ALOUD_NOTIFICATIONS_ENABLED'] = 'false'
    os.environ['WHISPER_ALOUD_NOTIFICATIONS_RECORDING_STARTED'] = 'false'
    os.environ['WHISPER_ALOUD_NOTIFICATIONS_RECORDING_STOPPED'] = 'false'
    os.environ['WHISPER_ALOUD_NOTIFICATIONS_TRANSCRIPTION_COMPLETED'] = 'false'
    os.environ['WHISPER_ALOUD_NOTIFICATIONS_ERROR'] = 'false'
    try:
        config = WhisperAloudConfig.load()
        assert config.notifications.enabled is False
        assert config.notifications.recording_started is False
        assert config.notifications.recording_stopped is False
        assert config.notifications.transcription_completed is False
        assert config.notifications.error is False
    finally:
        os.environ.pop('WHISPER_ALOUD_NOTIFICATIONS_ENABLED', None)
        os.environ.pop('WHISPER_ALOUD_NOTIFICATIONS_RECORDING_STARTED', None)
        os.environ.pop('WHISPER_ALOUD_NOTIFICATIONS_RECORDING_STOPPED', None)
        os.environ.pop('WHISPER_ALOUD_NOTIFICATIONS_TRANSCRIPTION_COMPLETED', None)
        os.environ.pop('WHISPER_ALOUD_NOTIFICATIONS_ERROR', None)


def test_config_immutability():
    """Test that loaded config is properly isolated."""
    config1 = WhisperAloudConfig.load()
    config2 = WhisperAloudConfig.load()

    # Modify one config
    config1.model.name = "small"

    # Other config should be unchanged
    assert config2.model.name == "base"
