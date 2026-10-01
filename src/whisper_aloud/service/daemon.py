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
from ..clipboard import (  # noqa: F401 -- ClipboardManager/TestClipboardStub kept importable
    ClipboardManager,       # here only so existing tests can still patch daemon_module.ClipboardManager;
    TestClipboardStub,      # actual construction always goes through build_clipboard_manager() below.
    WindowManager,
    build_clipboard_manager,
)
from ..config import WhisperAloudConfig, detect_config_changes
from ..gnome_integration import NotificationManager
from ..persistence import HistoryManager
from ..stt.engine import SpeechEngine, create_engine
from .hotkey import HotkeyManager
from .indicator import WhisperAloudIndicator
from .media_control import GainController, MprisController

logger = logging.getLogger(__name__)

# D-Bus identifiers. Kept as module constants so the CLI entrypoint can
# acquire the name before constructing the (heavy) service object.
BUS_NAME = "org.fede.whisperaloud"
OBJECT_PATH = "/org/fede/whisperaloud"

# Distinct exit code for "another instance already owns the bus name".
# The systemd unit maps this to RestartPreventExitStatus so a losing
# instance does not restart-loop forever (see data/whisper-aloud.service).
EXIT_ALREADY_RUNNING = 3

# Semantic version of the org.fede.whisperaloud.Control D-Bus *interface*
# itself (methods/signals/properties), not the WhisperAloud application
# version (see GetStatus()["version"] for that). Exposed read-only via the
# ApiVersion property. Bump per docs/dbus-api.md's versioning policy
# (additive = minor, breaking = major + a new Control2 interface) and
# record the change in that document's Changelog section, in the same
# commit as the interface change.
API_VERSION = "1.1.1"

# Polling cadence for PartialTranscript while a streaming engine is recording
# (WHISP-57): docs/dbus-api.md's "Planned" entry says 3-10Hz; 5Hz is a
# mid-range default. An internal polling cadence, not a user-facing setting,
# so it lives here as a constant rather than in WhisperAloudConfig.
PARTIAL_TRANSCRIPT_HZ = 5
PARTIAL_TRANSCRIPT_INTERVAL_MS = int(1000 / PARTIAL_TRANSCRIPT_HZ)


class AlreadyRunningError(RuntimeError):
    """Raised when another process already owns BUS_NAME on the session bus."""

    def __init__(self, message: str, pid: Optional[int] = None):
        super().__init__(message)
        self.pid = pid


def _lookup_owner_pid(bus, name: str = BUS_NAME) -> Optional[int]:
    """Best-effort lookup of the PID currently holding `name`.

    Returns None if the lookup fails for any reason (e.g. no permission,
    bus quirks) -- this is purely for a nicer log line, never load-bearing.
    """
    try:
        unique_name = bus.dbus.GetNameOwner(name)
        return int(bus.dbus.GetConnectionUnixProcessID(unique_name))
    except Exception:
        return None


def acquire_bus_name(bus):
    """Claim BUS_NAME on `bus` without publishing any object.

    Doing this before constructing WhisperAloudService means a losing
    daemon start (another instance already owns the name) never pays for
    audio/tray/model initialization -- it fails fast, here, first.

    Returns the NameOwner context manager on success; keep a reference to
    it for the lifetime of the process (releasing it gives up the name).

    Raises AlreadyRunningError if another process already owns the name.
    Any other failure to acquire the name is re-raised unchanged.
    """
    try:
        return bus.request_name(BUS_NAME, allow_replacement=False, replace=False)
    except RuntimeError as e:
        if "already exists" in str(e):
            pid = _lookup_owner_pid(bus)
            suffix = f" (pid {pid})" if pid else " (pid unknown)"
            raise AlreadyRunningError(f"another instance owns {BUS_NAME}{suffix}", pid=pid) from None
        raise


