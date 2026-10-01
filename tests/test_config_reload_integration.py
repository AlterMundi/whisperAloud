"""Integration tests for config reload flow.

WHISP-45: exercises detect_config_changes()'s full field matrix (not just the
already-covered `engine` field in tests/test_config.py) and the daemon-level
ReloadConfig()/SetConfig() D-Bus methods that apply those changes — in particular
that a MODEL_RELOAD_FIELDS change rebuilds the engine via create_engine(), that an
audio-reinit field rebuilds the recorder, and that fields in neither set are applied
without rebuilding anything.

Uses tests/_daemon_harness.py so the daemon under test never touches a real D-Bus
bus, GTK, wpctl, MPRIS, hotkey portal, or clipboard/paste backend.
"""
from whisper_aloud.config import (
    MODEL_RELOAD_FIELDS,
    TRANSCRIPTION_RELOAD_FIELDS,
    AUDIO_REINIT_FIELDS,
    WhisperAloudConfig,
    detect_config_changes,
)

from tests._daemon_harness import make_daemon


# ─── Pure detect_config_changes() field matrix ─────────────────────────────


def test_model_name_change_requires_reload():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.model.name = "large-v3"
    changes = detect_config_changes(old, new)
    assert "name" in changes.changed_fields["model"]
    assert changes.requires_model_reload is True


def test_model_device_change_requires_reload():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.model.device = "cuda"
    changes = detect_config_changes(old, new)
    assert "device" in changes.changed_fields["model"]
    assert changes.requires_model_reload is True


def test_model_compute_type_change_requires_reload():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.model.compute_type = "float16"
    changes = detect_config_changes(old, new)
    assert "compute_type" in changes.changed_fields["model"]
    assert changes.requires_model_reload is True


def test_transcription_language_is_a_reload_field():
    """language is in TRANSCRIPTION_RELOAD_FIELDS, so changing it also reloads the model."""
    assert "language" in TRANSCRIPTION_RELOAD_FIELDS
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.transcription.language = "fr"
    changes = detect_config_changes(old, new)
    assert "language" in changes.changed_fields["transcription"]
    assert changes.requires_model_reload is True


def test_transcription_beam_size_does_not_require_reload():
    """beam_size/task change the transcription section but are not reload fields."""
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.transcription.beam_size = old.transcription.beam_size + 1
    changes = detect_config_changes(old, new)
    assert "beam_size" in changes.changed_fields["transcription"]
    assert changes.requires_model_reload is False


def test_audio_device_id_requires_reinit_not_model_reload():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.audio.device_id = (old.audio.device_id or 0) + 1
    changes = detect_config_changes(old, new)
    assert "device_id" in changes.changed_fields["audio"]
    assert changes.requires_audio_reinit is True
    assert changes.requires_model_reload is False


def test_audio_normalize_audio_does_not_require_reinit():
    """normalize_audio is tracked but is not in AUDIO_REINIT_FIELDS."""
    assert "normalize_audio" not in AUDIO_REINIT_FIELDS
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.audio.normalize_audio = not old.audio.normalize_audio
    changes = detect_config_changes(old, new)
    assert "normalize_audio" in changes.changed_fields["audio"]
    assert changes.requires_audio_reinit is False


def test_clipboard_and_notification_changes_are_tracked_without_reload():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.clipboard.auto_paste = not old.clipboard.auto_paste
    new.notifications.enabled = not old.notifications.enabled
    changes = detect_config_changes(old, new)
    assert "clipboard" in changes.changed_sections
    assert "notifications" in changes.changed_sections
    assert changes.requires_model_reload is False
    assert changes.requires_audio_reinit is False


def test_no_changes_yields_falsy_changeset():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    changes = detect_config_changes(old, new)
    assert not changes
    assert changes.changed_sections == set()


def test_changes_str_summarizes_reload_and_sections():
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.model.engine = "parakeet"
    changes = detect_config_changes(old, new)
    text = str(changes)
    assert "model reload required" in text
    assert "model" in text


# ─── Daemon-level ReloadConfig()/SetConfig() integration ──────────────────


