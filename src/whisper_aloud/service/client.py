"""D-Bus client for WhisperAloud daemon."""

import logging
from typing import Callable, Optional

from pydbus import SessionBus

logger = logging.getLogger(__name__)


class WhisperAloudClient:
    """D-Bus client for WhisperAloud daemon.

    Connects to org.fede.whisperaloud on the session bus and provides
    Pythonic wrappers for all daemon methods and signal subscriptions.
    """

    BUS_NAME = "org.fede.whisperaloud"

    def __init__(self):
        """Connect to daemon via D-Bus. Sets is_connected=False if unavailable."""
        self._proxy = None
        self._bus = None
        self._connected = False
        self._subscriptions = []

        self._try_connect()

    def _try_connect(self) -> bool:
        """Internal: attempt to connect to the daemon."""
        try:
            self._bus = SessionBus()
            self._proxy = self._bus.get(self.BUS_NAME)
            self._connected = True
            logger.info("Connected to WhisperAloud daemon via D-Bus")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to WhisperAloud daemon: {e}")
            self._proxy = None
            self._bus = None
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        """Whether the client is connected to the daemon."""
        return self._connected

    def connect(self) -> bool:
        """Attempt to connect/reconnect to the daemon. Returns success."""
        return self._try_connect()

    # ─── Method wrappers ─────────────────────────────────────────────────

    def start_recording(self) -> bool:
        """Start audio recording. Returns True on success."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.StartRecording()
        except Exception as e:
            logger.warning(f"start_recording failed: {e}")
            return False

    def stop_recording(self) -> str:
        """Stop recording and begin transcription. Returns state string."""
        if not self._connected or not self._proxy:
            return ""
        try:
            return self._proxy.StopRecording()
        except Exception as e:
            logger.warning(f"stop_recording failed: {e}")
            return ""

    def toggle_recording(self) -> str:
        """Toggle recording state. Returns state string."""
        if not self._connected or not self._proxy:
            return ""
        try:
            return self._proxy.ToggleRecording()
        except Exception as e:
            logger.warning(f"toggle_recording failed: {e}")
            return ""

    def cancel_recording(self) -> bool:
        """Cancel active recording without transcribing."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.CancelRecording()
        except Exception as e:
            logger.warning(f"cancel_recording failed: {e}")
            return False

    def get_status(self) -> dict:
        """Get current service status."""
        if not self._connected or not self._proxy:
            return {}
        try:
            return self._proxy.GetStatus()
        except Exception as e:
            logger.warning(f"get_status failed: {e}")
            return {}

    def get_history(self, limit: int = 50) -> list:
        """Get recent transcription history."""
        if not self._connected or not self._proxy:
            return []
        try:
            return self._proxy.GetHistory(limit)
        except Exception as e:
            logger.warning(f"get_history failed: {e}")
            return []

    def get_config(self) -> dict:
        """Get current configuration."""
        if not self._connected or not self._proxy:
            return {}
        try:
            return self._proxy.GetConfig()
        except Exception as e:
            logger.warning(f"get_config failed: {e}")
            return {}

    def set_config(self, changes: dict) -> bool:
        """Apply configuration changes."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.SetConfig(changes)
        except Exception as e:
            logger.warning(f"set_config failed: {e}")
            return False

    def reload_config(self) -> bool:
        """Reload configuration from file."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.ReloadConfig()
        except Exception as e:
            logger.warning(f"reload_config failed: {e}")
            return False

    def quit_daemon(self) -> bool:
        """Request the daemon to quit."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.Quit()
        except Exception as e:
            logger.warning(f"quit_daemon failed: {e}")
            return False

    # ─── Signal subscriptions ────────────────────────────────────────────

    def on_recording_started(self, callback: Callable) -> None:
        """Subscribe to RecordingStarted signal."""
        self._subscribe_signal("RecordingStarted", callback)

    def on_recording_stopped(self, callback: Callable) -> None:
        """Subscribe to RecordingStopped signal."""
        self._subscribe_signal("RecordingStopped", callback)

    def on_transcription_ready(self, callback: Callable) -> None:
        """Subscribe to TranscriptionReady signal (text, meta)."""
        self._subscribe_signal("TranscriptionReady", callback)

    def on_level_update(self, callback: Callable) -> None:
        """Subscribe to LevelUpdate signal (level float)."""
        self._subscribe_signal("LevelUpdate", callback)

    def on_status_changed(self, callback: Callable) -> None:
        """Subscribe to StatusChanged signal (state string)."""
        self._subscribe_signal("StatusChanged", callback)

    def on_config_changed(self, callback: Callable) -> None:
        """Subscribe to ConfigChanged signal (changes dict)."""
        self._subscribe_signal("ConfigChanged", callback)

    def on_error(self, callback: Callable) -> None:
        """Subscribe to Error signal (code, message)."""
        self._subscribe_signal("Error", callback)

    def _subscribe_signal(self, signal_name: str, callback: Callable) -> None:
        """Internal: subscribe to a D-Bus signal by name."""
        if not self._connected or not self._proxy:
            logger.warning(
                f"Cannot subscribe to {signal_name}: not connected"
            )
            return
        try:
            sig = getattr(self._proxy, signal_name)
            sub = sig.connect(callback)
            self._subscriptions.append(sub)
        except Exception as e:
            logger.warning(f"Failed to subscribe to {signal_name}: {e}")

    # ─── Cleanup ─────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        """Disconnect and clean up signal subscriptions."""
        self._subscriptions.clear()
        self._proxy = None
        self._bus = None
        self._connected = False
        logger.info("Disconnected from WhisperAloud daemon")
