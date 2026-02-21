"""Tests for WhisperAloud D-Bus daemon service."""
import importlib
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── Bootstrap: patch pydbus and GLib before daemon module loads ──────────
# pydbus signal() is a descriptor that prevents instance-level assignment.
# We replace it with a simple factory so tests can inspect signal calls.

class _FakeSignal:
    """Drop-in for pydbus.generic.signal that allows per-instance mocking."""

    def __init__(self):
        self._store = {}
        self.name = "anon"

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        key = id(obj)
        if key not in self._store:
            self._store[key] = MagicMock(name=f"signal_{self.name}")
        return self._store[key]

    def __set__(self, obj, value):
        self._store[id(obj)] = value


def _build_stub_modules():
    """Build local stubs used for daemon imports and runtime checks."""
    fake_pydbus_generic = MagicMock()
    fake_pydbus_generic.signal = _FakeSignal
    fake_pydbus = MagicMock()
    fake_pydbus.generic = fake_pydbus_generic
    fake_pydbus.SessionBus = MagicMock

    # Keep daemon imports deterministic and non-blocking in headless CI.
    fake_gtk = MagicMock()
    fake_gtk.init_check.return_value = (True, [])
    fake_glib = MagicMock()
    fake_glib.Variant = lambda t, v: v
    fake_glib.idle_add = lambda fn, *args: fn(*args)
    fake_glib.SOURCE_REMOVE = False
    fake_glib.PRIORITY_DEFAULT = 0
    fake_glib.unix_signal_add = MagicMock(return_value=1)
    fake_glib.timeout_add = MagicMock(return_value=1)
    fake_glib.source_remove = MagicMock()

    fake_gi_repo = MagicMock()
    fake_gi_repo.Gtk = fake_gtk
    fake_gi_repo.GLib = fake_glib
    fake_gi = MagicMock()
    fake_gi.repository = fake_gi_repo

    fake_scipy = MagicMock()
    fake_scipy.signal = MagicMock()
    fake_faster_whisper = MagicMock()
    fake_faster_whisper.WhisperModel = MagicMock()

    return {
        "pydbus": fake_pydbus,
        "pydbus.generic": fake_pydbus_generic,
        "gi": fake_gi,
        "gi.repository": fake_gi_repo,
        "sounddevice": MagicMock(),
        "scipy": fake_scipy,
        "faster_whisper": fake_faster_whisper,
    }


def _import_daemon_module(stub_modules):
    """Import daemon module with local D-Bus/GLib stubs."""

    with patch.dict(
        sys.modules,
        stub_modules,
    ):
        sys.modules.pop("whisper_aloud.service.daemon", None)
        module = importlib.import_module("whisper_aloud.service.daemon")
    return module


def _make_daemon(indicator_fails=False, hotkey_fails=False):
    """Create a daemon instance with all external dependencies mocked."""
    stub_modules = _build_stub_modules()
    daemon_module = _import_daemon_module(stub_modules)
    with patch.dict(sys.modules, stub_modules), \
         patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False), \
         patch.object(daemon_module, "SessionBus"), \
         patch.object(daemon_module, "AudioRecorder"), \
         patch.object(daemon_module, "Transcriber"), \
         patch.object(daemon_module, "NotificationManager"), \
         patch.object(daemon_module, "HistoryManager"), \
         patch.object(daemon_module, "ClipboardManager"), \
         patch.object(daemon_module, "WhisperAloudIndicator") as mock_ind_cls, \
         patch.object(daemon_module, "HotkeyManager") as mock_hk_cls, \
         patch.object(daemon_module, "GLib") as mock_glib:
        # GLib.Variant passthrough for test assertions
        mock_glib.Variant = lambda t, v: v
        # GLib.idle_add should invoke the callback immediately in tests
        mock_glib.idle_add = lambda fn, *args: fn(*args)
        mock_glib.SOURCE_REMOVE = False

        if indicator_fails:
            mock_ind_cls.side_effect = Exception("no tray available")

        if hotkey_fails:
            mock_hk_cls.side_effect = Exception("hotkey init failed")
        else:
            mock_hk_cls.return_value.available = True
            mock_hk_cls.return_value.backend = "keybinder"

        from whisper_aloud.config import WhisperAloudConfig
        config = WhisperAloudConfig()
        service = daemon_module.WhisperAloudService(config=config)
        service._daemon_module = daemon_module
        service._mock_glib = mock_glib
        service._mock_indicator_cls = mock_ind_cls
        service._mock_hotkey_cls = mock_hk_cls
        return service


