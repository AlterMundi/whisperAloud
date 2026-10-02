"""Tests for configuration management."""

import os
from pathlib import Path

import pytest

from whisper_aloud.config import MODEL_RELOAD_FIELDS, WhisperAloudConfig
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
    assert config.transcription.language == "auto"
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


def test_clipboard_config_default_paste_shortcut():
    """paste_shortcut defaults to ctrl+shift+v."""
    from whisper_aloud.config import ClipboardConfig
    cfg = ClipboardConfig()
    assert cfg.paste_shortcut == "ctrl+shift+v"


# ─── WHISP-77: VAD / loudness-normalization settings for quiet speech ──────


def test_default_vad_and_normalize_loudness_settings():
    """Sensible defaults: VAD is more permissive than Silero's own defaults,
    file transcription removes low-frequency rumble before VAD/decoding
    (highpass_hz), and loudness normalization defaults to off (it measured as
    a net negative for VAD retention on a real recording -- see
    stt/normalize.py's docstring)."""
    config = WhisperAloudConfig.load()
    assert config.transcription.normalize_loudness is False
    assert config.transcription.highpass_hz == pytest.approx(80.0)
    assert config.transcription.vad_threshold == pytest.approx(0.2)
    assert config.transcription.vad_min_speech_ms == 100


def test_vad_threshold_and_min_speech_env_overrides():
    os.environ['WHISPER_ALOUD_VAD_SPEECH_THRESHOLD'] = '0.2'
    os.environ['WHISPER_ALOUD_VAD_MIN_SPEECH_MS'] = '50'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.vad_threshold == pytest.approx(0.2)
        assert config.transcription.vad_min_speech_ms == 50
    finally:
        os.environ.pop('WHISPER_ALOUD_VAD_SPEECH_THRESHOLD', None)
        os.environ.pop('WHISPER_ALOUD_VAD_MIN_SPEECH_MS', None)


def test_normalize_loudness_env_override():
    os.environ['WHISPER_ALOUD_TRANSCRIPTION_NORMALIZE'] = 'true'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.normalize_loudness is True
    finally:
        os.environ.pop('WHISPER_ALOUD_TRANSCRIPTION_NORMALIZE', None)


def test_highpass_hz_env_override():
    os.environ['WHISPER_ALOUD_HIGHPASS_HZ'] = '120'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.highpass_hz == pytest.approx(120.0)
    finally:
        os.environ.pop('WHISPER_ALOUD_HIGHPASS_HZ', None)


def test_highpass_hz_zero_disables():
    os.environ['WHISPER_ALOUD_HIGHPASS_HZ'] = '0'
    try:
        config = WhisperAloudConfig.load()
        assert config.transcription.highpass_hz == 0
    finally:
        os.environ.pop('WHISPER_ALOUD_HIGHPASS_HZ', None)


