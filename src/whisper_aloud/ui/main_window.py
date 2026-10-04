"""Main application window for WhisperAloud."""

import logging
import threading
from pathlib import Path
from typing import Optional

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from ..config import WhisperAloudConfig
from ..daemon_hints import gui_daemon_unavailable_hint
from ..sandbox import is_flatpak_sandbox
from ..service.history_client import DaemonHistoryManager
from .daemon_connect_logic import ConnectPhase, decide_after_attempt
from .error_handler import (
    handle_model_load_error,
)
from .file_transcription_logic import (
    file_dialog_filter_patterns,
    is_stale_job_response,
    pick_first_supported_path,
    status_label_for_file_job,
    transcribe_options_for_language,
)
from .history_panel import HistoryPanel
from .level_meter import LevelMeterPanel
from .main_window_logic import (
    describe_model_state,
    is_daemon_interaction_ready,
    resolve_daemon_status_state,
    resolve_language_change,
    resolve_model_state,
    should_enter_transcribing,
    should_restore_transcribing_after_cancel,
    transcribing_status_text,
)
from .partial_view_logic import PartialViewState
from .settings_dialog import SettingsDialog
from .shortcuts_window import ShortcutsWindow
from .sound_feedback import SoundFeedback
from .status_bar import StatusBar
from .utils import AppState, format_duration

logger = logging.getLogger(__name__)