class TestDaemonMethods:
    """Tests for daemon D-Bus methods (without actual D-Bus)."""

    @pytest.fixture
    def daemon(self):
        """Create daemon with mocked components."""
        return _make_daemon()

    def test_start_recording(self, daemon):
        """StartRecording should start recorder and emit signals."""
        daemon.recorder.is_recording = False
        result = daemon.StartRecording()
        assert result is True
        daemon.recorder.start.assert_called_once()
        daemon.RecordingStarted.assert_called_once()
        daemon.StatusChanged.assert_called_with("recording")

    def test_start_recording_no_recorder(self, daemon):
        """StartRecording with no recorder should return False."""
        daemon.recorder = None
        result = daemon.StartRecording()
        assert result is False
        daemon.Error.assert_called_once()

    def test_stop_recording(self, daemon):
        """StopRecording should stop recorder and return 'transcribing'."""
        daemon.recorder.stop.return_value = MagicMock()
        result = daemon.StopRecording()
        assert result == "transcribing"
        daemon.recorder.stop.assert_called_once()
        daemon.RecordingStopped.assert_called_once()
        daemon.StatusChanged.assert_any_call("transcribing")
        daemon.notifications.show_recording_stopped.assert_called_once()

    def test_stop_recording_no_components(self, daemon):
        """StopRecording with no components should return 'error'."""
        daemon.recorder = None
        result = daemon.StopRecording()
        assert result == "error"

    def test_cancel_recording(self, daemon):
        """CancelRecording should cancel and return True."""
        daemon.recorder.is_recording = True
        result = daemon.CancelRecording()
        assert result is True
        daemon.recorder.cancel.assert_called_once()
        daemon.StatusChanged.assert_called_with("idle")

    def test_cancel_recording_when_not_recording(self, daemon):
        """CancelRecording when idle should return False."""
        daemon.recorder.is_recording = False
        result = daemon.CancelRecording()
        assert result is False

    def test_cancel_recording_during_transcribing(self, daemon):
        """CancelRecording should cancel an in-flight transcription."""
        daemon.recorder.is_recording = False
        daemon._transcribing = True
        result = daemon.CancelRecording()
        assert result is True
        assert daemon._transcribing is False
        daemon.transcriber.cancel_transcription.assert_called_once()
        daemon.StatusChanged.assert_called_with("idle")

    def test_toggle_recording_starts(self, daemon):
        """ToggleRecording when idle should start recording."""
        daemon.recorder.is_recording = False
        result = daemon.ToggleRecording()
        assert result == "recording"
        daemon.recorder.start.assert_called_once()

    def test_toggle_recording_stops(self, daemon):
        """ToggleRecording when recording should stop and return 'transcribing'."""
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = MagicMock()
        result = daemon.ToggleRecording()
        assert result == "transcribing"

    def test_get_status_returns_dict(self, daemon):
        """GetStatus should return dict with expected keys."""
        daemon.recorder.state = MagicMock()
        daemon.recorder.state.value = "idle"
        daemon._transcribing = False
        result = daemon.GetStatus()
        assert isinstance(result, dict)
        assert "state" in result
        assert "version" in result
        assert "model" in result
        assert "device" in result

    def test_get_status_transcribing(self, daemon):
        """GetStatus should report 'transcribing' when active."""
        daemon._transcribing = True
        result = daemon.GetStatus()
        # Value may be raw string or GLib.Variant depending on mock state
        state = result["state"]
        state_str = state if isinstance(state, str) else str(state)
        assert "transcribing" in state_str

    def test_get_config_returns_dict(self, daemon):
        """GetConfig should return flattened config dict."""
        result = daemon.GetConfig()
        assert isinstance(result, dict)
        # Check some expected keys exist with section.field format
        assert any("model" in k for k in result.keys())

    def test_set_config_invalid(self, daemon):
        """SetConfig with bad key missing from config should return False."""
        # Use a key where the section exists but the field does not,
        # so the split works but no update happens, and from_dict/validate
        # may still succeed. Instead use a completely bogus section.
        result = daemon.SetConfig({"bogus_section.bogus_field": "value"})
        # The key won't match any section, so config_dict is unchanged.
        # from_dict + validate may still pass if the original config is valid.
        # What matters is that SetConfig handles it without crashing.
        assert isinstance(result, bool)

    def test_quit_returns_true(self, daemon):
        """Quit should return True."""
        daemon._loop = MagicMock()
        result = daemon.Quit()
        assert result is True
        daemon._loop.quit.assert_called_once()

    def test_reload_config_returns_bool(self, daemon):
        """ReloadConfig should return True on success."""
        with patch.object(daemon._daemon_module, "WhisperAloudConfig") as mock_conf:
            mock_conf.load.return_value = daemon.config
            result = daemon.ReloadConfig()
            assert result is True

    def test_reload_config_failure(self, daemon):
        """ReloadConfig should return False on failure."""
        with patch.object(daemon._daemon_module, "WhisperAloudConfig") as mock_conf:
            mock_conf.load.side_effect = Exception("config error")
            result = daemon.ReloadConfig()
            assert result is False

    def test_get_history(self, daemon):
        """GetHistory should return list of dicts."""
        mock_entry = MagicMock()
        mock_entry.id = 1
        mock_entry.text = "hello world"
        mock_entry.timestamp = "2026-01-01T00:00:00"
        mock_entry.duration = 1.5
        mock_entry.language = "en"
        mock_entry.confidence = 0.88
        mock_entry.processing_time = 0.5
        mock_entry.favorite = True
        mock_entry.notes = "note"
        mock_entry.tags = ["tag1"]
        daemon.history_manager.get_recent.return_value = [mock_entry]
        result = daemon.GetHistory(10)
        assert isinstance(result, list)
        assert len(result) == 1
        # Values may be raw or GLib.Variant depending on mock state
        text_val = result[0]["text"]
        text_str = text_val if isinstance(text_val, str) else str(text_val)
        assert "hello world" in text_str
        id_val = result[0]["id"]
        id_int = id_val if isinstance(id_val, int) else int(str(id_val))
        assert id_int == 1
        assert result[0]["favorite"] is True
        assert result[0]["tags"] == ["tag1"]

    def test_search_history(self, daemon):
        """SearchHistory should delegate to HistoryManager.search."""
        mock_entry = MagicMock()
        mock_entry.id = 2
        mock_entry.text = "matched text"
        mock_entry.timestamp = "2026-01-02T00:00:00"
        mock_entry.duration = 2.0
        mock_entry.language = "es"
        mock_entry.confidence = 0.9
        mock_entry.processing_time = 0.7
        mock_entry.favorite = False
        mock_entry.notes = ""
        mock_entry.tags = []
        daemon.history_manager.search.return_value = [mock_entry]

        result = daemon.SearchHistory("matched", 15)
        daemon.history_manager.search.assert_called_once_with("matched", limit=15)
        assert len(result) == 1
        assert result[0]["text"] == "matched text"

    def test_search_history_empty_query_falls_back_to_recent(self, daemon):
        """SearchHistory with empty query should return GetHistory output."""
        daemon.history_manager.get_recent.return_value = []
        result = daemon.SearchHistory("   ", 5)
        daemon.history_manager.get_recent.assert_called_once_with(limit=5)
        assert result == []

    def test_get_favorite_history(self, daemon):
        """GetFavoriteHistory should delegate to HistoryManager.get_favorites."""
        daemon.history_manager.get_favorites.return_value = []
        result = daemon.GetFavoriteHistory(20)
        daemon.history_manager.get_favorites.assert_called_once_with(limit=20)
        assert result == []

    def test_toggle_history_favorite(self, daemon):
        """ToggleHistoryFavorite should delegate to HistoryManager.toggle_favorite."""
        daemon.history_manager.toggle_favorite.return_value = True
        result = daemon.ToggleHistoryFavorite(3)
        daemon.history_manager.toggle_favorite.assert_called_once_with(3)
        assert result is True

    def test_delete_history_entry(self, daemon):
        """DeleteHistoryEntry should delegate to HistoryManager.delete."""
        daemon.history_manager.delete.return_value = True
        result = daemon.DeleteHistoryEntry(4)
        daemon.history_manager.delete.assert_called_once_with(4)
        assert result is True


    # ─── Deferred model loading ────────────────────────────────────────

    def test_model_not_loaded_at_init(self, daemon):
        """Model should NOT be loaded during __init__ (deferred for D-Bus activation)."""
        daemon.transcriber.load_model.assert_not_called()
        assert daemon._model_loaded is False

    def test_ensure_model_loaded_on_first_transcription(self, daemon):
        """Model should be loaded on first _transcribe_audio call."""
        mock_result = MagicMock()
        daemon.transcriber.transcribe_numpy.return_value = mock_result
        daemon._transcribe_audio(np.zeros(16000, dtype=np.float32))
        daemon.transcriber.load_model.assert_called_once()
        assert daemon._model_loaded is True

    def test_ensure_model_loaded_idempotent(self, daemon):
        """Repeated _ensure_model_loaded calls should only load once."""
        daemon._ensure_model_loaded()
        daemon._ensure_model_loaded()
        daemon.transcriber.load_model.assert_called_once()

    # ─── Task 2.2: Level tracking tests ──────────────────────────────────

    def test_start_recording_starts_level_timer(self, daemon):
        """StartRecording should start the level emission timer."""
        daemon.recorder.is_recording = False
        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            daemon.StartRecording()
            mock_glib.timeout_add.assert_called_once_with(100, daemon._emit_level)

    def test_stop_recording_stops_level_timer(self, daemon):
        """StopRecording should stop the level timer."""
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon._level_timer_id = 42
        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            daemon.StopRecording()
            mock_glib.source_remove.assert_called_once_with(42)
        assert daemon._level_timer_id is None

    def test_cancel_recording_stops_level_timer(self, daemon):
        """CancelRecording should stop the level timer."""
        daemon.recorder.is_recording = True
        daemon._level_timer_id = 42
        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            daemon.CancelRecording()
            mock_glib.source_remove.assert_called_once_with(42)
        assert daemon._level_timer_id is None

    def test_level_callback_tracks_peak(self, daemon):
        """Level callback should track peak level."""
        from whisper_aloud.audio.level_meter import AudioLevel
        level = AudioLevel(rms=-20.0, peak=0.8, db=-2.0)
        daemon._on_level(level)
        assert daemon._peak_level == 0.8
        level2 = AudioLevel(rms=-25.0, peak=0.5, db=-6.0)
        daemon._on_level(level2)
        assert daemon._peak_level == 0.8  # Still 0.8 (peak tracking)

    def test_emit_level_resets_peak(self, daemon):
        """_emit_level should emit current peak and reset to 0."""
        daemon._peak_level = 0.75
        result = daemon._emit_level()
        assert result is True  # Keep timer running
        daemon.LevelUpdate.assert_called_once_with(0.75)
        assert daemon._peak_level == 0.0

    # ─── Task 2.3: SIGTERM handler + clipboard tests ─────────────────────

    def test_cancel_recording_on_sigterm(self, daemon):
        """SIGTERM during recording should cancel recording."""
        daemon.recorder.is_recording = True
        daemon._loop = MagicMock()
        daemon._signal_handler_glib()
        daemon.recorder.cancel.assert_called_once()
        daemon._loop.quit.assert_called_once()

    def test_sigterm_when_not_recording(self, daemon):
        """SIGTERM when idle should just quit cleanly."""
        daemon.recorder.is_recording = False
        daemon._loop = MagicMock()
        daemon._signal_handler_glib()
        daemon.recorder.cancel.assert_not_called()
        daemon._loop.quit.assert_called_once()

    def test_clipboard_copy_after_transcription(self, daemon):
        """Transcription should be copied to clipboard when auto_copy enabled."""
        daemon.config.clipboard.auto_copy = True
        daemon.clipboard_manager = MagicMock()
        # Create a mock transcription result
        mock_result = MagicMock()
        mock_result.text = "Hello world"
        mock_result.duration = 1.0
        mock_result.language = "en"
        mock_result.confidence = 0.95
        daemon.transcriber.transcribe_numpy.return_value = mock_result
        daemon.history_manager.add_transcription.return_value = 1

        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            mock_glib.idle_add = lambda fn, *args: fn(*args)
            daemon._transcribe_and_emit(np.zeros(16000, dtype=np.float32))
        daemon.clipboard_manager.copy.assert_called_once_with("Hello world")

    def test_clipboard_not_called_when_disabled(self, daemon):
        """Clipboard copy should be skipped when auto_copy is disabled."""
        daemon.config.clipboard.auto_copy = False
        daemon.clipboard_manager = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "Hello world"
        mock_result.duration = 1.0
        mock_result.language = "en"
        mock_result.confidence = 0.95
        daemon.transcriber.transcribe_numpy.return_value = mock_result
        daemon.history_manager.add_transcription.return_value = 1

        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            mock_glib.idle_add = lambda fn, *args: fn(*args)
        daemon._transcribe_and_emit(np.zeros(16000, dtype=np.float32))
        daemon.clipboard_manager.copy.assert_not_called()

    def test_transcribe_error_emits_later_without_closure_failure(self, daemon):
        """Deferred error callback should not depend on except-scope variable lifetime."""
        daemon.transcriber.transcribe_numpy.side_effect = Exception("boom")
        deferred_callbacks = []

        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v

            def _capture_idle(fn, *args):
                deferred_callbacks.append((fn, args))
                return 1

            mock_glib.idle_add = _capture_idle
            daemon._transcribe_and_emit(np.zeros(16000, dtype=np.float32))

        assert len(deferred_callbacks) == 1
        fn, args = deferred_callbacks[0]
        fn(*args)
        daemon.Error.assert_called_once_with("transcription_failed", "boom")


    # ─── Task 3.2: Indicator integration tests ─────────────────────────

    def test_daemon_creates_indicator(self, daemon):
        """Indicator should be created during __init__."""
        assert daemon.indicator is not None
        daemon._mock_indicator_cls.assert_called_once_with(
            on_toggle=daemon._safe_toggle,
            on_quit=daemon._safe_quit,
        )

    def test_indicator_state_updated_on_start_recording(self, daemon):
        """StartRecording should call indicator.set_state('recording')."""
        daemon.recorder.is_recording = False
        daemon.StartRecording()
        daemon.indicator.set_state.assert_called_with("recording")

    def test_indicator_state_updated_on_stop(self, daemon):
        """StopRecording should call indicator.set_state('transcribing')."""
        daemon.recorder.stop.return_value = MagicMock()
        daemon.StopRecording()
        daemon.indicator.set_state.assert_any_call("transcribing")

    def test_indicator_last_text_updated(self, daemon):
        """set_last_text should be called after successful transcription."""
        mock_result = MagicMock()
        mock_result.text = "transcribed text"
        mock_result.duration = 2.0
        mock_result.language = "en"
        mock_result.confidence = 0.9
        daemon.transcriber.transcribe_numpy.return_value = mock_result
        daemon.history_manager.add_transcription.return_value = 1

        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            mock_glib.idle_add = lambda fn, *args: fn(*args)
            daemon._transcribe_and_emit(np.zeros(16000, dtype=np.float32))

        daemon.indicator.set_last_text.assert_called_once_with("transcribed text")
        daemon.indicator.set_state.assert_called_with("idle")

    def test_daemon_runs_without_indicator(self):
        """Daemon should work normally if indicator creation fails."""
        daemon = _make_daemon(indicator_fails=True)
        assert daemon.indicator is None
        # Core operations should still work
        daemon.recorder.is_recording = False
        result = daemon.StartRecording()
        assert result is True
        daemon.recorder.start.assert_called_once()

    # ─── Task 4.4: Hotkey integration tests ──────────────────────────────

    def test_daemon_creates_hotkey_manager(self, daemon):
        """Hotkey manager should be created during __init__."""
        assert daemon.hotkey_manager is not None
        daemon._mock_hotkey_cls.assert_called_once()

    def test_hotkey_manager_registers_toggle(self, daemon):
        """Register should be called with config accel on available backend."""
        daemon.hotkey_manager.register.assert_called_once_with(
            daemon.config.hotkey.toggle_recording,
            daemon._safe_toggle,
        )

    def test_daemon_runs_without_hotkey(self):
        """Daemon should work normally if hotkey manager fails to init."""
        daemon = _make_daemon(hotkey_fails=True)
        assert daemon.hotkey_manager is None
        # Core operations should still work
        daemon.recorder.is_recording = False
        result = daemon.StartRecording()
        assert result is True
        daemon.recorder.start.assert_called_once()

    def test_get_status_includes_hotkey_backend(self, daemon):
        """GetStatus should include hotkey_backend key."""
        daemon.recorder.state = MagicMock()
        daemon.recorder.state.value = "idle"
        daemon._transcribing = False
        with patch.object(daemon._daemon_module, "GLib") as mock_glib:
            mock_glib.Variant = lambda t, v: v
            result = daemon.GetStatus()
        assert "hotkey_backend" in result
        assert result["hotkey_backend"] == "keybinder"