def test_invalid_highpass_hz():
    os.environ['WHISPER_ALOUD_HIGHPASS_HZ'] = '9000'
    try:
        with pytest.raises(ConfigurationError, match="highpass_hz"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_HIGHPASS_HZ', None)


def test_audio_processing_highpass_hz_default():
    """WHISP-83: unlike the file-transcription path's
    TranscriptionConfig.highpass_hz (default 80Hz), the live AudioPipeline's
    high-pass knob defaults to 0 (off) -- opt-in. Measured with
    scripts/measure_live_highpass_vad_retention.py: the causal filter's own
    phase shift showed a small net negative for Silero VAD retention right
    around the project's vad_threshold=0.2 default on a real fixture, and
    the live-dictation path does run Silero VAD by default
    (Transcriber.transcribe_numpy), unlike what the file path's win would
    suggest. See AudioProcessingConfig.highpass_hz's docstring."""
    config = WhisperAloudConfig.load()
    assert config.audio_processing.highpass_hz == pytest.approx(0.0)


def test_audio_processing_highpass_hz_env_override():
    os.environ['WHISPER_ALOUD_AUDIO_HIGHPASS_HZ'] = '120'
    try:
        config = WhisperAloudConfig.load()
        assert config.audio_processing.highpass_hz == pytest.approx(120.0)
    finally:
        os.environ.pop('WHISPER_ALOUD_AUDIO_HIGHPASS_HZ', None)


def test_audio_processing_highpass_hz_zero_disables():
    os.environ['WHISPER_ALOUD_AUDIO_HIGHPASS_HZ'] = '0'
    try:
        config = WhisperAloudConfig.load()
        assert config.audio_processing.highpass_hz == 0
    finally:
        os.environ.pop('WHISPER_ALOUD_AUDIO_HIGHPASS_HZ', None)


def test_invalid_audio_processing_highpass_hz():
    os.environ['WHISPER_ALOUD_AUDIO_HIGHPASS_HZ'] = '9000'
    try:
        with pytest.raises(ConfigurationError, match="highpass_hz"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_AUDIO_HIGHPASS_HZ', None)


def test_audio_processing_highpass_hz_round_trips():
    config = WhisperAloudConfig.load()
    config.audio_processing.highpass_hz = 100.0
    data = config.to_dict()
    assert data["audio_processing"]["highpass_hz"] == pytest.approx(100.0)

    restored = WhisperAloudConfig.from_dict(data)
    assert restored.audio_processing.highpass_hz == pytest.approx(100.0)


def test_invalid_vad_threshold_transcription():
    """validate() rejects an out-of-range transcription.vad_threshold (Silero
    speech probability, distinct from audio.vad_threshold's RMS gate)."""
    os.environ['WHISPER_ALOUD_VAD_SPEECH_THRESHOLD'] = '1.5'
    try:
        with pytest.raises(ConfigurationError, match="vad_threshold"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_VAD_SPEECH_THRESHOLD', None)


def test_invalid_vad_min_speech_ms():
    os.environ['WHISPER_ALOUD_VAD_MIN_SPEECH_MS'] = '-10'
    try:
        with pytest.raises(ConfigurationError, match="vad_min_speech_ms"):
            WhisperAloudConfig.load()
    finally:
        os.environ.pop('WHISPER_ALOUD_VAD_MIN_SPEECH_MS', None)


def test_vad_fields_not_in_model_reload_or_transcription_reload_fields():
    """WHISP-74 deferred-reload semantics: these fields are per-call kwargs to
    transcribe(), not model-construction parameters, so changing them must
    never trigger a model reload."""
    from whisper_aloud.config import MODEL_RELOAD_FIELDS, TRANSCRIPTION_RELOAD_FIELDS
    assert "vad_threshold" not in MODEL_RELOAD_FIELDS
    assert "vad_min_speech_ms" not in MODEL_RELOAD_FIELDS
    assert "normalize_loudness" not in MODEL_RELOAD_FIELDS
    assert "highpass_hz" not in MODEL_RELOAD_FIELDS
    assert "vad_threshold" not in TRANSCRIPTION_RELOAD_FIELDS
    assert "vad_min_speech_ms" not in TRANSCRIPTION_RELOAD_FIELDS
    assert "normalize_loudness" not in TRANSCRIPTION_RELOAD_FIELDS
    assert "highpass_hz" not in TRANSCRIPTION_RELOAD_FIELDS


def test_detect_config_changes_tracks_vad_and_normalize_without_reload():
    from whisper_aloud.config import detect_config_changes
    old = WhisperAloudConfig.load()
    new = WhisperAloudConfig.load()
    new.transcription.vad_threshold = 0.15
    new.transcription.vad_min_speech_ms = 50
    new.transcription.normalize_loudness = not old.transcription.normalize_loudness
    new.transcription.highpass_hz = 120.0

    changes = detect_config_changes(old, new)
    assert "transcription" in changes.changed_sections
    assert changes.changed_fields["transcription"] >= {
        "vad_threshold", "vad_min_speech_ms", "normalize_loudness", "highpass_hz",
    }
    assert changes.requires_model_reload is False


def test_round_trip_vad_and_normalize_fields():
    config = WhisperAloudConfig.load()
    config.transcription.vad_threshold = 0.22
    config.transcription.vad_min_speech_ms = 75
    config.transcription.normalize_loudness = True
    config.transcription.highpass_hz = 100.0
    data = config.to_dict()
    assert data["transcription"]["vad_threshold"] == pytest.approx(0.22)
    assert data["transcription"]["vad_min_speech_ms"] == 75
    assert data["transcription"]["normalize_loudness"] is True
    assert data["transcription"]["highpass_hz"] == pytest.approx(100.0)

    restored = WhisperAloudConfig.from_dict(data)
    assert restored.transcription.vad_threshold == pytest.approx(0.22)
    assert restored.transcription.vad_min_speech_ms == 75
    assert restored.transcription.highpass_hz == pytest.approx(100.0)
    assert restored.transcription.normalize_loudness is True


def test_clipboard_config_paste_shortcut_roundtrip():
    """paste_shortcut survives to_dict / from_dict roundtrip."""
    from whisper_aloud.config import WhisperAloudConfig
    config = WhisperAloudConfig()
    config.clipboard.paste_shortcut = "ctrl+shift+v"
    data = config.to_dict()
    assert data["clipboard"]["paste_shortcut"] == "ctrl+shift+v"
    restored = WhisperAloudConfig.from_dict(data)
    assert restored.clipboard.paste_shortcut == "ctrl+shift+v"


def test_clipboard_paste_to_captured_window_default():
    """paste_to_captured_window defaults to True."""
    config = WhisperAloudConfig()
    assert config.clipboard.paste_to_captured_window is True


def test_clipboard_paste_to_captured_window_roundtrip():
    """paste_to_captured_window survives to_dict/from_dict."""
    config = WhisperAloudConfig()
    config.clipboard.paste_to_captured_window = False
    restored = WhisperAloudConfig.from_dict(config.to_dict())
    assert restored.clipboard.paste_to_captured_window is False


def test_model_engine_defaults_to_faster_whisper():
    """ModelConfig.engine defaults to 'faster-whisper'."""
    config = WhisperAloudConfig()
    assert config.model.engine == "faster-whisper"


def test_engine_is_a_reload_field():
    """Changing the engine must trigger a model reload."""
    assert "engine" in MODEL_RELOAD_FIELDS


def test_engine_serialization_roundtrip():
    """engine survives a to_dict / from_dict roundtrip."""
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    data = config.to_dict()
    assert data["model"]["engine"] == "parakeet"
    restored = WhisperAloudConfig.from_dict(data)
    assert restored.model.engine == "parakeet"


def test_engine_env_override():
    """WHISPER_ALOUD_MODEL_ENGINE is applied by _apply_env_overrides.

    Uses a sentinel value (not the default) to prove the wiring, and calls
    _apply_env_overrides directly so it bypasses validate() — faster-whisper is
    currently the only value that would survive WhisperAloudConfig.load().
    """
    os.environ['WHISPER_ALOUD_MODEL_ENGINE'] = 'sentinel-engine'
    try:
        config = WhisperAloudConfig()
        config._apply_env_overrides()
        assert config.model.engine == "sentinel-engine"
    finally:
        os.environ.pop('WHISPER_ALOUD_MODEL_ENGINE', None)


def test_engine_change_requires_reload():
    """detect_config_changes flags an engine change as reload-requiring."""
    from whisper_aloud.config import detect_config_changes
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.model.engine = "some-other-engine"
    changes = detect_config_changes(old, new)
    assert "engine" in changes.changed_fields["model"]
    assert changes.requires_model_reload is True


def test_valid_engine_passes_validation():
    """The default engine validates cleanly."""
    config = WhisperAloudConfig()
    config.validate()  # must not raise


def test_invalid_engine_rejected():
    """validate() rejects an unknown engine."""
    config = WhisperAloudConfig()
    config.model.engine = "nope-engine"
    with pytest.raises(ConfigurationError, match="Invalid engine"):
        config.validate()


def test_parakeet_model_validates():
    """engine=parakeet with its canonical model passes validation."""
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "parakeet-tdt-0.6b-v3"
    config.validate()  # must not raise


def test_parakeet_rejects_whisper_model():
    """engine=parakeet with a whisper model name is rejected, naming the engine."""
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "base"
    with pytest.raises(ConfigurationError, match="for engine 'parakeet'"):
        config.validate()


def test_faster_whisper_rejects_parakeet_model():
    """engine=faster-whisper with a parakeet model name is rejected."""
    config = WhisperAloudConfig()
    config.model.engine = "faster-whisper"
    config.model.name = "parakeet-tdt-0.6b-v3"
    with pytest.raises(ConfigurationError, match="for engine 'faster-whisper'"):
        config.validate()


# =============================================================================
# WHISP-71: load(strict=...) on a malformed config file
# =============================================================================


def _write_malformed_config():
    """Write an unparseable config.json under the isolated HOME and return
    its path."""
    config_dir = os.path.join(os.environ["HOME"], ".config", "whisper_aloud")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.json")
    with open(config_path, "w") as f:
        f.write("{ not valid json")
    return config_path


def test_load_default_falls_back_to_defaults_on_malformed_file():
    """load() (strict=False, the default) must keep today's lenient
    startup behavior: log and fall back to defaults rather than raising.
    This is the daemon-startup path -- see WhisperAloudService.__init__,
    which calls WhisperAloudConfig.load() with no arguments."""
    _write_malformed_config()
    config = WhisperAloudConfig.load()
    assert config.model.name == "base"  # default, not read from the broken file


def test_load_default_does_not_overwrite_the_malformed_file():
    """Falling back to defaults must never silently replace the user's
    (malformed) file on disk -- only an explicit save() should write it."""
    config_path = _write_malformed_config()
    original_bytes = open(config_path, "rb").read()
    WhisperAloudConfig.load()
    assert open(config_path, "rb").read() == original_bytes


def test_load_strict_raises_configuration_error_on_malformed_file():
    """load(strict=True) -- the ReloadConfig() path -- must raise instead
    of silently falling back, so the caller can tell the difference
    between 'reloaded' and 'kept the old config because the file was
    broken'."""
    _write_malformed_config()
    with pytest.raises(ConfigurationError):
        WhisperAloudConfig.load(strict=True)


def test_load_strict_raises_on_file_read_error_too():
    """Not just JSON decode errors -- any failure to read/parse the file
    should raise in strict mode (the non-strict path already treats both
    the same way, via two separate except clauses that both log-and-
    fall-back)."""
    config_dir = os.path.join(os.environ["HOME"], ".config", "whisper_aloud")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.json")
    os.mkdir(os.path.join(config_dir, "config.json.d"))  # irrelevant, just ensure dir exists
    # A directory where a file is expected reliably raises on open() (IsADirectoryError).
    os.rmdir(os.path.join(config_dir, "config.json.d"))
    os.mkdir(config_path)
    try:
        with pytest.raises(ConfigurationError):
            WhisperAloudConfig.load(strict=True)
    finally:
        os.rmdir(config_path)


def test_load_strict_succeeds_normally_on_a_valid_file():
    """strict=True must not change behavior for a well-formed file."""
    config = WhisperAloudConfig()
    config.transcription.language = "fr"
    config.save()
    loaded = WhisperAloudConfig.load(strict=True)
    assert loaded.transcription.language == "fr"


def test_load_strict_with_no_file_still_uses_defaults():
    """strict=True with no config file at all (the common first-run case)
    must not raise -- there is nothing malformed to report."""
    config = WhisperAloudConfig.load(strict=True)
    assert config.model.name == "base"


def test_load_never_writes_the_config_file():
    """WhisperAloudConfig.load() must be read-only: a real config.json's
    mtime and contents must be unchanged after load(), whether or not the
    file already exists.

    This guards against the kind of regression found during WHISP-77: not in
    load() itself (which was and remains read-only -- confirmed by this
    test), but in test infrastructure (tests/_daemon_harness.py-based tests
    calling the daemon's SetConfig, which unconditionally calls
    self.config.save()) that could write a real user's config.json if HOME
    were ever left unisolated. See tests/conftest.py's autouse
    `_isolated_home` fixture, which is what actually prevents that now.
    """
    config_path = Path(os.environ["HOME"]) / ".config" / "whisper_aloud" / "config.json"

    # Case 1: no file yet -- load() with defaults must not create one.
    assert not config_path.exists()
    WhisperAloudConfig.load()
    assert not config_path.exists()

    # Case 2: a file already exists -- load() must not touch its mtime/content.
    config = WhisperAloudConfig()
    config.transcription.language = "fr"
    config.save()
    assert config_path.exists()
    mtime_before = config_path.stat().st_mtime_ns
    content_before = config_path.read_text()

    for _ in range(3):
        WhisperAloudConfig.load()

    assert config_path.stat().st_mtime_ns == mtime_before
    assert config_path.read_text() == content_before