class MainWindow(Gtk.ApplicationWindow):
    """Main application window with recording and transcription controls."""

    def __init__(self, application: Gtk.Application) -> None:
        """
        Initialize the main window.

        Args:
            application: The parent application
        """
        super().__init__(application=application)
        logger.info("Initializing main window")

        # Application state
        self._state: AppState = AppState.IDLE
        self._model_loading: bool = False
        # Daemon model readiness (API 1.6.0 ModelStateChanged / GetStatus
        # "model_state"); "" until reported (or for an older daemon).
        self._daemon_model_state: str = ""
        self._timer_active: bool = False
        self._timer_seconds: int = 0
        self._daemon_available: bool = False
        # 1-indexed count of D-Bus activation attempts made since the last
        # success or manual retry click; drives daemon_connect_logic's
        # auto-retry-with-backoff decision (WHISP-60).
        self._connect_attempt: int = 0
        self._retry_timeout_id: int = 0
        # The currently-open "daemon unavailable" dialog (field bug 4), or
        # None. Shared by _on_daemon_unavailable() and _handle_disconnection()
        # via _show_daemon_unavailable_dialog() so a vanished daemon offers
        # the same actionable retry as a daemon that was never reachable.
        self._unavailable_dialog: Optional[Gtk.Window] = None
        self._current_entry_id: Optional[int] = None
        self._edit_save_timer_id: int = 0
        self._buffer_changed_handler_id: int = 0

        # WHISP-96: the job_id of an in-flight TranscribeFile() call, or None.
        # Entirely independent of self._state/AppState -- a file transcription
        # never changes the mic-recording state machine.
        self._file_job_id: Optional[str] = None

        # Live partial-transcript view state (WHISP-58); only advanced by a
        # streaming engine's PartialTranscript signal, see partial_view_logic.py
        self._partial_view = PartialViewState()

        # D-Bus client (replaces direct component access)
        from ..service.client import WhisperAloudClient
        self.client: Optional[WhisperAloudClient] = None

        # Local config for UI settings (language dropdown, settings dialog)
        self.config: Optional[WhisperAloudConfig] = None

        # Daemon-backed history manager adapter (all data via D-Bus)
        self.history_manager: Optional[DaemonHistoryManager] = None

        # "Start in background at login" (WHISP-87). Lives as long as the
        # GUI, not the settings dialog: a Background portal answer can
        # arrive after the dialog was closed. Created on first use.
        self._autostart_controller = None

        # Sound feedback (initialized early as it's lightweight)
        self.sound_feedback = SoundFeedback(enabled=True)

        # Build UI
        self._build_ui()

        # Set window properties
        self.set_title("WhisperAloud")
        self.set_default_size(900, 600)
        self.add_css_class("wa-app-window")
        self.set_titlebar(self.header_bar)  # Use custom header bar

        # Set up keyboard shortcuts
        self._setup_keyboard_shortcuts()

        # Connect to daemon in background
        GLib.idle_add(self._init_components_async)

        logger.info("Main window initialized")

    def _build_ui(self) -> None:
        """Build the user interface."""
        # Create main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # Create header bar
        self.header_bar = Gtk.HeaderBar()
        self.header_bar.set_show_title_buttons(True)
        self.header_bar.add_css_class("wa-headerbar")

        # History toggle button
        self.history_toggle = Gtk.ToggleButton()
        self.history_toggle.set_icon_name("sidebar-show-symbolic")
        self.history_toggle.set_tooltip_text("Toggle History")
        self.history_toggle.add_css_class("wa-toolbar-btn")
        self.history_toggle.set_active(True)
        self.history_toggle.connect("toggled", self._on_history_toggled)
        self.header_bar.pack_start(self.history_toggle)

        # Transcribe file... button (WHISP-96)
        self.transcribe_file_button = Gtk.Button()
        self.transcribe_file_button.set_icon_name("document-open-symbolic")
        self.transcribe_file_button.set_tooltip_text("Transcribe file… (Ctrl+O)")
        self.transcribe_file_button.add_css_class("wa-toolbar-btn")
        self.transcribe_file_button.connect("clicked", self._on_transcribe_file_clicked)
        self.header_bar.pack_start(self.transcribe_file_button)

        # Help/Shortcuts button
        help_button = Gtk.Button()
        help_button.set_icon_name("help-about-symbolic")
        help_button.set_tooltip_text("Keyboard Shortcuts (F1)")
        help_button.add_css_class("wa-toolbar-btn")
        help_button.connect("clicked", self._on_help_clicked)
        self.header_bar.pack_end(help_button)

        # Settings button
        self.settings_button = Gtk.Button()
        self.settings_button.set_icon_name("preferences-system-symbolic")
        self.settings_button.set_tooltip_text("Settings (Ctrl+,)")
        self.settings_button.add_css_class("wa-toolbar-btn")
        self.settings_button.set_sensitive(False)
        self.settings_button.connect("clicked", self._on_settings_clicked)
        self.header_bar.pack_end(self.settings_button)

        # Language selector dropdown
        self._language_codes = ["auto", "en", "es", "fr", "de", "it", "pt", "ja", "zh", "ko", "ru", "ar", "hi"]
        self._language_labels = ["Auto", "English", "Espa\u00f1ol", "Fran\u00e7ais", "Deutsch",
                                  "Italiano", "Portugu\u00eas", "\u65e5\u672c\u8a9e", "\u4e2d\u6587", "\ud55c\uad6d\uc5b4", "\u0420\u0443\u0441\u0441\u043a\u0438\u0439", "\u0627\u0644\u0639\u0631\u0628\u064a\u0629", "\u0939\u093f\u0928\u094d\u0926\u0940"]
        self.lang_dropdown = Gtk.DropDown.new_from_strings(self._language_labels)
        self.lang_dropdown.set_tooltip_text("Select transcription language")
        self.lang_dropdown.add_css_class("wa-language")
        self.lang_dropdown.set_selected(0)  # Default to Auto, will be updated when config loads

        self.lang_dropdown.connect("notify::selected", self._on_language_changed)
        self.header_bar.pack_end(self.lang_dropdown)

        # Main content area (Paned)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_position(225)  # ~25% of 900px default width
        self.paned.set_wide_handle(True)
        self.paned.set_shrink_start_child(False)
        self.paned.set_shrink_end_child(False)
        self.paned.set_resize_start_child(True)   # history grows proportionally
        self.paned.set_resize_end_child(True)     # controls grow proportionally
        main_box.append(self.paned)

        # Left side: Recording and Transcription
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Recording panel
        recording_panel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        recording_panel_box.add_css_class("wa-surface")
        recording_panel_box.set_margin_start(24)
        recording_panel_box.set_margin_end(24)
        recording_panel_box.set_margin_top(10)
        recording_panel_box.set_margin_bottom(10)
        recording_panel_box.set_halign(Gtk.Align.CENTER)  # Center the recording controls

        # Record button
        self.record_button = Gtk.Button(label="Start Recording")
        self.record_button.add_css_class("suggested-action")
        self.record_button.add_css_class("pill")
        self.record_button.add_css_class("wa-primary-action")
        self.record_button.set_size_request(200, 44)  # Fixed width for consistent look
        self.record_button.set_sensitive(False)  # Disabled until daemon connects
        self.record_button.set_tooltip_text("Start/stop recording (Space)")
        self.record_button.connect("clicked", self._on_record_button_clicked)
        recording_panel_box.append(self.record_button)

        # Cancel button (shown during transcription)
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.add_css_class("pill")
        self.cancel_button.add_css_class("wa-primary-action")
        self.cancel_button.set_size_request(200, 44)  # Same size as record button
        self.cancel_button.set_visible(False)  # Hidden by default
        self.cancel_button.set_tooltip_text("Cancel transcription (Ctrl+X)")
        self.cancel_button.connect("clicked", self._on_cancel_clicked)
        recording_panel_box.append(self.cancel_button)

        # Timer label
        self.timer_label = Gtk.Label(label="0:00")
        self.timer_label.add_css_class("title-2")
        self.timer_label.set_margin_top(4)
        recording_panel_box.append(self.timer_label)

        # Level meter
        self.level_meter = LevelMeterPanel()
        self.level_meter.set_margin_top(4)
        self.level_meter.set_size_request(280, -1)  # Fixed width for level meter
        recording_panel_box.append(self.level_meter)

        left_box.append(recording_panel_box)

        # Separator
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Transcription view
        transcription_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        transcription_box.add_css_class("wa-surface")
        transcription_box.set_margin_start(16)
        transcription_box.set_margin_end(16)
        transcription_box.set_margin_top(12)
        transcription_box.set_margin_bottom(12)
        transcription_box.set_vexpand(True)

        # Scrolled window with text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.add_css_class("wa-output-wrap")
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.text_view = Gtk.TextView()
        self.text_view.add_css_class("wa-output")
        self.text_view.set_editable(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.set_margin_start(12)
        self.text_view.set_margin_end(12)
        self.text_view.set_margin_top(12)
        self.text_view.set_margin_bottom(12)
        self._buffer_changed_handler_id = self.text_view.get_buffer().connect(
            "changed", self._on_text_buffer_changed
        )

        scrolled.set_child(self.text_view)
        transcription_box.append(scrolled)

        # Button box
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)

        self.copy_button = Gtk.Button(label="Copy to Clipboard")
        self.copy_button.add_css_class("wa-ghost")
        self.copy_button.set_sensitive(False)
        self.copy_button.set_tooltip_text("Copy transcription to clipboard (Ctrl+C)")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        button_box.append(self.copy_button)

        self.clear_button = Gtk.Button(label="Clear")
        self.clear_button.add_css_class("wa-ghost")
        self.clear_button.set_sensitive(False)
        self.clear_button.set_tooltip_text("Clear transcription text (Escape)")
        self.clear_button.connect("clicked", self._on_clear_clicked)
        button_box.append(self.clear_button)

        transcription_box.append(button_box)
        left_box.append(transcription_box)

        # Left side: History Panel (placeholder until initialized)
        self.history_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.history_container.set_size_request(180, -1)
        self.paned.set_start_child(self.history_container)

        # Right side: Main recording/transcription panel
        left_box.set_size_request(300, -1)  # Minimum width for main content
        self.paned.set_end_child(left_box)

        # Status bar
        self.status_bar = StatusBar()
        # "Model missing — Download": the download itself (with license
        # consent and progress) lives in Settings' model row.
        self.status_bar.model_download_button.connect("clicked", self._on_settings_clicked)
        main_box.append(self.status_bar)

        # Drag-and-drop a file anywhere on the window to transcribe it
        # (WHISP-96). Gdk.FileList covers both plain local drops and
        # document-portal-mounted paths under Flatpak -- Gio.File.get_path()
        # resolves either.
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_file_drop)
        self.add_controller(drop_target)

        logger.debug("UI built successfully")

    def _setup_keyboard_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Create event controller for key press
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        logger.debug("Keyboard shortcuts configured")

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: object
    ) -> bool:
        """
        Handle key press events.

        Args:
            controller: Event controller
            keyval: Key value
            keycode: Hardware keycode
            state: Modifier state

        Returns:
            True if event was handled
        """
        from gi.repository import Gdk

        # Check for Ctrl modifier
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0

        # Ctrl+C: Copy to clipboard
        if ctrl_pressed and keyval == Gdk.KEY_c:
            if self.copy_button.get_sensitive():
                self._copy_to_clipboard()
                return True

        # Space: Toggle recording (if not in transcribing state)
        if keyval == Gdk.KEY_space:
            if self.record_button.get_sensitive():
                self.record_button.emit("clicked")
                return True

        # Escape: Clear text (if in ready state)
        if keyval == Gdk.KEY_Escape:
            if self.clear_button.get_sensitive():
                self._on_clear_clicked(None)
                return True

        # Ctrl+X: Cancel transcription (if in transcribing state)
        if ctrl_pressed and keyval == Gdk.KEY_x:
            if self._state == AppState.TRANSCRIBING:
                self._on_cancel_clicked(None)
                return True

        # F1: Show keyboard shortcuts
        if keyval == Gdk.KEY_F1:
            self._on_help_clicked(None)
            return True

        # Ctrl+,: Open settings
        if ctrl_pressed and keyval == Gdk.KEY_comma:
            self._on_settings_clicked(None)
            return True

        # Ctrl+O: Transcribe file... (WHISP-96)
        if ctrl_pressed and keyval == Gdk.KEY_o:
            self._on_transcribe_file_clicked(None)
            return True

        return False

    # ─── Daemon connection ────────────────────────────────────────────────

    def _init_components_async(self) -> bool:
        """
        Connect to daemon asynchronously.

        Each call is one D-Bus activation attempt (see service/client.py's
        WhisperAloudClient -- it never spawns a daemon subprocess itself; the
        first D-Bus call is what makes dbus-daemon auto-start the service if
        an activation file is installed). Returns:
            False to remove this idle callback
        """
        self._connect_attempt += 1
        attempt = self._connect_attempt
        self._show_loading_dialog()

        def _connect_in_thread():
            """Connect to daemon in background thread."""
            try:
                from ..service.client import WhisperAloudClient
                client = WhisperAloudClient()
                if client.is_connected:
                    GLib.idle_add(self._on_daemon_connected, client)
                else:
                    GLib.idle_add(
                        self._on_connect_attempt_failed, attempt, client.is_starting
                    )
            except Exception as e:
                logger.error(f"Failed to connect to daemon: {e}", exc_info=True)
                GLib.idle_add(self._on_load_error, str(e))

        threading.Thread(target=_connect_in_thread, daemon=True).start()
        return False

    def _on_connect_attempt_failed(self, attempt: int, starting: bool = False) -> bool:
        """
        Called on the main thread after a failed D-Bus activation attempt.

        Delegates to the pure decide_after_attempt() policy (WHISP-60/field
        bug 1): either schedule another automatic attempt with backoff, or
        give up and show the "daemon unavailable" state with a manual retry
        button. `starting` (from WhisperAloudClient.is_starting) means the
        bus name is owned but the daemon hasn't published its D-Bus object
        yet -- a much longer, steady retry budget applies in that case (see
        daemon_connect_logic.DEFAULT_STARTING_MAX_AUTO_RETRIES) instead of
        the short exponential-backoff one used for a plain "nothing there at
        all" failure.

        Returns:
            False to remove this idle callback
        """
        decision = decide_after_attempt(connected=False, attempt=attempt, starting=starting)

        if decision.phase is ConnectPhase.STARTING:
            self._hide_loading_dialog()
            self.status_bar.set_status("Daemon is starting, please wait...")
            self._retry_timeout_id = GLib.timeout_add(
                int(decision.retry_delay_seconds * 1000), self._init_components_async
            )
        elif decision.phase is ConnectPhase.WAITING_RETRY:
            self._hide_loading_dialog()
            self.status_bar.set_status(
                f"Daemon not available, retrying in {decision.retry_delay_seconds:.0f}s..."
            )
            self._retry_timeout_id = GLib.timeout_add(
                int(decision.retry_delay_seconds * 1000), self._init_components_async
            )
        else:
            self._on_daemon_unavailable()

        return False

    def _on_daemon_connected(self, client) -> bool:
        """
        Called when daemon connection succeeds (main thread).

        Args:
            client: Connected WhisperAloudClient instance

        Returns:
            False to remove this idle callback
        """
        self.client = client
        self._daemon_available = True
        self._close_unavailable_dialog()
        logger.info("Connected to WhisperAloud daemon")

        # Subscribe to daemon signals
        self.client.on_status_changed(self._on_daemon_status_changed)
        self.client.on_transcription_ready(self._on_daemon_transcription_ready)
        self.client.on_level_update(self._on_daemon_level_update)
        self.client.on_error(self._on_daemon_error)
        self.client.on_partial_transcript(self._on_daemon_partial_transcript)
        # Subscribe before reading GetStatus so no transition is missed.
        self.client.on_model_state_changed(self._on_daemon_model_state_changed)
        self._refresh_model_state_from_status()

        # WHISP-96: file transcription job signals.
        self.client.on_file_transcription_progress(
            self._on_daemon_file_transcription_progress
        )
        self.client.on_file_transcription_ready(self._on_daemon_file_transcription_ready)
        self.client.on_file_transcription_error(self._on_daemon_file_transcription_error)

        # Watch for daemon restart (auto-reconnect)
        self.client.watch_name(
            on_connected=self._on_daemon_reconnected,
            on_disconnected=self._on_daemon_lost,
        )

        # Load local config for UI settings
        try:
            self.config = WhisperAloudConfig.load()
        except Exception as e:
            logger.warning(f"Failed to load local config, using defaults: {e}")
            self.config = WhisperAloudConfig()

        # Initialize daemon-backed history adapter for the history panel
        self.history_manager = DaemonHistoryManager(self.client)

        # Hide loading dialog
        self._hide_loading_dialog()

        # Update UI state
        self.status_bar.set_status("Ready")
        self.record_button.set_sensitive(True)
        self.settings_button.set_sensitive(True)
        self.set_state(AppState.IDLE)

        # Update language dropdown from config
        if self.config and self.config.transcription.language:
            current_lang = self.config.transcription.language
            if current_lang in self._language_codes:
                self.lang_dropdown.set_selected(self._language_codes.index(current_lang))

        # Initialize History Panel with daemon-backed history data
        if self.history_manager:
            self.history_panel = HistoryPanel(self.history_manager)
            self.history_panel.connect("entry-selected", self._on_history_entry_selected)
            self.history_container.append(self.history_panel)

        # Update model info display
        self._update_model_info()

        return False

    def _on_daemon_reconnected(self) -> None:
        """Called when daemon reappears on the bus after a crash/restart."""
        logger.info("Daemon reappeared, reconnecting...")
        GLib.idle_add(self._handle_reconnection)

    def _handle_reconnection(self) -> bool:
        """Re-establish daemon connection on the main thread."""
        self._daemon_available = True
        self._close_unavailable_dialog()
        self.status_bar.set_status("Reconnecting...")
        daemon_state = "idle"

        if self.client and self.client.is_connected:
            try:
                status = self.client.get_status()
                daemon_state = resolve_daemon_status_state(status)
                self._apply_model_state(*resolve_model_state(status))
            except Exception as e:
                logger.debug("Failed to refresh daemon status on reconnect: %s", e)

        try:
            self.config = WhisperAloudConfig.load()
            self._update_model_info()
        except Exception:
            pass

        self.record_button.set_sensitive(True)
        self.settings_button.set_sensitive(True)
        self._handle_status_change(daemon_state, force=True)
        if daemon_state == "idle":
            self.status_bar.set_status("Ready")
        logger.info("Reconnected to daemon after restart (state=%s)", daemon_state)
        return False

    def _on_daemon_lost(self) -> None:
        """Called when daemon vanishes from the bus (crash or quit)."""
        logger.warning("Daemon connection lost")
        GLib.idle_add(self._handle_disconnection)

    def _handle_disconnection(self) -> bool:
        """Update UI for lost daemon connection on the main thread.

        Field bug 4: previously this only set a transient, non-clickable
        status message ("Daemon disconnected - waiting for restart...") and
        relied entirely on watch_name()'s NameOwnerChanged callback to
        reconnect -- if nothing else on the bus restarted the daemon (e.g.
        the user ran `whisper-aloud quit` with no systemd auto-restart), the
        GUI gave the user no way to restart it. Show the same "daemon
        unavailable" dialog _on_daemon_unavailable() uses, with a working
        Retry Connection button, since retrying is also what triggers D-Bus
        activation (and the bug-1 startup wait) to bring the daemon back.
        """
        self._daemon_available = False
        self._timer_active = False
        self.record_button.set_sensitive(False)
        self.settings_button.set_sensitive(False)
        self.status_bar.set_status("Daemon disconnected")
        self.set_state(AppState.ERROR)
        self._show_daemon_unavailable_dialog("WhisperAloud daemon disconnected")
        return False

    def _on_daemon_unavailable(self) -> bool:
        """
        Called when daemon is not running (main thread).

        Returns:
            False to remove this idle callback
        """
        self._daemon_available = False
        self._timer_active = False
        self._hide_loading_dialog()
        logger.warning("WhisperAloud daemon is not running")

        self.status_bar.set_status("Daemon not running")
        self.settings_button.set_sensitive(False)
        self.set_state(AppState.ERROR)

        self._show_daemon_unavailable_dialog("WhisperAloud daemon not running")

        return False

    def _show_daemon_unavailable_dialog(self, title_text: str) -> None:
        """Present the "daemon unavailable" dialog with a Retry Connection
        button, reused by both _on_daemon_unavailable() (never connected)
        and _handle_disconnection() (field bug 4: daemon vanished after
        being connected). If the dialog is already open (e.g. a second
        disconnection event while the user hasn't dismissed it yet), just
        bring the existing one to the front instead of stacking a duplicate.
        """
        if self._unavailable_dialog is not None:
            self._unavailable_dialog.present()
            return

        dialog = Gtk.Window()
        dialog.set_modal(True)
        dialog.set_transient_for(self)
        dialog.set_title("WhisperAloud - Daemon Not Running")
        dialog.set_default_size(450, 200)
        dialog.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)

        title = Gtk.Label(label=title_text)
        title.add_css_class("title-2")
        box.append(title)

        has_user_service = (Path.home() / ".config/systemd/user/whisper-aloud.service").exists()
        hint_text = gui_daemon_unavailable_hint(
            is_flatpak=is_flatpak_sandbox(), has_user_service=has_user_service
        )

        hint = Gtk.Label(label=hint_text)
        hint.set_wrap(True)
        hint.set_selectable(True)
        hint.set_justify(Gtk.Justification.LEFT)
        box.append(hint)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.END)

        retry_button = Gtk.Button(label="Retry Connection")
        retry_button.add_css_class("suggested-action")
        retry_button.connect("clicked", lambda btn: self._retry_daemon_connection(dialog))
        button_box.append(retry_button)

        box.append(button_box)
        dialog.set_child(box)

        def _on_close_request(_win) -> bool:
            self._unavailable_dialog = None
            return False  # allow the close to proceed

        dialog.connect("close-request", _on_close_request)

        self._unavailable_dialog = dialog
        dialog.present()

    def _close_unavailable_dialog(self) -> None:
        """Close and forget the "daemon unavailable" dialog, if open."""
        if self._unavailable_dialog is not None:
            self._unavailable_dialog.close()
            self._unavailable_dialog = None

    def _retry_daemon_connection(self, dialog: Gtk.Window) -> None:
        """
        Retry connecting to the daemon.

        A manual click starts a fresh auto-retry burst (see
        daemon_connect_logic.decide_after_attempt): the attempt counter resets
        to 0 so the user gets the full automatic-backoff budget again, not an
        immediate re-exhaustion back to the unavailable dialog.

        Args:
            dialog: The unavailable dialog to close on success
        """
        dialog.close()
        self._connect_attempt = 0
        GLib.idle_add(self._init_components_async)

    def _show_loading_dialog(self) -> None:
        """Show a loading dialog during daemon connection."""
        self._loading_dialog = Gtk.Window()
        self._loading_dialog.set_modal(True)
        self._loading_dialog.set_transient_for(self)
        self._loading_dialog.set_title("WhisperAloud")
        self._loading_dialog.set_default_size(400, 150)
        self._loading_dialog.set_resizable(False)
        self._loading_dialog.set_deletable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(24)
        box.set_margin_bottom(24)

        msg = Gtk.Label(label="Connecting to WhisperAloud daemon...")
        msg.add_css_class("title-3")
        box.append(msg)

        # Progress bar (indeterminate/pulsing)
        self._loading_progress = Gtk.ProgressBar()
        self._loading_progress.set_show_text(False)
        box.append(self._loading_progress)

        self._loading_dialog.set_child(box)
        self._loading_dialog.present()

        # Start progress bar animation
        self._loading_pulse_id = GLib.timeout_add(100, self._pulse_loading_progress)

    def _pulse_loading_progress(self) -> bool:
        """Pulse the loading progress bar."""
        if hasattr(self, '_loading_progress') and self._loading_progress:
            self._loading_progress.pulse()
            return True  # Continue pulsing
        return False  # Stop if progress bar no longer exists

    def _hide_loading_dialog(self) -> None:
        """Hide and destroy the loading dialog."""
        # Stop the pulse animation
        if hasattr(self, '_loading_pulse_id') and self._loading_pulse_id:
            GLib.source_remove(self._loading_pulse_id)
            self._loading_pulse_id = None

        # Close the dialog
        if hasattr(self, '_loading_dialog') and self._loading_dialog:
            self._loading_dialog.close()
            self._loading_dialog = None
            self._loading_progress = None

    def _on_load_error(self, error_msg: str) -> bool:
        """
        Called when daemon connection fails with an exception (main thread).

        Args:
            error_msg: Error message

        Returns:
            False to remove this idle callback
        """
        self._daemon_available = False
        self._timer_active = False
        self._hide_loading_dialog()

        logger.error(f"Daemon connection error: {error_msg}")
        self.status_bar.set_status("Error connecting to daemon")
        self.settings_button.set_sensitive(False)
        self.set_state(AppState.ERROR)

        handle_model_load_error(self, Exception(error_msg))
        return False

    # ─── Daemon signal handlers ───────────────────────────────────────────

    def _on_daemon_status_changed(self, state: str) -> None:
        """Handle StatusChanged signal from daemon (may be called from any thread)."""
        GLib.idle_add(self._handle_status_change, state)

    def _handle_status_change(self, state: str, force: bool = False) -> bool:
        """
        Process daemon status change on main thread.

        Args:
            state: New daemon state string

        Returns:
            False to remove this idle callback
        """
        logger.debug(f"Daemon status changed: {state}")

        if state == "idle":
            # Don't reset to idle if we're waiting for TranscriptionReady
            if force or self._state != AppState.TRANSCRIBING:
                self.set_state(AppState.IDLE)
        elif state == "recording":
            self.set_state(AppState.RECORDING)
        elif state == "transcribing":
            self.set_state(AppState.TRANSCRIBING)
            self.status_bar.set_status(transcribing_status_text(self._daemon_model_state))
        return False

    def _on_daemon_transcription_ready(self, text: str, meta: dict) -> None:
        """Handle TranscriptionReady signal from daemon."""
        GLib.idle_add(self._handle_transcription_ready, text, meta)

    def _handle_transcription_ready(self, text: str, meta: dict) -> bool:
        """
        Process transcription result on main thread.

        Args:
            text: Transcribed text
            meta: Metadata dict with duration, confidence, etc.

        Returns:
            False to remove this idle callback
        """
        logger.info(f"Transcription received: {len(text)} characters")

        # Final text supersedes any partial view for this session (or clears
        # tracking entirely for a batch engine that never streamed partials). A
        # mismatching session_id means this is a stale signal from a session we've
        # moved on from (e.g. a cancel-then-restart race) — don't let it clobber
        # whatever newer session is now being displayed/recorded.
        if not self._partial_view.on_transcription_ready(text, meta):
            logger.info(
                "Ignoring stale TranscriptionReady for a superseded session: %r", meta
            )
            if hasattr(self, "history_panel"):
                self.history_panel.refresh_recent()
            return False

        # Display transcription text — block "changed" signal while loading to
        # avoid arming the debounce timer against the outgoing entry_id.
        buffer = self.text_view.get_buffer()
        history_id = int(meta.get("history_id", -1)) if isinstance(meta, dict) else -1
        self._set_current_entry(history_id if history_id > 0 else None)
        buffer.handler_block(self._buffer_changed_handler_id)
        buffer.set_text(text)
        buffer.handler_unblock(self._buffer_changed_handler_id)

        # Update status with metadata
        duration = meta.get("duration", 0.0) if isinstance(meta, dict) else 0.0
        confidence = meta.get("confidence", 0.0) if isinstance(meta, dict) else 0.0
        confidence_pct = int(confidence * 100)
        self.status_bar.set_status(
            f"Ready (Confidence: {confidence_pct}%, "
            f"Duration: {duration:.1f}s)"
        )

        # Update state
        self.set_state(AppState.READY)
        self.copy_button.set_sensitive(True)
        self.clear_button.set_sensitive(True)

        # Refresh history panel
        if hasattr(self, 'history_panel'):
            self.history_panel.refresh_recent()

        return False

    def _on_daemon_partial_transcript(
        self, session_id: str, committed: str, tentative: str, seq: int
    ) -> None:
        """Handle PartialTranscript signal from daemon (streaming engines only)."""
        GLib.idle_add(self._handle_partial_transcript, session_id, committed, tentative, seq)

    def _handle_partial_transcript(
        self, session_id: str, committed: str, tentative: str, seq: int
    ) -> bool:
        """
        Process a live partial transcript update on the main thread.

        Args:
            session_id: Recording session this partial belongs to.
            committed: Stable prefix text.
            tentative: Unstable in-flight tail text.
            seq: Monotonically increasing sequence number for this session.

        Returns:
            False to remove this idle callback.
        """
        self._partial_view.on_partial_transcript(session_id, committed, tentative, seq)
        self._render_partial_transcript()
        return False

    def _render_partial_transcript(self) -> None:
        """Render the tracked (committed, tentative) partial text into the
        transcription view: committed text normal, tentative text dimmed.

        Uses Gtk.TextBuffer.insert_markup with GLib.markup_escape_text on both
        pieces of text first, so engine output is never interpreted as markup.
        """
        committed, tentative = self._partial_view.render()
        buffer = self.text_view.get_buffer()

        markup = GLib.markup_escape_text(committed)
        if tentative:
            # GTK's TextBuffer.insert_markup only converts a handful of Pango
            # attributes into GtkTextTags; "alpha" alone is dropped, so dim via a
            # muted foreground color (GNOME's standard "dim-label" gray) instead.
            markup += (
                '<span foreground="#9a9996">'
                f"{GLib.markup_escape_text(tentative)}</span>"
            )

        buffer.handler_block(self._buffer_changed_handler_id)
        buffer.set_text("")
        buffer.insert_markup(buffer.get_end_iter(), markup, -1)
        buffer.handler_unblock(self._buffer_changed_handler_id)

    def _on_daemon_level_update(self, level: float) -> None:
        """Handle LevelUpdate signal from daemon."""
        GLib.idle_add(self.level_meter.update_level, level, level, 0.0)

    def _on_daemon_model_state_changed(self, state: str, detail: str) -> None:
        """Handle ModelStateChanged signal from daemon (may be any thread)."""
        GLib.idle_add(self._apply_model_state, state, detail)

    def _refresh_model_state_from_status(self) -> None:
        """Seed the model readiness display from GetStatus (best-effort)."""
        try:
            self._apply_model_state(*resolve_model_state(self.client.get_status()))
        except Exception as e:
            logger.debug("Failed to read model state from daemon status: %s", e)

    def _apply_model_state(self, state: str, detail: str = "") -> bool:
        """Show the daemon's model readiness in the status bar (main thread).

        The record button is deliberately left alone: a batch transcription
        waits for the model, and while it does the transcribing status makes
        that wait visible."""
        self._daemon_model_state = state
        view = describe_model_state(state, detail)
        self.status_bar.set_model_state(view.text, view.show_download)
        if self._state == AppState.TRANSCRIBING:
            self.status_bar.set_status(transcribing_status_text(state))
        return False

    def _on_daemon_error(self, code: str, message: str) -> None:
        """Handle Error signal from daemon."""
        GLib.idle_add(self._handle_daemon_error, code, message)

    def _handle_daemon_error(self, code: str, message: str) -> bool:
        """
        Process daemon error on main thread.

        Args:
            code: Error code string
            message: Human-readable error message

        Returns:
            False to remove this idle callback
        """
        logger.error(f"Daemon error [{code}]: {message}")
        self.status_bar.set_status(f"Error: {message}")
        self._partial_view.reset()
        self.set_state(AppState.ERROR)
        return False

    # ─── File transcription (WHISP-96) ─────────────────────────────────────

    def _on_transcribe_file_clicked(self, _button) -> None:
        """"Transcribe file..." header button / Ctrl+O."""
        if not self.client or not self.client.is_connected:
            self.status_bar.set_status("Not connected to daemon")
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Transcribe File")

        audio_video_filter = Gtk.FileFilter()
        audio_video_filter.set_name("Audio/Video")
        for pattern in file_dialog_filter_patterns():
            audio_video_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(audio_video_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(audio_video_filter)

        dialog.open(self, None, self._on_transcribe_file_dialog_response)

    def _on_transcribe_file_dialog_response(self, dialog, result) -> None:
        """Gtk.FileDialog.open() async callback. Portal-backed under
        Flatpak -- the returned Gio.File's get_path() is a document-portal
        path the sandbox can actually read, not the host path the user saw
        in the picker."""
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error as e:
            logger.debug(f"Transcribe-file dialog cancelled or failed: {e}")
            return
        if gfile is None:
            return
        path = gfile.get_path()
        if not path:
            logger.warning("Transcribe-file dialog returned a file with no local path")
            return
        self._start_file_transcription(path)

    def _on_file_drop(self, _drop_target, value, _x, _y) -> bool:
        """Gtk.DropTarget "drop" handler: `value` is a Gdk.FileList (one or
        more dropped Gio.File). Only the first supported audio/video file
        is used -- see pick_first_supported_path()'s docstring."""
        try:
            gfiles = value.get_files()
        except AttributeError:
            gfiles = [value] if value is not None else []
        path = pick_first_supported_path(f.get_path() for f in gfiles)
        if not path:
            return False
        self._start_file_transcription(path)
        return True

    def open_file_for_transcription(self, path: str) -> None:
        """Public entry point for "Open With WhisperAloud" (GApplication
        do_open) and the command line -- called once the window/daemon
        connection is up. Retries briefly if the daemon isn't connected yet
        (e.g. the app was just launched by opening a file)."""
        self._open_file_when_ready(path, attempts_left=40)

    def _open_file_when_ready(self, path: str, attempts_left: int) -> bool:
        if self.client and self.client.is_connected:
            self._start_file_transcription(path)
            return False
        if attempts_left <= 0:
            logger.warning(f"Gave up waiting for daemon connection to open {path!r}")
            return False
        GLib.timeout_add(250, self._open_file_when_ready, path, attempts_left - 1)
        return False

    def _start_file_transcription(self, path: str) -> None:
        """Kick off TranscribeFile() for `path` and show it as in-flight.
        Entirely independent of the mic-recording AppState machine -- a
        file transcription never disables the record button or changes
        `self._state`."""
        if not self.client or not self.client.is_connected:
            self.status_bar.set_status("Not connected to daemon")
            return

        language = "auto"
        if self._language_codes:
            selected = self.lang_dropdown.get_selected()
            if 0 <= selected < len(self._language_codes):
                language = self._language_codes[selected]
        options = transcribe_options_for_language(language)

        job_id = self.client.transcribe_file(path, options)
        if not job_id:
            self.status_bar.set_status("Could not start file transcription")
            return

        self._file_job_id = job_id
        filename = Path(path).name
        self.status_bar.set_status(status_label_for_file_job(filename))
        logger.info(f"Started file transcription job {job_id[:8]} for {filename!r}")

    def _on_daemon_file_transcription_progress(self, job_id: str, stage: str) -> None:
        """Handle FileTranscriptionProgress signal from daemon."""
        GLib.idle_add(self._handle_file_transcription_progress, job_id, stage)

    def _handle_file_transcription_progress(self, job_id: str, stage: str) -> bool:
        if is_stale_job_response(job_id, self._file_job_id):
            return False
        self.status_bar.set_status(f"{stage.capitalize()}…")
        return False

    def _on_daemon_file_transcription_ready(self, job_id: str, text: str, meta: dict) -> None:
        """Handle FileTranscriptionReady signal from daemon."""
        GLib.idle_add(self._handle_file_transcription_ready, job_id, text, meta)

    def _handle_file_transcription_ready(self, job_id: str, text: str, meta: dict) -> bool:
        if is_stale_job_response(job_id, self._file_job_id):
            logger.info(f"Ignoring stale FileTranscriptionReady for job {job_id!r}")
            return False
        self._file_job_id = None

        buffer = self.text_view.get_buffer()
        history_id = int(meta.get("history_id", -1)) if isinstance(meta, dict) else -1
        self._set_current_entry(history_id if history_id > 0 else None)
        buffer.handler_block(self._buffer_changed_handler_id)
        buffer.set_text(text)
        buffer.handler_unblock(self._buffer_changed_handler_id)

        self.copy_button.set_sensitive(True)
        self.clear_button.set_sensitive(True)

        source_name = meta.get("source_name", "") if isinstance(meta, dict) else ""
        self.status_bar.set_status(
            f"Ready — transcribed {source_name}" if source_name else "Ready"
        )

        if hasattr(self, "history_panel"):
            self.history_panel.refresh_recent()
        return False

    def _on_daemon_file_transcription_error(self, job_id: str, code: str, message: str) -> None:
        """Handle FileTranscriptionError signal from daemon."""
        GLib.idle_add(self._handle_file_transcription_error, job_id, code, message)

    def _handle_file_transcription_error(self, job_id: str, code: str, message: str) -> bool:
        if is_stale_job_response(job_id, self._file_job_id):
            return False
        self._file_job_id = None
        logger.error(f"File transcription failed [{code}]: {message}")
        self.status_bar.set_status(f"File transcription failed: {message}")
        return False

    # ─── State management ─────────────────────────────────────────────────

    def set_state(self, new_state: AppState) -> None:
        """
        Update application state and UI accordingly.

        Args:
            new_state: The new state to transition to
        """
        old_state = self._state
        logger.info(f"State transition: {old_state.value} -> {new_state.value}")
        self._state = new_state

        # Play sound feedback for state transitions
        self._play_state_sound(old_state, new_state)

        try:
            # Update UI based on state
            if new_state == AppState.IDLE:
                logger.debug("Setting UI for IDLE state")
                # Show record button, hide cancel button
                self.cancel_button.set_visible(False)
                self.record_button.set_visible(True)
                self.record_button.set_label("Start Recording")
                self.record_button.set_sensitive(self._daemon_available)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
                self.timer_label.set_text("0:00")

            elif new_state == AppState.RECORDING:
                logger.debug("Setting UI for RECORDING state")
                self.record_button.set_label("Stop Recording")
                self.record_button.set_sensitive(self._daemon_available)
                self.record_button.remove_css_class("suggested-action")
                self.record_button.add_css_class("destructive-action")

            elif new_state == AppState.TRANSCRIBING:
                logger.debug("Setting UI for TRANSCRIBING state")
                # Hide record button, show cancel button
                self.record_button.set_visible(False)
                self.cancel_button.set_visible(True)
                self.cancel_button.set_sensitive(True)
                self.cancel_button.set_label("Cancel Transcription")
                self.status_bar.set_status(transcribing_status_text(self._daemon_model_state))

            elif new_state == AppState.CANCELLING:
                logger.debug("Setting UI for CANCELLING state")
                self.cancel_button.set_label("Cancelling...")
                self.cancel_button.set_sensitive(False)
                self.status_bar.set_status("Cancelling transcription...")

            elif new_state == AppState.READY:
                logger.debug("Setting UI for READY state")
                # Show record button, hide cancel button
                self.cancel_button.set_visible(False)
                self.record_button.set_visible(True)
                self.record_button.set_label("Record Again")
                self.record_button.set_sensitive(self._daemon_available)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
                self.status_bar.set_status("Ready")
                self.copy_button.set_sensitive(True)
                self.clear_button.set_sensitive(True)

            elif new_state == AppState.ERROR:
                logger.debug("Setting UI for ERROR state")
                # Show record button, hide cancel button
                self.cancel_button.set_visible(False)
                self.record_button.set_visible(True)
                self.record_button.set_label("Try Again")
                self.record_button.set_sensitive(self._daemon_available)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
        except Exception as e:
            logger.error(f"Error in set_state: {e}", exc_info=True)

    def _play_state_sound(self, old_state: AppState, new_state: AppState) -> None:
        """
        Play appropriate sound feedback for state transition.

        Args:
            old_state: Previous state
            new_state: New state
        """
        try:
            if new_state == AppState.RECORDING:
                self.sound_feedback.play_recording_start()
            elif new_state == AppState.TRANSCRIBING and old_state == AppState.RECORDING:
                self.sound_feedback.play_recording_stop()
            elif new_state == AppState.READY:
                self.sound_feedback.play_transcription_complete()
            elif new_state == AppState.ERROR:
                self.sound_feedback.play_error()
            elif new_state == AppState.CANCELLING:
                self.sound_feedback.play_cancel()
        except Exception as e:
            logger.debug(f"Sound feedback failed: {e}")

    # ─── Recording actions (via D-Bus) ────────────────────────────────────

    def _on_record_button_clicked(self, button: Gtk.Button) -> None:
        """
        Handle record button click.

        Args:
            button: The button that was clicked
        """
        logger.info(f"Record button clicked, current state: {self._state.value}")

        if self._state == AppState.IDLE or self._state == AppState.READY:
            # Start recording
            self._start_recording()
        elif self._state == AppState.RECORDING:
            # Stop recording and start transcription
            self._stop_recording_and_transcribe()

    def _start_recording(self) -> None:
        """Start audio recording via daemon."""
        try:
            logger.info("Starting audio recording via daemon")
            if not self.client or not self.client.is_connected or not self._daemon_available:
                self.status_bar.set_status("Daemon not connected")
                self.set_state(AppState.ERROR)
                return

            # Reset level meter and any leftover partial-transcript tracking from
            # a previous session before starting a new one.
            self.level_meter.reset()
            self._partial_view.reset()

            success = self.client.start_recording()
            if success:
                self.set_state(AppState.RECORDING)
                self.status_bar.set_status("Recording...")

                # Start local timer
                self._timer_active = True
                self._timer_seconds = 0
                GLib.timeout_add(1000, self._update_timer)
            else:
                self.status_bar.set_status("Failed to start recording")
                self.set_state(AppState.ERROR)

        except Exception as e:
            logger.error(f"Failed to start recording: {e}", exc_info=True)
            self.status_bar.set_status("Recording failed")
            self.set_state(AppState.ERROR)

    def _stop_recording_and_transcribe(self) -> None:
        """Stop recording via daemon. Result comes back via TranscriptionReady signal."""
        logger.info("Stopping recording via daemon")

        # Stop local timer
        self._timer_active = False

        daemon_ready = is_daemon_interaction_ready(
            client_present=self.client is not None,
            client_connected=bool(self.client and self.client.is_connected),
            daemon_available=self._daemon_available,
        )
        if not daemon_ready:
            self.status_bar.set_status("Daemon not connected")
            self.set_state(AppState.ERROR)
            return

        # Tell daemon to stop recording (non-blocking; transcription result arrives via signal)
        result = self.client.stop_recording() if self.client else ""
        if not should_enter_transcribing(result):
            logger.warning("StopRecording returned unexpected state: %r", result)
            self.status_bar.set_status("Failed to start transcription")
            self.set_state(AppState.ERROR)
            return

        # Update UI only when daemon accepted the stop/transcribe request.
        self.set_state(AppState.TRANSCRIBING)

    def _update_timer(self) -> bool:
        """
        Update recording timer locally.

        Returns:
            True to continue timer, False to stop
        """
        if not self._timer_active:
            return False

        self._timer_seconds += 1
        self.timer_label.set_text(format_duration(self._timer_seconds))
        return True

    def _on_cancel_clicked(self, button: Gtk.Button) -> None:
        """
        Handle cancel transcription button click.

        Args:
            button: The button that was clicked
        """
        logger.info("Cancel transcription button clicked")

        if self._state != AppState.TRANSCRIBING:
            return

        daemon_ready = is_daemon_interaction_ready(
            client_present=self.client is not None,
            client_connected=bool(self.client and self.client.is_connected),
            daemon_available=self._daemon_available,
        )
        if not daemon_ready:
            self.status_bar.set_status("Daemon not connected")
            self.set_state(AppState.ERROR)
            return

        self.set_state(AppState.CANCELLING)
        cancel_result = self.client.cancel_recording() if self.client else False
        if should_restore_transcribing_after_cancel(cancel_result):
            logger.warning("CancelRecording rejected while transcribing")
            self.status_bar.set_status("Cancel not available")
            self.set_state(AppState.TRANSCRIBING)
        else:
            # No TranscriptionReady will follow for this session; drop any
            # leftover partial-transcript tracking so it can't leak into the next.
            self._partial_view.reset()

    # ─── Clipboard ────────────────────────────────────────────────────────

    def _on_copy_clicked(self, button: Gtk.Button) -> None:
        """
        Handle copy button click.

        Args:
            button: The button that was clicked
        """
        logger.debug("Copy button clicked")
        self._copy_to_clipboard()

    def _set_current_entry(self, entry_id: Optional[int]) -> None:
        """Track which history entry is displayed and update text_view editability."""
        self._current_entry_id = entry_id
        editable = (
            entry_id is not None
            and self.config is not None
            and self.config.persistence.edit_history_enabled
        )
        self.text_view.set_editable(editable)
        self.text_view.set_cursor_visible(editable)

    def _on_text_buffer_changed(self, buffer: Gtk.TextBuffer) -> None:
        """Debounce edits: reset 1.5s timer on every keystroke."""
        if not self._current_entry_id or not self.client:
            return
        if self._edit_save_timer_id:
            GLib.source_remove(self._edit_save_timer_id)
            self._edit_save_timer_id = 0
        self._edit_save_timer_id = GLib.timeout_add(1500, self._flush_edit)

    def _flush_edit(self) -> bool:
        """Save current text_view content to history entry."""
        self._edit_save_timer_id = 0
        if not self._current_entry_id or not self.client:
            return GLib.SOURCE_REMOVE
        buffer = self.text_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        try:
            ok = self.client.update_history_entry(self._current_entry_id, text)
            logger.debug(f"[EDIT] update_history_entry({self._current_entry_id}) → {ok}")
            if ok:
                self.status_bar.set_status("Saved", timeout_ms=2000)
                if hasattr(self, 'history_panel'):
                    self.history_panel.refresh_recent()
            else:
                self.status_bar.set_status("Save failed")
        except Exception as e:
            logger.warning(f"Auto-save failed: {e}")
            self.status_bar.set_status("Save failed")
        return GLib.SOURCE_REMOVE

    def _copy_to_clipboard(self) -> None:
        """Copy transcription text to clipboard using GTK4 clipboard API."""
        buffer = self.text_view.get_buffer()
        text = buffer.get_text(
            buffer.get_start_iter(),
            buffer.get_end_iter(),
            False
        )

        if not text:
            logger.warning("No text to copy")
            return

        try:
            clipboard = self.get_clipboard()
            clipboard.set(text)
            self.status_bar.set_status("Copied to clipboard")
            logger.info(f"Copied {len(text)} characters to clipboard")
        except Exception as e:
            logger.error(f"Clipboard error: {e}", exc_info=True)
            self.status_bar.set_status("Clipboard error")

    # ─── Clear / History / Settings / Help ────────────────────────────────

    def _on_clear_clicked(self, button: Gtk.Button) -> None:
        """
        Handle clear button click.

        Args:
            button: The button that was clicked
        """
        logger.debug("Clear button clicked")
        self._set_current_entry(None)
        buffer = self.text_view.get_buffer()
        buffer.set_text("")
        self.copy_button.set_sensitive(False)
        self.clear_button.set_sensitive(False)

        if self._state == AppState.READY:
            self.set_state(AppState.IDLE)

    def _on_language_changed(self, dropdown: Gtk.DropDown, param) -> None:
        """
        Handle language dropdown selection change.

        Args:
            dropdown: The dropdown widget
            param: The parameter that changed
        """
        # Guard: don't change language if config not loaded yet
        if not self.config:
            return

        selection = resolve_language_change(
            selected_idx=dropdown.get_selected(),
            language_codes=self._language_codes,
            current_language=self.config.transcription.language,
        )
        if selection is None:
            return
        new_lang, current_lang = selection

        logger.info(f"Language changed: {current_lang} -> {new_lang}")

        # Apply via daemon SetConfig (daemon owns config persistence)
        try:
            daemon_ready = is_daemon_interaction_ready(
                client_present=self.client is not None,
                client_connected=bool(self.client and self.client.is_connected),
                daemon_available=self._daemon_available,
            )
            if not daemon_ready:
                logger.warning("Ignoring language change while daemon is unavailable")
                self.status_bar.set_status("Daemon unavailable: language not changed")
                dropdown.set_selected(self._language_codes.index(current_lang))
                return

            changed = self.client.set_config({"transcription.language": new_lang}) if self.client else False
            if not changed:
                logger.warning("Daemon rejected language update")
                self.status_bar.set_status("Daemon rejected language update")
                dropdown.set_selected(self._language_codes.index(current_lang))
                return

            # Update local reference to stay in sync
            self.config.transcription.language = new_lang
            self._update_model_info()

        except Exception as e:
            logger.error(f"Failed to change language: {e}", exc_info=True)

    def _on_help_clicked(self, button: Gtk.Button) -> None:
        """
        Handle help/shortcuts button click.

        Args:
            button: The button that was clicked
        """
        logger.info("Opening shortcuts window")
        shortcuts = ShortcutsWindow(self)
        shortcuts.present()

    def _on_settings_clicked(self, button: Gtk.Button) -> None:
        """
        Handle settings button click.

        Args:
            button: The button that was clicked
        """
        logger.info("Opening settings dialog")

        if not self.config:
            self.status_bar.set_status("Settings unavailable until daemon connects")
            return

        # Create and show settings dialog
        dialog = SettingsDialog(
            self,
            self.config,
            on_save_callback=self._on_settings_saved,
            autostart_controller=self._get_autostart_controller(),
        )
        dialog.present()

    def _get_autostart_controller(self):
        """The process-wide AutostartController (WHISP-87), or None if it
        cannot be built (the Startup section then shows as unavailable)."""
        if self._autostart_controller is None:
            try:
                from ..sandbox import is_flatpak_sandbox
                from .startup_logic import AutostartController

                self._autostart_controller = AutostartController(
                    sandboxed=is_flatpak_sandbox(),
                    initial=bool(self.config and self.config.startup.autostart_background),
                    persist=self._persist_autostart,
                )
            except Exception:
                logger.exception("Could not set up the login autostart controller")
                return None
        return self._autostart_controller

    def _persist_autostart(self, enabled: bool) -> None:
        """Store the confirmed autostart state (daemon SetConfig, else file)."""
        from .startup_logic import persist_autostart_setting

        if self.config is not None:
            self.config.startup.autostart_background = bool(enabled)
        client = (
            self.client
            if self.client and self.client.is_connected and self._daemon_available
            else None
        )
        where = persist_autostart_setting(enabled, client, load_config=WhisperAloudConfig.load)
        logger.info("startup.autostart_background=%s stored via %s", enabled, where)

    def _on_settings_saved(self) -> None:
        """Handle settings saved event.

        The SettingsDialog writes the config file directly (multi-field batch).
        We tell the daemon to reload so it picks up the changes and
        re-initializes any affected components.
        """
        logger.info("Settings saved, notifying daemon to reload config")

        try:
            # Reload local config reference from disk
            self.config = WhisperAloudConfig.load()

            # Tell daemon to reload from disk (triggers _apply_config_changes)
            if self.client and self.client.is_connected and self._daemon_available:
                self.client.reload_config()
            else:
                self.status_bar.set_status("Saved locally; reconnect daemon to apply")

            # Update model info display
            self._update_model_info()

            # Re-evaluate text_view editability with fresh config
            self._set_current_entry(self._current_entry_id)

        except Exception as e:
            logger.error(f"Error in _on_settings_saved: {e}", exc_info=True)
            self.status_bar.set_status("Error updating settings")
            self.set_state(AppState.ERROR)

    def _on_history_toggled(self, button: Gtk.ToggleButton) -> None:
        """Handle history toggle button."""
        is_visible = button.get_active()
        button.set_icon_name("sidebar-show-symbolic" if is_visible else "sidebar-hide-symbolic")

        # History panel is on the left (start_child)
        if is_visible:
            self.paned.set_start_child(self.history_container)
        else:
            self.paned.set_start_child(None)

    def _on_history_entry_selected(self, panel, entry):
        """Handle history entry selection — loads text and copies to clipboard."""
        buffer = self.text_view.get_buffer()
        # Set entry_id before loading text so the "changed" signal guard sees None→id,
        # then block the signal to prevent arming the debounce timer on a programmatic load.
        self._set_current_entry(entry.id)
        buffer.handler_block(self._buffer_changed_handler_id)
        buffer.set_text(entry.text)
        buffer.handler_unblock(self._buffer_changed_handler_id)

        self.set_state(AppState.READY)
        self.copy_button.set_sensitive(True)
        self.clear_button.set_sensitive(True)

        # Auto-copy to clipboard on selection
        try:
            clipboard = self.get_clipboard()
            clipboard.set(entry.text)
            confidence_pct = int(entry.confidence * 100)
            self.status_bar.set_status(
                f"Copied to clipboard (Confidence: {confidence_pct}%, "
                f"Duration: {entry.duration:.1f}s)"
            )
        except Exception as e:
            logger.error(f"Clipboard error on history selection: {e}")
            confidence_pct = int(entry.confidence * 100)
            self.status_bar.set_status(
                f"Loaded from history (Confidence: {confidence_pct}%, "
                f"Duration: {entry.duration:.1f}s)"
            )

    def _update_model_info(self) -> None:
        """Update status bar and language button with current model info."""
        if not self.config:
            return
        self.status_bar.set_model_info(
            self.config.model.name,
            self.config.model.device,
            self.config.transcription.language
        )

        # Update language dropdown selection
        lang = self.config.transcription.language or "auto"
        if lang in self._language_codes:
            self.lang_dropdown.set_selected(self._language_codes.index(lang))

    # ─── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Clean up resources before shutdown."""
        logger.info("Cleaning up main window resources")

        # Stop status bar monitoring
        if hasattr(self, 'status_bar'):
            self.status_bar.cleanup()

        # Cancel pending edit-save timer before disconnecting
        if self._edit_save_timer_id:
            GLib.source_remove(self._edit_save_timer_id)
            self._edit_save_timer_id = 0

        # Cancel a pending auto-retry-with-backoff timeout (WHISP-60), if any
        if self._retry_timeout_id:
            GLib.source_remove(self._retry_timeout_id)
            self._retry_timeout_id = 0

        if self._autostart_controller is not None:
            self._autostart_controller.close()

        # Disconnect D-Bus client
        if self.client:
            self.client.disconnect()

        logger.info("Cleanup complete")