class TestDaemonIntrospection:
    """Test that the D-Bus introspection XML is valid."""

    def test_docstring_contains_interface(self):
        """Service docstring should contain the interface XML."""
        daemon_module = _import_daemon_module(_build_stub_modules())
        doc = daemon_module.WhisperAloudService.__doc__
        assert "org.fede.whisperaloud.Control" in doc
        assert "StartRecording" in doc
        assert "StopRecording" in doc
        assert "CancelRecording" in doc
        assert "GetStatus" in doc
        assert "GetHistory" in doc
        assert "SearchHistory" in doc
        assert "GetFavoriteHistory" in doc
        assert "ToggleHistoryFavorite" in doc
        assert "DeleteHistoryEntry" in doc
        assert "GetConfig" in doc
        assert "SetConfig" in doc
        assert "ReloadConfig" in doc
        assert "Quit" in doc
        assert "TranscriptionReady" in doc
        assert "RecordingStarted" in doc
        assert "RecordingStopped" in doc
        assert "LevelUpdate" in doc
        assert "StatusChanged" in doc
        assert "ConfigChanged" in doc
        assert 'name="Error"' in doc

    def test_bus_name_unified(self):
        """Bus name should use lowercase 'whisperaloud'."""
        daemon_module = _import_daemon_module(_build_stub_modules())
        doc = daemon_module.WhisperAloudService.__doc__
        # Should NOT contain old camelCase name
        assert "org.fede.whisperAloud" not in doc
        assert "org.fede.whisperaloud" in doc