class TestDaemonReloadConfig:
    def test_reload_config_recreates_engine_on_model_change(self, monkeypatch, tmp_path):
        daemon = make_daemon()
        try:
            # WHISP-74: an idle daemon reloads the engine immediately; a busy
            # one (recording or transcribing) defers it -- see
            # tests/test_daemon_concurrency.py's TestDeferredModelReload for
            # that path. This test is specifically about the idle case.
            daemon.recorder.is_recording = False
            daemon._transcribing = False

            new_config = WhisperAloudConfig()
            new_config.model.name = "large-v3"
            monkeypatch.setattr(WhisperAloudConfig, "load", staticmethod(lambda **kwargs: new_config))

            daemon._daemon_module.create_engine.reset_mock()
            result = daemon.ReloadConfig()

            assert result is True
            daemon._daemon_module.create_engine.assert_called_once_with(new_config)
            new_engine = daemon._daemon_module.create_engine.return_value
            new_engine.load_model.assert_called_once()
            assert daemon.transcriber is new_engine
            assert daemon._model_loaded is True
            assert daemon.config is new_config
            daemon.ConfigChanged.assert_called_once_with({})
        finally:
            daemon._patches.close()

    def test_reload_config_recreates_recorder_on_audio_change(self, monkeypatch):
        daemon = make_daemon()
        try:
            new_config = WhisperAloudConfig()
            new_config.audio.device_id = (new_config.audio.device_id or 0) + 1
            monkeypatch.setattr(WhisperAloudConfig, "load", staticmethod(lambda **kwargs: new_config))

            daemon._daemon_module.AudioRecorder.reset_mock()
            result = daemon.ReloadConfig()

            assert result is True
            # AudioRecorder is a MagicMock class, so every call shares the same
            # `.return_value` instance — identity can't prove a rebuild happened.
            # Call count + the new config being passed through does.
            daemon._daemon_module.AudioRecorder.assert_called_once()
            assert daemon._daemon_module.AudioRecorder.call_args[0][0] is new_config.audio
            assert daemon.recorder is daemon._daemon_module.AudioRecorder.return_value
        finally:
            daemon._patches.close()

    def test_reload_config_skips_engine_and_recorder_when_untouched(self, monkeypatch):
        """A reload with no model/audio changes must not rebuild either component."""
        daemon = make_daemon()
        try:
            unchanged_config = WhisperAloudConfig()
            monkeypatch.setattr(WhisperAloudConfig, "load", staticmethod(lambda **kwargs: unchanged_config))

            old_transcriber = daemon.transcriber
            old_recorder = daemon.recorder
            daemon._daemon_module.create_engine.reset_mock()
            daemon._daemon_module.AudioRecorder.reset_mock()

            result = daemon.ReloadConfig()

            assert result is True
            daemon._daemon_module.create_engine.assert_not_called()
            daemon._daemon_module.AudioRecorder.assert_not_called()
            assert daemon.transcriber is old_transcriber
            assert daemon.recorder is old_recorder
        finally:
            daemon._patches.close()

    def test_reload_config_failure_emits_error_and_returns_false(self, monkeypatch):
        daemon = make_daemon()
        try:
            def _boom(**kwargs):
                raise RuntimeError("config file corrupted")

            monkeypatch.setattr(WhisperAloudConfig, "load", staticmethod(_boom))

            result = daemon.ReloadConfig()

            assert result is False
            daemon.Error.assert_called_once()
            error_code, error_message = daemon.Error.call_args[0]
            assert error_code == "reload_failed"
            assert "config file corrupted" in error_message
        finally:
            daemon._patches.close()

    def test_reload_config_calls_load_with_strict_true(self, monkeypatch):
        """WHISP-71: ReloadConfig() is the one caller that must opt into
        strict=True, so a malformed file raises instead of silently
        reloading defaults -- see WhisperAloudConfig.load()'s docstring."""
        daemon = make_daemon()
        try:
            calls = []

            def _record(**kwargs):
                calls.append(kwargs)
                return WhisperAloudConfig()

            monkeypatch.setattr(WhisperAloudConfig, "load", staticmethod(_record))

            result = daemon.ReloadConfig()

            assert result is True
            assert calls == [{"strict": True}]
        finally:
            daemon._patches.close()

    def test_reload_config_malformed_file_leaves_live_config_untouched(self, monkeypatch):
        """A strict-mode ConfigurationError from a malformed file must not
        mutate daemon.config -- the in-memory config stays exactly what it
        was before the failed reload attempt."""
        daemon = make_daemon()
        try:
            from whisper_aloud.exceptions import ConfigurationError

            old_config = daemon.config

            def _boom(**kwargs):
                raise ConfigurationError("Config file corrupted: Expecting value")

            monkeypatch.setattr(WhisperAloudConfig, "load", staticmethod(_boom))

            result = daemon.ReloadConfig()

            assert result is False
            assert daemon.config is old_config
            daemon.Error.assert_called_once()
            error_code, error_message = daemon.Error.call_args[0]
            assert error_code == "reload_failed"
            assert "corrupted" in error_message
        finally:
            daemon._patches.close()

    def test_set_config_rejects_invalid_values_without_mutating_state(self):
        daemon = make_daemon()
        try:
            old_config = daemon.config
            # engine is validated against a known set — this value must fail validate().
            result = daemon.SetConfig({"model.engine": "not-a-real-engine"})

            assert result is False
            daemon.Error.assert_called_once()
            assert daemon.Error.call_args[0][0] == "config_invalid"
            assert daemon.config is old_config
            daemon.ConfigChanged.assert_not_called()
        finally:
            daemon._patches.close()

    def test_set_config_applies_valid_change_and_saves(self):
        daemon = make_daemon()
        try:
            result = daemon.SetConfig({"clipboard.auto_paste": False})

            assert result is True
            assert daemon.config.clipboard.auto_paste is False
            daemon.ConfigChanged.assert_called_once()
        finally:
            daemon._patches.close()
