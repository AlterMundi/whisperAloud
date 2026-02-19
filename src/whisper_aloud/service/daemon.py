"""D-Bus daemon service for WhisperAloud."""

import logging
import signal as signal_module
import uuid
from pydbus.generic import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from gi.repository import GLib
from pydbus import SessionBus

from ..audio.recorder import AudioRecorder, RecordingState
from ..clipboard import ClipboardManager
from ..config import WhisperAloudConfig
from ..exceptions import WhisperAloudError
from ..transcriber import Transcriber
from ..gnome_integration import NotificationManager
from ..persistence import HistoryManager
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

        # Initialize system tray indicator
        try:
            self.indicator = WhisperAloudIndicator(
                on_toggle=self.ToggleRecording,
                on_quit=self.Quit,
            )
        except Exception as e:
            logger.warning(f"Failed to initialize indicator: {e}")
            self.indicator = None

        # Initialize hotkey manager
        try:
            self.hotkey_manager = HotkeyManager()
            if self.hotkey_manager.available:
                self.hotkey_manager.register(
                    self.config.hotkey.toggle_recording,
                    self.ToggleRecording,
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
        logger.info(f"Daemon session ID: {self.session_id}")

        # Initialize clipboard manager
        try:
            self.clipboard_manager = ClipboardManager(self.config.clipboard)
        except Exception as e:
            logger.warning(f"Failed to initialize clipboard: {e}")
            self.clipboard_manager = None

        logger.info("WhisperAloudService initialized")

    def _init_components(self) -> None:
        """Initialize recorder and transcriber."""
        try:
            self.recorder = AudioRecorder(
                self.config.audio,
                level_callback=self._on_level,
            )
            self.transcriber = Transcriber(self.config)
            self.transcriber.load_model()
            logger.info("Components initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            raise

    def run(self) -> None:
        """Run the D-Bus service."""
        try:
            # Publish service on D-Bus
            bus = SessionBus()
            bus.publish("org.fede.whisperaloud", self)
            logger.info("D-Bus service published as org.fede.whisperaloud")

            # Keep the service running
            signal_module.signal(signal_module.SIGTERM, self._signal_handler)
            signal_module.signal(signal_module.SIGINT, self._signal_handler)

            # Use GLib main loop
            loop = GLib.MainLoop()
            self._loop = loop
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
        if not self.recorder or not self.recorder.is_recording:
            return False
        self._stop_level_timer()
        self.recorder.cancel()
        self.StatusChanged("idle")
        if self.indicator:
            self.indicator.set_state("idle")
        return True

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
        }

    def GetHistory(self, limit: int) -> list:
        """Return recent history entries."""
        entries = self.history_manager.get_recent(limit=limit)
        return [
            {
                "id": GLib.Variant("i", e.id if e.id is not None else 0),
                "text": GLib.Variant("s", e.text),
                "timestamp": GLib.Variant("s", str(e.timestamp)),
                "duration": GLib.Variant("d", e.duration or 0.0),
                "language": GLib.Variant("s", e.language or ""),
            }
            for e in entries
        ]

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
                    config_dict[section][field] = variant
            new_config = WhisperAloudConfig.from_dict(config_dict)
            new_config.validate()
            self.config = new_config
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

            # Check if model config changed
            if (new_config.model.name != self.config.model.name or
                    new_config.model.device != self.config.model.device):
                logger.info("Model config changed, reloading...")
                self.transcriber = Transcriber(new_config)
                self.transcriber.load_model()

            # Check if audio config changed
            if new_config.audio != self.config.audio:
                logger.info("Audio config changed, recreating recorder...")
                self.recorder = AudioRecorder(new_config.audio)

            old_config = self.config
            self.config = new_config
            self.ConfigChanged({})
            logger.info("Configuration reloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to reload config: {e}")
            self.Error("reload_failed", str(e))
            return False

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

    def _signal_handler(self, signum, frame):
        """Handle SIGTERM/SIGINT for clean shutdown."""
        logger.info(f"Received signal {signum}, shutting down")
        # If recording, stop without transcribing
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

    # ─── Internal helpers ────────────────────────────────────────────────

    def _transcribe_audio(self, audio_data):
        """Transcribe audio data (runs in thread)."""
        return self.transcriber.transcribe_numpy(audio_data)

    def _transcribe_and_emit(self, audio_data) -> None:
        """Transcribe audio and emit completion signal (runs in thread)."""
        try:
            result = self._transcribe_audio(audio_data)

            # Save to history database
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
            self._transcribing = False
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
                self.indicator.set_last_text(result.text)
            self.TranscriptionReady(result.text, meta)

            # Auto-copy to clipboard
            if self.config.clipboard.auto_copy and self.clipboard_manager:
                try:
                    self.clipboard_manager.copy(result.text)
                    logger.info("Transcription copied to clipboard")
                except Exception as e:
                    logger.warning(f"Failed to copy to clipboard: {e}")

            if self.notifications:
                self.notifications.show_transcription_completed(result.text)
            logger.info("Transcription completed and signals emitted")

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            self._transcribing = False
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
            self.Error("transcription_failed", str(e))
            if self.notifications:
                self.notifications.show_error(str(e))

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
