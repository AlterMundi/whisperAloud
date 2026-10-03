"""D-Bus client for WhisperAloud daemon."""

import logging
import time
from typing import Any, Callable, Dict, Optional

from pydbus import SessionBus

logger = logging.getLogger(__name__)

# Default total wait budget for connect_with_activation_wait() (field bug 1):
# how long a caller is willing to keep polling while the daemon looks like
# it's mid-startup (see _is_starting_error below). Flatpak activation plus
# the daemon's own audio/model/tray/hotkey init can take noticeably longer
# than a bare-metal install, especially on first run.
DEFAULT_ACTIVATION_WAIT_SECONDS = 30.0
DEFAULT_ACTIVATION_POLL_INTERVAL_SECONDS = 1.0


def _is_starting_error(exc: BaseException) -> bool:
    """True if `exc` is pydbus's "object does not export any interfaces" error.

    This is what pydbus's ``SessionBus().get(BUS_NAME)`` raises (a KeyError,
    see pydbus.proxy.CompositeInterface) when BUS_NAME already has an owner
    -- i.e. this connection attempt (or an earlier one) already triggered
    D-Bus activation -- but the daemon process hasn't called
    ``bus.register_object()`` yet (see service/daemon.py's
    acquire_bus_name()/run() split, WHISP-63): the name is claimed before
    WhisperAloudService() is even constructed, so there is a window --
    observed to be several seconds long, longer under Flatpak -- where the
    name has an owner but exports no D-Bus object at all.

    Matched on message text since pydbus doesn't expose a dedicated
    exception type for this. Deliberately narrow: pydbus's sibling error
    ("no such object; you might need to pass object path...", raised when
    Introspect itself comes back empty) is NOT treated as "starting" -- that
    indicates something else is wrong, not a startup race.
    """
    return "does not export any interfaces" in str(exc)


def _variant_for_value(value: Any):
    """Wrap a plain Python value as a GLib.Variant for a D-Bus a{sv} argument.

    pydbus validates outgoing a{sv} arguments against the real introspected
    signature and rejects plain Python values with "Expected GLib.Variant,
    but got <type>" -- every value must be wrapped before being handed to
    the proxy. Matches the type choices the daemon itself uses when it
    flattens config to variants (see WhisperAloudService.GetConfig):
    bool -> 'b', int -> 'i', float -> 'd', str -> 's'.

    bool is checked before int because bool is an int subclass in Python.
    A value that is already a GLib.Variant is returned unchanged. Any other
    type raises TypeError with a clear message.
    """
    from gi.repository import GLib

    # Duck-typed rather than isinstance(value, GLib.Variant): in unit tests
    # that stub out gi.repository entirely, GLib.Variant is a MagicMock, not
    # a class, and isinstance() rejects a non-type second argument outright.
    if hasattr(value, "get_type_string") and hasattr(value, "unpack"):
        return value
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, int):
        return GLib.Variant("i", value)
    if isinstance(value, float):
        return GLib.Variant("d", value)
    raise TypeError(
        f"Unsupported type for D-Bus a{{sv}} value: {type(value).__name__!r} "
        f"(value={value!r}). Supported: GLib.Variant, bool, str, int, float."
    )


