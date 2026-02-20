"""D-Bus daemon service for WhisperAloud."""

import logging
import os
import signal as signal_module
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from gi.repository import GLib
from pydbus import SessionBus
from pydbus.generic import signal

from ..audio.recorder import AudioRecorder
from ..clipboard import ClipboardManager
from ..config import WhisperAloudConfig
from ..gnome_integration import NotificationManager
from ..persistence import HistoryManager
from ..transcriber import Transcriber
from .hotkey import HotkeyManager
from .indicator import WhisperAloudIndicator

logger = logging.getLogger(__name__)


class WhisperAloudService:
    """
    <node>
      <interface name="org.fede.whisperaloud.Control">
        <method name="StartRecording">
          <arg direction="out" type="b" name="success"/>
        </method>
        <method name="StopRecording">
          <arg direction="out" type="s" name="text"/>
        </method>
        <method name="ToggleRecording">
          <arg direction="out" type="s" name="state"/>
        </method>
        <method name="CancelRecording">
          <arg direction="out" type="b" name="success"/>
        </method>
        <method name="GetStatus">
          <arg direction="out" type="a{sv}" name="status"/>
        </method>
        <method name="GetHistory">
          <arg direction="in" type="u" name="limit"/>
          <arg direction="out" type="aa{sv}" name="entries"/>
        </method>
        <method name="SearchHistory">
          <arg direction="in" type="s" name="query"/>
          <arg direction="in" type="u" name="limit"/>
          <arg direction="out" type="aa{sv}" name="entries"/>
        </method>
        <method name="GetFavoriteHistory">
          <arg direction="in" type="u" name="limit"/>
          <arg direction="out" type="aa{sv}" name="entries"/>
        </method>
        <method name="ToggleHistoryFavorite">
          <arg direction="in" type="i" name="entry_id"/>
          <arg direction="out" type="b" name="success"/>
        </method>
        <method name="DeleteHistoryEntry">
          <arg direction="in" type="i" name="entry_id"/>
          <arg direction="out" type="b" name="success"/>
        </method>
        <method name="GetConfig">
          <arg direction="out" type="a{sv}" name="config"/>
        </method>
        <method name="SetConfig">
          <arg direction="in" type="a{sv}" name="changes"/>
          <arg direction="out" type="b" name="success"/>
        </method>
        <method name="ReloadConfig">
          <arg direction="out" type="b" name="success"/>
        </method>
        <method name="Quit">
          <arg direction="out" type="b" name="success"/>
        </method>
        <signal name="RecordingStarted"/>
        <signal name="RecordingStopped"/>
        <signal name="TranscriptionReady">
          <arg type="s" name="text"/>
          <arg type="a{sv}" name="meta"/>
        </signal>
        <signal name="LevelUpdate">
          <arg type="d" name="level"/>
        </signal>
        <signal name="StatusChanged">
          <arg type="s" name="state"/>
        </signal>
        <signal name="ConfigChanged">
          <arg type="a{sv}" name="changes"/>
        </signal>
        <signal name="Error">
          <arg type="s" name="code"/>
          <arg type="s" name="message"/>
        </signal>
      </interface>
    </node>
    """

    # pydbus signals
    RecordingStarted = signal()
    RecordingStopped = signal()
    TranscriptionReady = signal()
    LevelUpdate = signal()
    StatusChanged = signal()
    ConfigChanged = signal()
    Error = signal()

    def __init__(self, config: Optional[WhisperAloudConfig] = None):
        """Initialize the service."""
        self.config = config or WhisperAloudConfig.load()

        # Core components
        self.recorder: Optional[AudioRecorder] = None
        self.transcriber: Optional[Transcriber] = None

        # Threading
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="whisper-aloud")
        self._model_lock = threading.Lock()

        # State
        self._shutdown = False
        self._transcribing = False
        self._loop = None

        # Level tracking (throttled at 10Hz)
        self._peak_level = 0.0
        self._level_timer_id = None

        # GNOME integration
        self.notifications: Optional[NotificationManager] = None

        # Initialize components
        self._init_components()

        # Thread-safe wrappers for callbacks that may fire from any thread.
        # GLib.idle_add ensures D-Bus signal emissions happen on the main loop.
        self._safe_toggle = lambda: GLib.idle_add(self.ToggleRecording)
        self._safe_quit = lambda: GLib.idle_add(self.Quit)

        # Initialize system tray indicator only when a GUI session is available.
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if has_display:
            try:
                gtk_ready = True
                # AyatanaAppIndicator3 needs GTK3 initialized for menu rendering.
                # init_check avoids hard failures in headless sessions with stale DISPLAY.
                try:
                    import gi

                    gi.require_version('Gtk', '3.0')
                    from gi.repository import Gtk as Gtk3

                    init_result = Gtk3.init_check(None)
                    if isinstance(init_result, tuple):
                        gtk_ready = bool(init_result[0])
                    else:
                        gtk_ready = bool(init_result)
                except Exception:
                    gtk_ready = False

                if gtk_ready:
                    self.indicator = WhisperAloudIndicator(
                        on_toggle=self._safe_toggle,
                        on_quit=self._safe_quit,
                    )
                else:
                    logger.info("GUI session not available; skipping tray indicator")
                    self.indicator = None
            except Exception as e:
                logger.warning(f"Failed to initialize indicator: {e}")
                self.indicator = None
        else:
            logger.info("No DISPLAY/WAYLAND_DISPLAY; skipping tray indicator")
            self.indicator = None

        # Initialize hotkey manager
        try:
            self.hotkey_manager = HotkeyManager()
            if self.hotkey_manager.available:
                self.hotkey_manager.register(
                    self.config.hotkey.toggle_recording,
                    self._safe_toggle,
                )
                logger.info(f"Hotkey registered: {self.config.hotkey.toggle_recording} (backend: {self.hotkey_manager.backend})")
            else:
                logger.info("No hotkey backend available, D-Bus methods only")
        except Exception as e:
            logger.warning(f"Failed to initialize hotkey manager: {e}")
            self.hotkey_manager = None

        # Initialize notifications
        try:
            self.notifications = NotificationManager(self.config)
        except Exception as e:
            logger.warning(f"Failed to initialize notifications: {e}")

        # Initialize history manager for persistence
        self.history_manager = HistoryManager(self.config.persistence)
        self.session_id = str(uuid.uuid4())
        self._start_time = time.monotonic()
        logger.info(f"Daemon session ID: {self.session_id}")

        # Initialize clipboard manager
        try:
            self.clipboard_manager = ClipboardManager(self.config.clipboard)
        except Exception as e:
            logger.warning(f"Failed to initialize clipboard: {e}")
            self.clipboard_manager = None

        logger.info("WhisperAloudService initialized")

    def _init_components(self) -> None:
        """Initialize recorder and transcriber.

        The recorder is created immediately (fast).  The transcriber is
        created but model loading is deferred to first use so that D-Bus
        name registration is not blocked (avoids systemd activation timeout).
        """
        try:
            self.recorder = AudioRecorder(
                self.config.audio,
                level_callback=self._on_level,
            )
            self.transcriber = Transcriber(self.config)
            # Model loading deferred to _ensure_model_loaded()
            self._model_loaded = False
            logger.info("Components initialized (model loading deferred)")
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            raise

    def _ensure_model_loaded(self) -> None:
        """Load the transcription model on first use (thread-safe)."""
        if self._model_loaded:
            return
        with self._model_lock:
            if self._model_loaded:
                return
            logger.info("Loading transcription model (first use)...")
            self.transcriber.load_model()
            self._model_loaded = True
            logger.info("Transcription model loaded")

    def run(self) -> None:
        """Run the D-Bus service."""
        try:
            # Publish service on D-Bus
            bus = SessionBus()
            bus.publish("org.fede.whisperaloud", self)
            logger.info("D-Bus service published as org.fede.whisperaloud")

            # Use GLib main loop
            loop = GLib.MainLoop()
            self._loop = loop

            # Register signal handlers via GLib (safe for D-Bus emission)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal_module.SIGTERM, self._signal_handler_glib)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal_module.SIGINT, self._signal_handler_glib)

            loop.run()

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Service failed to start: {e}")
            raise
        finally:
            self._cleanup()

    # ─── D-Bus Methods ───────────────────────────────────────────────────

    def StartRecording(self) -> bool:
        """Start audio recording."""
        if not self.recorder:
            self.Error("recorder_error", "Recorder not initialized")
            return False

        try:
            self.recorder.start()
            self._start_level_timer()
            self.RecordingStarted()
            self.StatusChanged("recording")
            if self.indicator:
                self.indicator.set_state("recording")
            if self.notifications:
                self.notifications.show_recording_started()
            logger.info("Recording started via D-Bus")
            return True
        except Exception as e:
            self.Error("recording_failed", str(e))
            return False

    def StopRecording(self) -> str:
        """Stop recording and start async transcription."""
        if not self.recorder or not self.transcriber:
            self.Error("component_error", "Components not initialized")
            return "error"

        try:
            self._stop_level_timer()
            audio_data = self.recorder.stop()
            self.RecordingStopped()
            self.StatusChanged("transcribing")
            if self.indicator:
                self.indicator.set_state("transcribing")
            if self.notifications:
                self.notifications.show_recording_stopped()

            # Start transcription in background thread (non-blocking)
            self._transcribing = True
            self.executor.submit(self._transcribe_and_emit, audio_data)

            # Return immediately - result will be emitted via signal
            return "transcribing"

        except Exception as e:
            self.Error("stop_failed", str(e))
            return "error"

    def ToggleRecording(self) -> str:
        """Toggle recording state."""
        if not self.recorder:
            self.Error("recorder_error", "Recorder not initialized")
            return "error"

        if self.recorder.is_recording:
            return self.StopRecording()
        else:
            self.StartRecording()
            return "recording"

    def CancelRecording(self) -> bool:
        """Cancel active recording without transcribing."""
        if self.recorder and self.recorder.is_recording:
            self._stop_level_timer()
            self.recorder.cancel()
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
            return True

        if self._transcribing and self.transcriber:
            self.transcriber.cancel_transcription()
            self._transcribing = False
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
            logger.info("Transcription cancellation requested via D-Bus")
            return True

        return False

    def GetStatus(self) -> dict:
        """Get current service status as a dict."""
        from whisper_aloud import __version__
        state = "transcribing" if self._transcribing else (
            self.recorder.state.value if self.recorder else "error"
        )
        return {
            "state": GLib.Variant("s", state),
            "version": GLib.Variant("s", __version__),
            "model": GLib.Variant("s", self.config.model.name),
            "device": GLib.Variant("s", self.config.model.device),
            "hotkey_backend": GLib.Variant("s", self.hotkey_manager.backend if self.hotkey_manager else "none"),
            "uptime": GLib.Variant("d", time.monotonic() - self._start_time),
        }

    def GetHistory(self, limit: int) -> list:
        """Return recent history entries."""
        safe_limit = max(1, int(limit))
        entries = self.history_manager.get_recent(limit=safe_limit)
        return [self._serialize_history_entry(entry) for entry in entries]

    def SearchHistory(self, query: str, limit: int) -> list:
        """Search history entries by text."""
        safe_limit = max(1, int(limit))
        safe_query = (query or "").strip()
        if not safe_query:
            return self.GetHistory(safe_limit)
        try:
            entries = self.history_manager.search(safe_query, limit=safe_limit)
        except Exception as e:
            logger.error("SearchHistory failed: %s", e)
            self.Error("history_search_failed", str(e))
            return []
        return [self._serialize_history_entry(entry) for entry in entries]

    def GetFavoriteHistory(self, limit: int) -> list:
        """Return favorite history entries."""
        safe_limit = max(1, int(limit))
        try:
            entries = self.history_manager.get_favorites(limit=safe_limit)
        except Exception as e:
            logger.error("GetFavoriteHistory failed: %s", e)
            self.Error("history_fetch_failed", str(e))
            return []
        return [self._serialize_history_entry(entry) for entry in entries]

    def ToggleHistoryFavorite(self, entry_id: int) -> bool:
        """Toggle favorite status for a history entry."""
        try:
            return self.history_manager.toggle_favorite(int(entry_id))
        except Exception as e:
            logger.error("ToggleHistoryFavorite failed: %s", e)
            self.Error("history_update_failed", str(e))
            return False

    def DeleteHistoryEntry(self, entry_id: int) -> bool:
        """Delete a history entry."""
        try:
            return self.history_manager.delete(int(entry_id))
        except Exception as e:
            logger.error("DeleteHistoryEntry failed: %s", e)
            self.Error("history_delete_failed", str(e))
            return False

    def GetConfig(self) -> dict:
        """Return current configuration flattened to GLib variants."""
        d = self.config.to_dict()
        result = {}
        for section, values in d.items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                flat_key = f"{section}.{key}"
                if isinstance(value, bool):
                    result[flat_key] = GLib.Variant("b", value)
                elif isinstance(value, int):
                    result[flat_key] = GLib.Variant("i", value)
                elif isinstance(value, float):
                    result[flat_key] = GLib.Variant("d", value)
                elif value is not None:
                    result[flat_key] = GLib.Variant("s", str(value))
        return result

    def SetConfig(self, changes: dict) -> bool:
        """Apply configuration changes."""
        try:
            config_dict = self.config.to_dict()
            for key, variant in changes.items():
                section, field = key.split(".", 1)
                if section in config_dict and field in config_dict[section]:
                    # Unpack GLib.Variant to native Python value
                    value = variant.unpack() if hasattr(variant, 'unpack') else variant
                    config_dict[section][field] = value
            new_config = WhisperAloudConfig.from_dict(config_dict)
            new_config.validate()
            self._apply_config_changes(new_config)
            self.config.save()
            self.ConfigChanged(changes)
            return True
        except Exception as e:
            self.Error("config_invalid", str(e))
            return False

    def ReloadConfig(self) -> bool:
        """Reload configuration from file and apply changes."""
        try:
            logger.info("Reloading configuration...")
            new_config = WhisperAloudConfig.load()
            self._apply_config_changes(new_config)
            self.ConfigChanged({})
            logger.info("Configuration reloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to reload config: {e}")
            self.Error("reload_failed", str(e))
            return False

    def _apply_config_changes(self, new_config: 'WhisperAloudConfig') -> None:
        """Re-initialize components whose config has changed."""
        # Check if model config changed
        if (new_config.model.name != self.config.model.name or
                new_config.model.device != self.config.model.device):
            logger.info("Model config changed, reloading...")
            self.transcriber = Transcriber(new_config)
            self.transcriber.load_model()
            self._model_loaded = True

        # Check if audio config changed
        if (new_config.audio != self.config.audio or
                new_config.audio_processing != self.config.audio_processing):
            logger.info("Audio config changed, recreating recorder...")
            self.recorder = AudioRecorder(
                new_config.audio,
                level_callback=self._on_level,
                processing_config=new_config.audio_processing,
            )

        # Check if hotkey config changed
        if (self.hotkey_manager and self.hotkey_manager.available and
                new_config.hotkey.toggle_recording != self.config.hotkey.toggle_recording):
            logger.info("Hotkey config changed, re-registering...")
            self.hotkey_manager.unregister()
            self.hotkey_manager.register(
                new_config.hotkey.toggle_recording,
                self._safe_toggle,
            )

        if self.notifications:
            self.notifications.config = new_config

        self.config = new_config

    def Quit(self) -> bool:
        """Quit the service."""
        logger.info("Quit requested via D-Bus")
        self._shutdown = True

        if self._loop:
            self._loop.quit()
        else:
            import os
            os._exit(0)
        return True

    def _serialize_history_entry(self, entry) -> dict:
        """Convert a HistoryEntry instance to a D-Bus-friendly dictionary."""
        timestamp = ""
        if getattr(entry, "timestamp", None):
            ts = entry.timestamp
            timestamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

        tags = getattr(entry, "tags", None) or []
        if not isinstance(tags, list):
            tags = []

        return {
            "id": GLib.Variant("i", int(entry.id) if entry.id is not None else 0),
            "text": GLib.Variant("s", entry.text or ""),
            "timestamp": GLib.Variant("s", timestamp),
            "duration": GLib.Variant("d", float(entry.duration or 0.0)),
            "language": GLib.Variant("s", entry.language or ""),
            "confidence": GLib.Variant("d", float(entry.confidence or 0.0)),
            "processing_time": GLib.Variant("d", float(entry.processing_time or 0.0)),
            "favorite": GLib.Variant("b", bool(entry.favorite)),
            "notes": GLib.Variant("s", entry.notes or ""),
            "tags": GLib.Variant("as", [str(tag) for tag in tags]),
        }

    # ─── Level tracking ────────────────────────────────────────────────

    def _on_level(self, level):
        """Track peak level from audio callback."""
        self._peak_level = max(self._peak_level, level.peak)

    def _start_level_timer(self):
        """Start 10Hz level emission timer."""
        if self._level_timer_id is None:
            self._level_timer_id = GLib.timeout_add(100, self._emit_level)

    def _stop_level_timer(self):
        """Stop level emission timer."""
        if self._level_timer_id is not None:
            GLib.source_remove(self._level_timer_id)
            self._level_timer_id = None

    def _emit_level(self):
        """Emit throttled level update (called at 10Hz by GLib)."""
        level = self._peak_level
        self._peak_level = 0.0
        self.LevelUpdate(level)
        return True  # Keep timer running

    # ─── Signal handling ─────────────────────────────────────────────────

    def _signal_handler_glib(self):
        """Handle SIGTERM/SIGINT from GLib main loop (safe for D-Bus emission)."""
        logger.info("Received shutdown signal, shutting down")
        if self.recorder and self.recorder.is_recording:
            try:
                self.recorder.cancel()
            except Exception as e:
                logger.warning(f"Error during shutdown recording stop: {e}")

        self.StatusChanged("shutdown")
        if self.indicator:
            self.indicator.set_state("shutdown")
        self._stop_level_timer()
        self._shutdown = True
        if hasattr(self, '_loop') and self._loop:
            self._loop.quit()
        return GLib.SOURCE_REMOVE

    # ─── Internal helpers ────────────────────────────────────────────────

    def _transcribe_audio(self, audio_data):
        """Transcribe audio data (runs in thread)."""
        self._ensure_model_loaded()
        return self.transcriber.transcribe_numpy(audio_data)

    def _transcribe_and_emit(self, audio_data) -> None:
        """Transcribe audio and emit completion signal (runs in thread).

        All D-Bus signal emissions and indicator updates are marshalled
        to the GLib main loop via idle_add for thread safety.
        """
        try:
            result = self._transcribe_audio(audio_data)

            # Save to history database (safe from worker thread)
            try:
                entry_id = self.history_manager.add_transcription(
                    result=result,
                    audio=audio_data if self.config.persistence.save_audio else None,
                    sample_rate=self.config.audio.sample_rate,
                    session_id=self.session_id
                )
                logger.info(f"Transcription saved to database: ID {entry_id}")
            except Exception as e:
                logger.error(f"Failed to save history: {e}")
                entry_id = -1

            meta = {
                "duration": GLib.Variant("d", result.duration),
                "language": GLib.Variant("s", result.language),
                "confidence": GLib.Variant("d", result.confidence),
                "history_id": GLib.Variant("i", entry_id),
            }

            def _emit_success():
                self._transcribing = False
                self.StatusChanged("idle")
                if self.indicator:
                    self.indicator.set_state("idle")
                    self.indicator.set_last_text(result.text)
                self.TranscriptionReady(result.text, meta)
                if self.config.clipboard.auto_copy and self.clipboard_manager:
                    try:
                        self.clipboard_manager.copy(result.text)
                        logger.info("Transcription copied to clipboard")
                    except Exception as e:
                        logger.warning(f"Failed to copy to clipboard: {e}")
                if self.notifications:
                    self.notifications.show_transcription_completed(result.text)
                logger.info("Transcription completed and signals emitted")
                return False

            GLib.idle_add(_emit_success)

        except Exception as e:
            error_message = str(e)
            logger.error(f"Transcription failed: {error_message}")

            def _emit_error():
                self._transcribing = False
                self.StatusChanged("idle")
                if self.indicator:
                    self.indicator.set_state("idle")
                self.Error("transcription_failed", error_message)
                if self.notifications:
                    self.notifications.show_error(error_message)
                return False

            GLib.idle_add(_emit_error)

    def _cleanup(self) -> None:
        """Clean up resources."""
        logger.info("Cleaning up service resources")

        self._stop_level_timer()

        # Unregister hotkeys
        if self.hotkey_manager:
            self.hotkey_manager.unregister()

        # Shutdown executor
        self.executor.shutdown(wait=True)

        # Cleanup components
        if self.recorder:
            self.recorder.cancel()
        if self.transcriber:
            self.transcriber.unload_model()

        logger.info("Service cleanup complete")