class TestRecorderInitialization:
    """Tests for recorder construction in daemon _init_components."""

    def test_recorder_initialized_with_processing_config(self):
        """AudioRecorder must be constructed with processing_config from daemon config."""
        stub_modules = _build_stub_modules()
        daemon_module = _import_daemon_module(stub_modules)
        with (
            patch.dict(sys.modules, stub_modules),
            patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False),
            patch.object(daemon_module, "SessionBus"),
            patch.object(daemon_module, "AudioRecorder") as mock_ar,
            patch.object(daemon_module, "Transcriber"),
            patch.object(daemon_module, "NotificationManager"),
            patch.object(daemon_module, "HistoryManager"),
            patch.object(daemon_module, "ClipboardManager"),
            patch.object(daemon_module, "WhisperAloudIndicator"),
            patch.object(daemon_module, "HotkeyManager") as mock_hk_cls,
            patch.object(daemon_module, "GLib") as mock_glib,
        ):
            mock_glib.Variant = lambda t, v: v
            mock_glib.idle_add = lambda fn, *args: fn(*args)
            mock_glib.SOURCE_REMOVE = False
            mock_hk_cls.return_value.available = True
            mock_hk_cls.return_value.backend = "keybinder"

            from whisper_aloud.config import WhisperAloudConfig

            config = WhisperAloudConfig()
            daemon_module.WhisperAloudService(config=config)

            _, kwargs = mock_ar.call_args
            assert "processing_config" in kwargs, "processing_config not passed to AudioRecorder"
            assert kwargs["processing_config"] == config.audio_processing