def _wrap_config_changes(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap every value of a config-changes dict as a GLib.Variant, ready
    for a D-Bus a{sv} method call such as SetConfig."""
    return {key: _variant_for_value(value) for key, value in changes.items()}


class WhisperAloudClient:
    """D-Bus client for WhisperAloud daemon.

    Connects to org.fede.whisperaloud on the session bus and provides
    Pythonic wrappers for all daemon methods and signal subscriptions.
    Automatically reconnects when the daemon restarts (via NameOwnerChanged).
    """

    BUS_NAME = "org.fede.whisperaloud"

    def __init__(self):
        """Connect to daemon via D-Bus. Sets is_connected=False if unavailable."""
        self._proxy = None
        self._bus = None
        self._connected = False
        self._starting = False
        self._subscriptions = []
        self._name_watch_id = None
        self._on_connected_callback: Optional[Callable] = None
        self._on_disconnected_callback: Optional[Callable] = None
        self._pending_signal_setups: list[Callable] = []

        self._try_connect()

    def _try_connect(self) -> bool:
        """Internal: attempt to connect to the daemon."""
        try:
            if self._bus is None:
                self._bus = SessionBus()
            self._proxy = self._bus.get(self.BUS_NAME)
            self._connected = True
            self._starting = False
            logger.info("Connected to WhisperAloud daemon via D-Bus")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to WhisperAloud daemon: {e}")
            self._proxy = None
            self._connected = False
            self._starting = _is_starting_error(e)
            return False

    @property
    def is_connected(self) -> bool:
        """Whether the client is connected to the daemon."""
        return self._connected

    @property
    def is_starting(self) -> bool:
        """True if the last failed connect attempt looked like the daemon is
        mid-startup (bus name owned, D-Bus object not published yet) rather
        than genuinely unavailable. See _is_starting_error()."""
        return self._starting

    def connect(self) -> bool:
        """Attempt to connect/reconnect to the daemon. Returns success.

        Creates a fresh bus connection to handle cases where the previous
        bus object is stale.
        """
        self._bus = None
        return self._try_connect()

    def connect_with_activation_wait(
        self,
        *,
        max_wait_seconds: float = DEFAULT_ACTIVATION_WAIT_SECONDS,
        poll_interval_seconds: float = DEFAULT_ACTIVATION_POLL_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> bool:
        """Block (via `sleep`) retrying the connection while the daemon looks
        like it's mid-startup, for up to `max_wait_seconds` total.

        Used by synchronous callers (the CLI) that need a single yes/no
        answer rather than the GUI's non-blocking retry-on-the-main-loop
        approach (see ui/daemon_connect_logic.py, which makes the same
        "keep trying while starting" decision without ever blocking a
        thread). Only continues retrying while `is_starting` is True --
        a connection failure of any other kind (no activation file, bus
        unreachable, etc.) returns False immediately, since waiting
        longer would not help.

        `sleep`/`clock` are injectable so tests can exercise the loop
        without a real wall-clock wait.
        """
        if self.is_connected:
            return True

        deadline = clock() + max_wait_seconds
        while self.is_starting and clock() < deadline:
            sleep(poll_interval_seconds)
            self.connect()
            if self.is_connected:
                return True

        return self.is_connected

    def watch_name(self, on_connected: Optional[Callable] = None,
                   on_disconnected: Optional[Callable] = None) -> None:
        """Watch for daemon appearing/disappearing on the bus.

        Automatically reconnects when daemon restarts (systemd restart).
        Callbacks fire on the GLib main loop thread.
        """
        self._on_connected_callback = on_connected
        self._on_disconnected_callback = on_disconnected

        if self._bus is None:
            self._bus = SessionBus()

        try:
            from gi.repository import Gio
            connection = self._bus.con  # underlying Gio.DBusConnection

            self._name_watch_id = Gio.bus_watch_name_on_connection(
                connection,
                self.BUS_NAME,
                Gio.BusNameWatcherFlags.NONE,
                self._on_name_appeared,
                self._on_name_vanished,
            )
            logger.info(f"Watching for {self.BUS_NAME} on session bus")
        except Exception as e:
            logger.warning(f"Failed to set up name watch: {e}")

    def _on_name_appeared(self, connection, name, name_owner):
        """Called when daemon name appears on the bus."""
        logger.info(f"Daemon appeared: {name} (owner: {name_owner})")
        if not self._connected:
            self._try_connect()
            # Re-subscribe signals
            for setup_fn in self._pending_signal_setups:
                setup_fn()
        if self._on_connected_callback:
            self._on_connected_callback()

    def _on_name_vanished(self, connection, name):
        """Called when daemon name vanishes from the bus."""
        logger.info(f"Daemon vanished: {name}")
        self._connected = False
        self._proxy = None
        if self._on_disconnected_callback:
            self._on_disconnected_callback()

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

    def search_history(self, query: str, limit: int = 50) -> list:
        """Search transcription history by text."""
        if not self._connected or not self._proxy:
            return []
        try:
            return self._proxy.SearchHistory(query, limit)
        except Exception as e:
            logger.warning(f"search_history failed: {e}")
            return []

    def get_favorite_history(self, limit: int = 50) -> list:
        """Get favorite transcription history entries."""
        if not self._connected or not self._proxy:
            return []
        try:
            return self._proxy.GetFavoriteHistory(limit)
        except Exception as e:
            logger.warning(f"get_favorite_history failed: {e}")
            return []

    def toggle_history_favorite(self, entry_id: int) -> bool:
        """Toggle favorite status for a history entry."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.ToggleHistoryFavorite(entry_id)
        except Exception as e:
            logger.warning(f"toggle_history_favorite failed: {e}")
            return False

    def delete_history_entry(self, entry_id: int) -> bool:
        """Delete a history entry by id."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.DeleteHistoryEntry(entry_id)
        except Exception as e:
            logger.warning(f"delete_history_entry failed: {e}")
            return False

    def update_history_entry(self, entry_id: int, text: str) -> bool:
        """Update the transcribed text of a history entry."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.UpdateHistoryEntry(entry_id, text)
        except Exception as e:
            logger.warning(f"update_history_entry failed: {e}")
            return False

    def export_history(self, format: str, filters: Optional[Dict[str, Any]] = None) -> str:
        """Export history entries as a string via the daemon's ExportHistory.

        `format` is one of "md"/"json"/"csv"/"txt". `filters` may include
        "since"/"until" (ISO date/datetime strings), "favorites_only" (bool),
        "query" (str), and "limit" (int) -- see docs/dbus-api.md. Values are
        wrapped as GLib.Variant the same way SetConfig's `changes` are
        (ExportHistory's `filters` is also an a{sv} argument).

        Returns the exported content, or "" if not connected or the daemon
        call failed/was refused (see the Error signal for why). The caller
        is responsible for writing the result to disk -- the daemon never
        writes to a path itself (see docs/dbus-api.md's ExportHistory
        section for the rationale).
        """
        if not self._connected or not self._proxy:
            return ""
        try:
            return self._proxy.ExportHistory(format, _wrap_config_changes(filters or {}))
        except Exception as e:
            logger.warning(f"export_history failed: {e}")
            return ""

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
        """Apply configuration changes.

        Wraps each value as a GLib.Variant before calling the daemon --
        SetConfig's D-Bus signature is a{sv}, and pydbus rejects plain
        Python values outright.
        """
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.SetConfig(_wrap_config_changes(changes))
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

    def download_model(self, engine: str, name: str, accept_license: bool = False) -> bool:
        """Start a background download of a catalog (engine, name) model.

        Returns True only if the daemon *started* the download (False if one
        was already running, or the model/license was refused -- see the
        Error signal for why). Progress/completion arrive via
        on_download_progress/on_download_finished, not this return value.
        """
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.DownloadModel(engine, name, accept_license)
        except Exception as e:
            logger.warning(f"download_model failed: {e}")
            return False

    def cancel_download(self) -> bool:
        """Request cancellation of the in-flight model download, if any."""
        if not self._connected or not self._proxy:
            return False
        try:
            return self._proxy.CancelDownload()
        except Exception as e:
            logger.warning(f"cancel_download failed: {e}")
            return False

    def get_api_version(self) -> str:
        """Get the org.fede.whisperaloud.Control interface's semantic version.

        This is a D-Bus property (not a method call), read via plain
        attribute access on the proxy. Returns "" if not connected or the
        property can't be read (e.g. an older daemon without ApiVersion).
        """
        if not self._connected or not self._proxy:
            return ""
        try:
            return self._proxy.ApiVersion
        except Exception as e:
            logger.warning(f"get_api_version failed: {e}")
            return ""

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

    def on_partial_transcript(self, callback: Callable) -> None:
        """Subscribe to PartialTranscript signal (session_id, committed, tentative, seq).

        Emitted while recording with a streaming engine configured (WHISP-57);
        never emitted with a batch (non-streaming) engine. `session_id`
        matches the one carried in the following TranscriptionReady's `meta`.
        """
        self._subscribe_signal("PartialTranscript", callback)

    def on_status_changed(self, callback: Callable) -> None:
        """Subscribe to StatusChanged signal (state string)."""
        self._subscribe_signal("StatusChanged", callback)

    def on_config_changed(self, callback: Callable) -> None:
        """Subscribe to ConfigChanged signal (changes dict)."""
        self._subscribe_signal("ConfigChanged", callback)

    def on_error(self, callback: Callable) -> None:
        """Subscribe to Error signal (code, message)."""
        self._subscribe_signal("Error", callback)

    def on_download_progress(self, callback: Callable) -> None:
        """Subscribe to DownloadProgress signal (engine, name, bytes_done, bytes_total)."""
        self._subscribe_signal("DownloadProgress", callback)

    def on_download_finished(self, callback: Callable) -> None:
        """Subscribe to DownloadFinished signal (engine, name, ok, error)."""
        self._subscribe_signal("DownloadFinished", callback)

    def _subscribe_signal(self, signal_name: str, callback: Callable) -> None:
        """Internal: subscribe to a D-Bus signal by name.

        Also records the subscription for re-establishment on reconnect.
        """
        def _do_subscribe():
            if not self._connected or not self._proxy:
                return
            try:
                sig = getattr(self._proxy, signal_name)
                sub = sig.connect(callback)
                self._subscriptions.append(sub)
            except Exception as e:
                logger.warning(f"Failed to subscribe to {signal_name}: {e}")

        # Record for reconnection
        self._pending_signal_setups.append(_do_subscribe)

        if not self._connected or not self._proxy:
            logger.warning(
                f"Cannot subscribe to {signal_name}: not connected (will retry on reconnect)"
            )
            return
        _do_subscribe()

    # ─── Cleanup ─────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        """Disconnect and clean up signal subscriptions."""
        if self._name_watch_id is not None:
            try:
                from gi.repository import Gio
                Gio.bus_unwatch_name(self._name_watch_id)
            except Exception:
                pass
            self._name_watch_id = None
        self._subscriptions.clear()
        self._pending_signal_setups.clear()
        self._proxy = None
        self._bus = None
        self._connected = False
        logger.info("Disconnected from WhisperAloud daemon")