def _wrap_flat_config_as_variants(flat: dict) -> dict:
    """Wrap each value of a flat {"section.field": value} dict as a
    GLib.Variant, for any a{sv} payload built from config values (GetConfig's
    return value, and the ConfigChanged signal's `changes` argument).

    pydbus requires every value of an outgoing a{sv} argument to already be
    a GLib.Variant -- a signal emission is just as strict about this as a
    method call's return value, so this must be applied before
    self.ConfigChanged(...) is called, not just before returning from
    GetConfig().
    """
    result = {}
    for key, value in flat.items():
        if isinstance(value, bool):
            result[key] = GLib.Variant("b", value)
        elif isinstance(value, int):
            result[key] = GLib.Variant("i", value)
        elif isinstance(value, float):
            result[key] = GLib.Variant("d", value)
        elif value is not None:
            result[key] = GLib.Variant("s", str(value))
    return result


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
        <method name="UpdateHistoryEntry">
          <arg direction="in" type="i" name="entry_id"/>
          <arg direction="in" type="s" name="text"/>
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
        <signal name="PartialTranscript">
          <arg type="s" name="session_id"/>
          <arg type="s" name="committed"/>
          <arg type="s" name="tentative"/>
          <arg type="u" name="seq"/>
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
        <property name="ApiVersion" type="s" access="read"/>
      </interface>
    </node>
    """

    # pydbus signals
    RecordingStarted = signal()
    RecordingStopped = signal()
    TranscriptionReady = signal()
    LevelUpdate = signal()
    PartialTranscript = signal()
    StatusChanged = signal()
    ConfigChanged = signal()
    Error = signal()

    def __init__(
        self,
        config: Optional[WhisperAloudConfig] = None,
        bus=None,
        name_owner=None,
    ):
        """Initialize the service.

        Args:
            config: Loaded config, or None to load from disk.
            bus: An already-connected SessionBus, if the caller already
                acquired one (see acquire_bus_name). If None, run() creates
                its own.
            name_owner: The NameOwner returned by a prior acquire_bus_name()
                call on `bus`. Passing this means run() will not attempt to
                re-request the name (and construction of this object, with
                its audio/tray/model setup below, implies the caller already
                confirmed no other instance owns the bus name).
        """
        self.config = config or WhisperAloudConfig.load()
        self._bus = bus
        self._name_owner = name_owner

        # WHISP-46 e2e harness: under WHISPER_ALOUD_TEST_MODE=1, skip the tray
        # indicator, hotkey manager, notifications, and media control
        # (MPRIS/GainController) entirely -- defense in depth on top of each
        # component's own env-driven degradation, so a test that happens to
        # set DISPLAY/WAYLAND_DISPLAY/XDG_SESSION_TYPE (or a bad config) can
        # never make the daemon open a real indicator, contact the real
        # hotkey portal, pause a real media player, or construct real
        # notifications. Computed first, before _init_components(), since
        # that method also needs it (MprisController).
        self._test_mode = os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1"

        # Core components
        self.recorder: Optional[AudioRecorder] = None
        self.transcriber: Optional[SpeechEngine] = None

        # Threading
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="whisper-aloud")
        self._model_lock = threading.Lock()

        # State
        self._shutdown = False
        self._transcribing = False
        self._loop = None

        # WHISP-73: monotonically increasing token identifying the
        # currently-live transcription job. Captured by the executor-thread
        # job at submission time; CancelRecording()/Quit() bump it to
        # invalidate whatever job is in flight so its eventual (possibly
        # late) success/error emission can recognize itself as stale and
        # drop silently -- no signal, no clipboard/paste, no history write.
        self._job_id = 0

        # WHISP-74: a model-reload SetConfig()/ReloadConfig() that lands
        # while a recording or transcription is in flight stores the new
        # config here instead of swapping self.transcriber immediately; it
        # is applied once the daemon returns to idle (success, error, or
        # cancel). A later deferred reload while one is already pending
        # overwrites it -- the last SetConfig() during a job wins.
        self._pending_reload_config: Optional["WhisperAloudConfig"] = None

        # Level tracking (throttled at 10Hz)
        self._peak_level = 0.0
        self._level_timer_id = None

        # Streaming session state (WHISP-57). `_current_session_id` is
        # generated fresh in StartRecording for every recording (streaming or
        # not), so TranscriptionReady can always carry a `session_id` for
        # client-side correlation; `_streaming_session` / `_partial_timer_id`
        # / `_last_partial_seq` are only meaningful while the configured
        # engine `is_streaming` (see StartRecording).
        self._current_session_id: Optional[str] = None
        self._streaming_session = False
        self._partial_timer_id = None
        self._last_partial_seq = None

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
        if self._test_mode:
            logger.info("WHISPER_ALOUD_TEST_MODE=1: tray indicator disabled")
            self.indicator = None
        elif has_display:
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

        # Initialize hotkey manager (skipped entirely in test mode -- no
        # portal contact at all, not even a check, per WHISP-46).
        if self._test_mode:
            logger.info("WHISPER_ALOUD_TEST_MODE=1: hotkeys disabled, D-Bus methods only")
            self.hotkey_manager = None
        else:
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

        # Initialize notifications (skipped entirely in test mode).
        if self._test_mode:
            logger.info("WHISPER_ALOUD_TEST_MODE=1: notifications disabled")
        else:
            try:
                self.notifications = NotificationManager(self.config)
            except Exception as e:
                logger.warning(f"Failed to initialize notifications: {e}")

        # Initialize history manager for persistence
        self.history_manager = HistoryManager(self.config.persistence)
        self.session_id = str(uuid.uuid4())
        self._start_time = time.monotonic()
        logger.info(f"Daemon session ID: {self.session_id}")

        # Initialize clipboard manager via the single tested factory
        # (build_clipboard_manager), which returns a TestClipboardStub under
        # WHISPER_ALOUD_TEST_MODE=1 and a real ClipboardManager otherwise --
        # branching here directly, instead of delegating to the factory
        # tests/test_clipboard.py exercises, previously let the real
        # ClipboardManager through in test mode (WHISP-46 incident: it
        # overwrote the operator's real Wayland clipboard even with
        # WAYLAND_DISPLAY unset).
        try:
            self.clipboard_manager = build_clipboard_manager(self.config.clipboard)
        except Exception as e:
            logger.warning(f"Failed to initialize clipboard: {e}")
            self.clipboard_manager = None

        # WindowManager._detect_backend() probes a real `xdotool` subprocess
        # at construction time -- skip it entirely in test mode (WHISP-46).
        if self._test_mode:
            self.window_manager = None
        else:
            self.window_manager = WindowManager()
        self._captured_by_job: dict = {}  # job_id → WindowInfo

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
                processing_config=self.config.audio_processing,
                auto_stop_callback=self._on_recorder_auto_stop,
            )
            self.transcriber = create_engine(self.config)
            # Model loading deferred to _ensure_model_loaded()
            self._model_loaded = False

            # Smart recording flow controllers
            self._mpris = MprisController()
            self._gain = GainController()
            self._gain.restore_from_crash_file()
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
        """Run the D-Bus service.

        Bus-name acquisition is kept outside the main try/finally: if it
        fails because another instance already owns the name (the WHISP-63
        race), we want a clean one-line log and a plain re-raise -- no
        traceback spam, and no _cleanup() of components that never did
        anything. The normal (CLI) path never reaches this branch because
        acquire_bus_name() is already called before this object is even
        constructed; this is a defensive fallback for direct callers/tests.
        """
        bus = self._bus if self._bus is not None else SessionBus()
        if self._name_owner is None:
            try:
                self._name_owner = acquire_bus_name(bus)
            except AlreadyRunningError as e:
                logger.error(str(e))
                raise

        try:
            # Register the object on the bus; the name is already ours.
            self._registration = bus.register_object(OBJECT_PATH, self, None)
            logger.info("D-Bus service published as org.fede.whisperaloud")

            # Inject bus into MPRIS controller now that we have one
            self._mpris.set_bus(bus)

            # Use GLib main loop
            loop = GLib.MainLoop()
            self._loop = loop

            # Register signal handlers via GLib (safe for D-Bus emission)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal_module.SIGTERM, self._signal_handler_glib)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal_module.SIGINT, self._signal_handler_glib)

            # WHISP-47 e2e harness hardening: under WHISPER_ALOUD_TEST_MODE=1
            # only, quit the main loop the moment the session bus connection
            # itself closes (e.g. the private dbus-run-session bus tears
            # down while this daemon is still up). Without this, a daemon
            # whose bus just vanished keeps running with nothing left to
            # talk to -- an orphan that outlives the test run that spawned
            # it. Production (no TEST_MODE) relies on the real session bus
            # staying up for the process lifetime, so this is never wired
            # outside test mode.
            if self._test_mode:
                try:
                    bus.con.connect("closed", lambda *a: loop.quit())
                except Exception as e:
                    logger.warning(f"WHISPER_ALOUD_TEST_MODE: failed to hook bus-closed shutdown: {e}")

            loop.run()

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Service failed to start: {e}")
            raise
        finally:
            self._cleanup()

    # ─── D-Bus Properties ───────────────────────────────────────────────

    @property
    def ApiVersion(self) -> str:
        """Semantic version of this D-Bus interface (see docs/dbus-api.md)."""
        return API_VERSION

    # ─── D-Bus Methods ───────────────────────────────────────────────────

    def StartRecording(self) -> bool:
        """Start audio recording."""
        if not self.recorder:
            self.Error("recorder_error", "Recorder not initialized")
            return False

        # A previous recording's transcription (batch transcribe_numpy, or a
        # streaming engine's finalize()) is still running on the executor.
        # Starting a new session now would call engine.start_session() (or
        # wire a fresh on_processed_chunk hook) while that in-flight job is
        # still touching the SAME engine instance's shared state -- for a
        # streaming engine this corrupts the session (a stale finalize() can
        # overwrite the new session's committed/tentative text, and a
        # straggler accept_chunk can raise against a session that's already
        # moved on). No new state is created here, so nothing needs undoing.
        if self._transcribing:
            logger.info("StartRecording: a transcription is still in flight, ignoring")
            return False

        try:
            rf = self.config.recording_flow

            # Pause media before recording
            if rf.pause_media:
                self._mpris.pause_all_playing()
                if rf.pre_pause_delay_ms > 0:
                    time.sleep(rf.pre_pause_delay_ms / 1000.0)

            # Raise mic gain before recording
            if rf.raise_mic_gain:
                self._gain.raise_to(rf.target_gain_linear)

            # WHISP-57: a fresh session_id per recording, regardless of
            # engine -- TranscriptionReady always carries it in meta so
            # clients can correlate (streaming or not). `is_streaming` is
            # checked with `is True` rather than truthiness: a MagicMock
            # test double for a *non*-streaming engine auto-creates a
            # truthy `.is_streaming` attribute, which would otherwise send
            # every mocked-engine test down the streaming path by accident.
            self._current_session_id = uuid.uuid4().hex
            is_streaming = getattr(self.transcriber, "is_streaming", False) is True
            self._streaming_session = is_streaming
            if is_streaming:
                self._ensure_model_loaded()
                self.transcriber.start_session(self.config.transcription.language)
                self.recorder.on_processed_chunk = self.transcriber.accept_chunk
                self._last_partial_seq = None
            else:
                self.recorder.on_processed_chunk = None

            self.recorder.start()
            if is_streaming:
                self._start_partial_timer()
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
            # Best-effort cleanup on start failure
            self._gain.restore()
            self._mpris.clear()
            if self._streaming_session:
                self._stop_partial_timer()
                try:
                    self.transcriber.cancel_session()
                except Exception as cancel_error:
                    logger.warning(f"engine cancel_session on start failure failed: {cancel_error}")
                self._streaming_session = False
            self.Error("recording_failed", str(e))
            return False

    def StopRecording(self) -> str:
        """Stop recording and start async transcription."""
        if not self.recorder or not self.transcriber:
            self.Error("component_error", "Components not initialized")
            return "error"

        try:
            self._stop_level_timer()
            self._stop_partial_timer()

            # Generate job ID for per-job window capture
            job_id = uuid.uuid4().hex

            # Capture active window for targeted paste
            if self.window_manager and self.config.clipboard.paste_to_captured_window:
                try:
                    captured = self.window_manager.get_active_window()
                    if captured:
                        self._captured_by_job[job_id] = captured
                        logger.info(
                            f"Captured window for job {job_id[:8]}: "
                            f"{captured.title} (id={captured.window_id})"
                        )
                except Exception as e:
                    logger.warning(f"Failed to capture window: {e}")

            # WHISP-65/57: AudioRecorder.dropped_chunks resets once its chunk
            # session is torn down, which stop() does internally -- it must be
            # read *before* stop(), not after, to report anything meaningful.
            dropped_chunks = self.recorder.dropped_chunks
            audio_data = self.recorder.stop()

            # Restore mic gain and resume media (try/finally ensures both run)
            rf = self.config.recording_flow
            try:
                self._gain.restore()
            except Exception as e:
                logger.warning(f"Gain restore failed: {e}")
            try:
                if rf.pause_media:
                    self._mpris.resume_ours(delay_ms=rf.post_resume_delay_ms)
            except Exception as e:
                logger.warning(f"Media resume failed: {e}")

            self.RecordingStopped()
            self.StatusChanged("transcribing")
            if self.indicator:
                self.indicator.set_state("transcribing")
            if self.notifications:
                self.notifications.show_recording_stopped()

            # Start transcription in background thread (non-blocking). For a
            # streaming engine, recorder.stop() above has already drained and
            # joined the chunk-hook worker (AudioRecorder.stop()'s documented
            # contract) before this submits engine.finalize() -- the WHISP-56
            # mandatory order: stop() happens-before finalize(), so finalize()
            # never races a still-in-flight accept_chunk().
            self._transcribing = True
            recording_session_id = self._current_session_id or ""
            # WHISP-73/74: a fresh job token, and a snapshot of the engine
            # instance to use for this job -- captured now, synchronously,
            # so a SetConfig() landing after this point can never change
            # which engine instance this job's executor thread calls (see
            # _transcribe_audio/_finalize_streaming_and_emit).
            self._job_id += 1
            token = self._job_id
            engine = self.transcriber
            if self._streaming_session:
                self.executor.submit(
                    self._finalize_streaming_and_emit,
                    audio_data, job_id, recording_session_id, dropped_chunks,
                    token, engine,
                )
            else:
                self.executor.submit(
                    self._transcribe_and_emit,
                    audio_data, job_id, recording_session_id, dropped_chunks,
                    token, engine,
                )

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
        # WHISP-73: invalidate whatever job token is currently live *before*
        # touching any other state, so a transcription job already in flight
        # (if any) can never again be mistaken for the current one, however
        # late its success/error emission eventually runs.
        self._job_id += 1

        if self.recorder and self.recorder.is_recording:
            self._stop_level_timer()
            self._stop_partial_timer()
            self.recorder.cancel()
            # WHISP-56 mandatory order: recorder.cancel() (which drains and
            # joins the chunk-hook worker, same as stop()) happens-before
            # engine.cancel_session() -- never touch the engine session while
            # an accept_chunk() could still be in flight.
            if self._streaming_session:
                try:
                    self.transcriber.cancel_session()
                except Exception as e:
                    logger.warning(f"engine cancel_session on cancel failed: {e}")
                self._streaming_session = False
            # Restore gain and resume media on cancel too
            try:
                self._gain.restore()
            except Exception as e:
                logger.warning(f"Gain restore on cancel failed: {e}")
            try:
                if self.config.recording_flow.pause_media:
                    self._mpris.resume_ours()
            except Exception as e:
                logger.warning(f"Media resume on cancel failed: {e}")
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
            return True

        if self._transcribing and self.transcriber:
            # Best-effort: the engine's own cancel_transcription() docstring
            # says this may only yield a partial result (or be ignored
            # entirely) -- the job token bump above is what actually
            # guarantees no stale TranscriptionReady/Error surfaces, this is
            # just a cooperative nudge to stop sooner.
            try:
                self.transcriber.cancel_transcription()
            except Exception as e:
                logger.warning(f"engine cancel_transcription on cancel failed: {e}")
            self._transcribing = False
            self._streaming_session = False
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
            # WHISP-74: the daemon is idle again now -- apply any model
            # reload that was deferred while this job was in flight.
            self._apply_pending_config_reload()
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

    def UpdateHistoryEntry(self, entry_id: int, text: str) -> bool:
        """Update the transcribed text of a history entry."""
        try:
            return self.history_manager.update_text(int(entry_id), str(text))
        except Exception as e:
            logger.error("UpdateHistoryEntry failed: %s", e)
            self.Error("history_update_failed", str(e))
            return False

    def GetConfig(self) -> dict:
        """Return current configuration flattened to GLib variants."""
        d = self.config.to_dict()
        flat = {}
        for section, values in d.items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                flat[f"{section}.{key}"] = value
        return _wrap_flat_config_as_variants(flat)

    def SetConfig(self, changes: dict) -> bool:
        """Apply configuration changes."""
        try:
            config_dict = self.config.to_dict()
            unpacked_changes = {}
            for key, variant in changes.items():
                # Unpack GLib.Variant to native Python value
                value = variant.unpack() if hasattr(variant, 'unpack') else variant
                unpacked_changes[key] = value
                section, field = key.split(".", 1)
                if section in config_dict and field in config_dict[section]:
                    config_dict[section][field] = value
            new_config = WhisperAloudConfig.from_dict(config_dict)
            new_config.validate()
            self._apply_config_changes(new_config)
            self.config.save()
            # ConfigChanged's D-Bus signature is a{sv} too -- a signal
            # emission is just as strict about GLib.Variant-wrapped values
            # as any other outgoing a{sv} argument (see
            # _wrap_flat_config_as_variants), so the unpacked native values
            # must be re-wrapped before emitting, not passed through as-is.
            self.ConfigChanged(_wrap_flat_config_as_variants(unpacked_changes))
            return True
        except Exception as e:
            self.Error("config_invalid", str(e))
            return False

    def ReloadConfig(self) -> bool:
        """Reload configuration from file and apply changes.

        Uses strict=True (WHISP-71): a malformed config.json must fail this
        explicit reload with reload_failed, not silently replace the live
        config with defaults (WhisperAloudConfig.load() raises
        ConfigurationError in that case, caught below like any other
        failure). self.config is only reassigned inside
        _apply_config_changes(), so a raise here leaves it untouched.
        """
        try:
            logger.info("Reloading configuration...")
            new_config = WhisperAloudConfig.load(strict=True)
            self._apply_config_changes(new_config)
            self.ConfigChanged(_wrap_flat_config_as_variants({}))
            logger.info("Configuration reloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to reload config: {e}")
            self.Error("reload_failed", str(e))
            return False

    def _apply_config_changes(self, new_config: 'WhisperAloudConfig') -> None:
        """Re-initialize components whose config has changed.

        WHISP-74: config is always persisted/applied immediately (`self.config
        = new_config` below still runs unconditionally, and SetConfig/
        ReloadConfig still emit ConfigChanged right away) -- only the engine
        *swap* is deferred when a recording or transcription is in flight, to
        avoid running an in-flight job's audio through a different engine
        instance mid-operation. See docs/dbus-api.md's SetConfig/ReloadConfig
        sections.
        """
        # Reload when any model-reload field changed (engine/name/device/compute_type).
        if detect_config_changes(self.config, new_config).requires_model_reload:
            busy = self._transcribing or (self.recorder is not None and self.recorder.is_recording)
            if busy:
                logger.info(
                    "Model config changed while recording/transcribing; "
                    "deferring engine reload until the daemon is idle"
                )
                self._pending_reload_config = new_config
            else:
                logger.info("Model config changed, reloading...")
                self.transcriber = create_engine(new_config)
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
                auto_stop_callback=self._on_recorder_auto_stop,
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

    def _apply_pending_config_reload(self) -> None:
        """Apply a model reload deferred by `_apply_config_changes()` while the
        daemon was busy (WHISP-74). Called once the daemon is back to idle --
        from `_emit_transcription_success`/`_emit_transcription_error`'s
        main-loop closures, and from `CancelRecording`'s transcribing branch.
        A no-op when nothing is pending. If two SetConfig()/ReloadConfig()
        calls requiring a reload landed while busy, `self._pending_reload_config`
        already holds only the latest one (each overwrites the previous), so
        the last one wins.
        """
        pending = self._pending_reload_config
        if pending is None:
            return
        self._pending_reload_config = None
        logger.info("Applying deferred model reload now that the daemon is idle")
        try:
            self.transcriber = create_engine(pending)
            self.transcriber.load_model()
            self._model_loaded = True
        except Exception as e:
            logger.error(f"Deferred model reload failed: {e}")
            self.Error("model_reload_failed", str(e))

    def Quit(self) -> bool:
        """Quit the service."""
        logger.info("Quit requested via D-Bus")
        self._shutdown = True
        # WHISP-73: invalidate any in-flight transcription job's token so its
        # success/error emission (if it still runs during/after _cleanup()'s
        # executor.shutdown(wait=True)) drops silently instead of emitting
        # TranscriptionReady/Error, touching the clipboard, or writing
        # history after the daemon has already announced shutdown.
        self._job_id += 1

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

    # ─── Partial-transcript polling (WHISP-57) ────────────────────────────

    def _start_partial_timer(self):
        """Start the PartialTranscript polling timer (streaming engines only)."""
        if self._partial_timer_id is None:
            self._partial_timer_id = GLib.timeout_add(
                PARTIAL_TRANSCRIPT_INTERVAL_MS, self._emit_partial_transcript
            )

    def _stop_partial_timer(self):
        """Stop the PartialTranscript polling timer."""
        if self._partial_timer_id is not None:
            GLib.source_remove(self._partial_timer_id)
            self._partial_timer_id = None

    def _emit_partial_transcript(self):
        """Poll the streaming engine for its latest partial (called at
        PARTIAL_TRANSCRIPT_HZ by GLib) and emit PartialTranscript only when
        `seq` has advanced since the last emission -- a raw partial that
        repeats the previous seq (the engine had nothing new to report) is
        not re-emitted.
        """
        try:
            partial = self.transcriber.get_partial()
        except Exception as e:
            logger.warning(f"get_partial failed: {e}")
            return True  # keep polling; a flaky poll shouldn't kill the timer

        if partial.seq != self._last_partial_seq:
            self._last_partial_seq = partial.seq
            self.PartialTranscript(
                self._current_session_id or "", partial.committed, partial.tentative, partial.seq
            )
        return True  # Keep timer running

    # ─── Auto-stop handler ───────────────────────────────────────────────

    def _on_recorder_auto_stop(self, audio_data) -> None:
        """Called by AudioRecorder when max_recording_duration is reached.

        Called from a daemon thread — marshal all GLib/D-Bus work to the main loop.
        """
        # Capture window immediately (before marshalling to main thread)
        job_id = uuid.uuid4().hex
        if self.window_manager and self.config.clipboard.paste_to_captured_window:
            try:
                captured = self.window_manager.get_active_window()
                if captured:
                    self._captured_by_job[job_id] = captured
                    logger.info(
                        f"Captured window for auto-stop job {job_id[:8]}: "
                        f"{captured.title} (id={captured.window_id})"
                    )
            except Exception as e:
                logger.warning(f"Failed to capture window on auto-stop: {e}")

        def _on_main_thread():
            logger.info("Auto-stop triggered by max recording duration")
            self._stop_level_timer()
            self._stop_partial_timer()
            rf = self.config.recording_flow
            try:
                self._gain.restore()
            except Exception as e:
                logger.warning(f"Gain restore on auto-stop failed: {e}")
            try:
                if rf.pause_media:
                    self._mpris.resume_ours(delay_ms=rf.post_resume_delay_ms)
            except Exception as e:
                logger.warning(f"Media resume on auto-stop failed: {e}")

            self.RecordingStopped()
            self.StatusChanged("transcribing")
            if self.indicator:
                self.indicator.set_state("transcribing")
            if self.notifications:
                self.notifications.show_recording_stopped()

            self._transcribing = True
            recording_session_id = self._current_session_id or ""
            # Unlike StopRecording, AudioRecorder.stop() has already run (inside
            # AudioRecorder._auto_stop(), before this callback fires), so
            # recorder.dropped_chunks has already reset to 0 by the time we could
            # read it -- a known gap, not fixable from here without changing
            # AudioRecorder's auto-stop callback contract (WHISP-65).
            dropped_chunks = 0
            # WHISP-73/74: same job token + engine snapshot as StopRecording.
            self._job_id += 1
            token = self._job_id
            engine = self.transcriber
            if self._streaming_session:
                self.executor.submit(
                    self._finalize_streaming_and_emit,
                    audio_data, job_id, recording_session_id, dropped_chunks,
                    token, engine,
                )
            else:
                self.executor.submit(
                    self._transcribe_and_emit,
                    audio_data, job_id, recording_session_id, dropped_chunks,
                    token, engine,
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_on_main_thread)

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
        self._stop_partial_timer()
        self._shutdown = True
        if hasattr(self, '_loop') and self._loop:
            self._loop.quit()
        return GLib.SOURCE_REMOVE

    # ─── Internal helpers ────────────────────────────────────────────────

    def _transcribe_audio(self, audio_data, engine: Optional[SpeechEngine] = None):
        """Transcribe audio data (runs in thread).

        WHISP-74: `engine` is a snapshot of `self.transcriber` taken at job
        submission time (see StopRecording/_on_recorder_auto_stop), not a
        fresh read of `self.transcriber` here -- a SetConfig() that reloads
        the model while this job is in flight must never redirect this call
        to a different engine instance mid-operation. `_ensure_model_loaded()`
        still operates on `self.transcriber`, which cannot differ from
        `engine` for the duration of this job: `_apply_config_changes()`
        defers any engine swap until the daemon is idle again.
        """
        engine = engine if engine is not None else self.transcriber
        self._ensure_model_loaded()
        return engine.transcribe_numpy(audio_data)

    def _transcribe_and_emit(
        self,
        audio_data,
        job_id: str = "",
        recording_session_id: str = "",
        dropped_chunks: int = 0,
        token: Optional[int] = None,
        engine: Optional[SpeechEngine] = None,
    ) -> None:
        """Batch (non-streaming) path: transcribe audio and emit completion
        signal (runs in thread)."""
        engine = engine if engine is not None else self.transcriber
        try:
            result = self._transcribe_audio(audio_data, engine)
        except Exception as e:
            self._emit_transcription_error(e, token)
            return
        self._emit_transcription_success(
            result, audio_data, job_id, recording_session_id, dropped_chunks, token
        )

    def _finalize_streaming_and_emit(
        self,
        audio_data,
        job_id: str = "",
        recording_session_id: str = "",
        dropped_chunks: int = 0,
        token: Optional[int] = None,
        engine: Optional[SpeechEngine] = None,
    ) -> None:
        """Streaming path: `engine.finalize()` then the same post-processing as
        the batch path (runs in thread, submitted to the executor only after
        StopRecording's/auto-stop's synchronous recorder.stop()/cancel() has
        already drained and joined the chunk-hook worker -- the WHISP-56
        mandatory order, so finalize() never races an in-flight accept_chunk()).
        `audio_data` is the full recorded buffer from recorder.stop() (collected
        independently of the streaming hook), used for history exactly like the
        batch path. `engine` is the snapshot captured at submission time
        (WHISP-74), the same engine instance that handled this session's
        start_session()/accept_chunk() calls.
        """
        engine = engine if engine is not None else self.transcriber
        try:
            result = engine.finalize()
        except Exception as e:
            self._emit_transcription_error(e, token)
            return
        self._emit_transcription_success(
            result, audio_data, job_id, recording_session_id, dropped_chunks, token
        )

    def _is_job_stale(self, token: Optional[int]) -> bool:
        """True when `token` no longer matches the current job (WHISP-73):
        CancelRecording()/Quit() bump `self._job_id`, so a job submitted
        before that point recognizes itself as superseded. `token=None`
        (not supplied) is never considered stale, for callers outside the
        job-submission path."""
        return token is not None and token != self._job_id

    def _emit_transcription_error(self, exc: Exception, token: Optional[int] = None) -> None:
        """Shared failure path for both the batch and streaming transcription
        routes. All D-Bus signal emissions are marshalled to the GLib main
        loop via idle_add for thread safety.

        WHISP-73: if `token` is stale (the job was cancelled, or the daemon
        is quitting) by the time this runs, the error is logged and dropped:
        no `Error` signal, no notification. The daemon already declared
        itself idle synchronously when it invalidated the token, so nothing
        here should contradict that.
        """
        error_message = str(exc)
        logger.error(f"Transcription failed: {error_message}")

        def _emit_error():
            if self._is_job_stale(token):
                logger.debug(
                    f"Dropping stale transcription error for job token {token} "
                    f"(current {self._job_id})"
                )
                return False
            self._transcribing = False
            self._streaming_session = False
            self.StatusChanged("idle")
            if self.indicator:
                self.indicator.set_state("idle")
            self.Error("transcription_failed", error_message)
            if self.notifications:
                self.notifications.show_error(error_message)
            # WHISP-74: a model reload deferred while this job was in flight
            # can now be applied.
            self._apply_pending_config_reload()
            return False

        GLib.idle_add(_emit_error)

    def _emit_transcription_success(
        self,
        result,
        audio_data,
        job_id: str,
        recording_session_id: str,
        dropped_chunks: int = 0,
        token: Optional[int] = None,
    ) -> None:
        """Shared success path for both the batch and streaming transcription
        routes: save history, emit TranscriptionReady (with `session_id` in
        meta -- WHISP-57, for client-side correlation with PartialTranscript --
        and `dropped_chunks`, the chunk-hook queue's drop count for this
        session, 0 when no hook was registered), then clipboard copy/paste and
        notifications. All D-Bus signal emissions and indicator updates are
        marshalled to the GLib main loop via idle_add for thread safety.

        WHISP-73: checked twice -- once here (on the executor thread, before
        the history write) and again inside the idle_add closure (on the main
        loop, right before any signal/clipboard/paste) -- because `token` can
        go stale in the gap between the two (CancelRecording()/Quit() run on
        whatever thread calls them, not marshalled through idle_add). A stale
        job emits nothing: no TranscriptionReady, no clipboard copy/paste, no
        history row.
        """
        if self._is_job_stale(token):
            logger.debug(
                f"Dropping stale transcription result for job token {token} "
                f"(current {self._job_id}); no history will be written"
            )
            return

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
            "session_id": GLib.Variant("s", recording_session_id or ""),
            "dropped_chunks": GLib.Variant("u", max(0, int(dropped_chunks))),
        }

        def _emit_success():
            if self._is_job_stale(token):
                logger.debug(
                    f"Dropping stale transcription result for job token {token} "
                    f"(current {self._job_id}); already dropped before history write"
                )
                return False
            self._transcribing = False
            self._streaming_session = False
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
            if (self.config.clipboard.auto_copy
                    and self.config.clipboard.auto_paste
                    and not self._test_mode
                    and self.clipboard_manager
                    and result.text):
                try:
                    from ..clipboard import PasteSimulator
                    import threading as _threading
                    simulator = PasteSimulator(
                        self.clipboard_manager._session_type,
                        self.config.clipboard,
                    )
                    captured = self._captured_by_job.pop(job_id, None)

                    def _do_paste():
                        if captured and self.config.clipboard.paste_to_captured_window:
                            try:
                                self.window_manager.activate_window(captured)
                                time.sleep(self.config.clipboard.paste_delay_ms / 1000.0)
                            except Exception as e:
                                logger.warning(f"Failed to activate captured window: {e}")
                        simulator.simulate_paste()

                    _threading.Thread(target=_do_paste, daemon=True).start()
                    logger.info("Auto-paste triggered")
                except Exception as e:
                    logger.warning(f"Failed to trigger auto-paste: {e}")
            if self.notifications:
                self.notifications.show_transcription_completed(result.text)
            # WHISP-74: a model reload deferred while this job was in flight
            # can now be applied.
            self._apply_pending_config_reload()
            logger.info("Transcription completed and signals emitted")
            return False

        GLib.idle_add(_emit_success)

    def _cleanup(self) -> None:
        """Clean up resources."""
        logger.info("Cleaning up service resources")

        self._stop_level_timer()
        self._stop_partial_timer()

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
