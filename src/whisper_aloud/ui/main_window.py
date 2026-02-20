"""Main application window for WhisperAloud."""

import logging
import threading
from pathlib import Path
from typing import Optional

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

from ..config import WhisperAloudConfig
from ..persistence.history_manager import HistoryManager
from .error_handler import (
    handle_model_load_error,
)
from .history_panel import HistoryPanel
from .level_meter import LevelMeterPanel
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
        self._timer_active: bool = False
        self._timer_seconds: int = 0
        self._daemon_available: bool = False

        # D-Bus client (replaces direct component access)
        from ..service.client import WhisperAloudClient
        self.client: Optional[WhisperAloudClient] = None

        # Local config for UI settings (language dropdown, settings dialog)
        self.config: Optional[WhisperAloudConfig] = None

        # Local read-only HistoryManager (reads shared SQLite DB written by daemon)
        self.history_manager: Optional[HistoryManager] = None

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

        # Help/Shortcuts button
        help_button = Gtk.Button()
        help_button.set_icon_name("help-about-symbolic")
        help_button.set_tooltip_text("Keyboard Shortcuts (F1)")
        help_button.add_css_class("wa-toolbar-btn")
        help_button.connect("clicked", self._on_help_clicked)
        self.header_bar.pack_end(help_button)

        # Settings button
        settings_button = Gtk.Button()
        settings_button.set_icon_name("preferences-system-symbolic")
        settings_button.set_tooltip_text("Settings (Ctrl+,)")
        settings_button.add_css_class("wa-toolbar-btn")
        settings_button.connect("clicked", self._on_settings_clicked)
        self.header_bar.pack_end(settings_button)

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
        self.paned.set_position(280)  # History panel width
        self.paned.set_wide_handle(True)
        self.paned.set_shrink_start_child(False)  # Don't allow history to shrink below minimum
        self.paned.set_shrink_end_child(False)  # Don't allow main area to shrink too much
        main_box.append(self.paned)

        # Left side: Recording and Transcription
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Status label
        self.status_label = Gtk.Label(label="Connecting to daemon...")
        self.status_label.add_css_class("wa-status-chip")
        self.status_label.set_margin_top(12)
        self.status_label.set_margin_bottom(12)
        left_box.append(self.status_label)

        # Recording panel
        recording_panel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        recording_panel_box.add_css_class("wa-surface")
        recording_panel_box.set_margin_start(32)
        recording_panel_box.set_margin_end(32)
        recording_panel_box.set_margin_top(16)
        recording_panel_box.set_margin_bottom(16)
        recording_panel_box.set_halign(Gtk.Align.CENTER)  # Center the recording controls

        # Record button
        self.record_button = Gtk.Button(label="Start Recording")
        self.record_button.add_css_class("suggested-action")
        self.record_button.add_css_class("pill")
        self.record_button.add_css_class("wa-primary-action")
        self.record_button.set_size_request(200, 56)  # Fixed width for consistent look
        self.record_button.set_sensitive(False)  # Disabled until daemon connects
        self.record_button.set_tooltip_text("Start/stop recording (Space)")
        self.record_button.connect("clicked", self._on_record_button_clicked)
        recording_panel_box.append(self.record_button)

        # Cancel button (shown during transcription)
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.add_css_class("pill")
        self.cancel_button.add_css_class("wa-primary-action")
        self.cancel_button.set_size_request(200, 56)  # Same size as record button
        self.cancel_button.set_visible(False)  # Hidden by default
        self.cancel_button.set_tooltip_text("Cancel transcription (Ctrl+X)")
        self.cancel_button.connect("clicked", self._on_cancel_clicked)
        recording_panel_box.append(self.cancel_button)

        # Timer label
        self.timer_label = Gtk.Label(label="0:00")
        self.timer_label.add_css_class("title-1")
        self.timer_label.set_margin_top(8)
        recording_panel_box.append(self.timer_label)

        # Level meter
        self.level_meter = LevelMeterPanel()
        self.level_meter.set_margin_top(8)
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
        self.history_container.set_size_request(240, -1)
        self.paned.set_start_child(self.history_container)

        # Right side: Main recording/transcription panel
        left_box.set_size_request(400, -1)  # Minimum width for main content
        self.paned.set_end_child(left_box)

        # Status bar
        self.status_bar = StatusBar()
        main_box.append(self.status_bar)

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

        return False

    # ─── Daemon connection ────────────────────────────────────────────────

    def _init_components_async(self) -> bool:
        """
        Connect to daemon asynchronously.

        Returns:
            False to remove this idle callback
        """
        self._show_loading_dialog()

        def _connect_in_thread():
            """Connect to daemon in background thread."""
            try:
                from ..service.client import WhisperAloudClient
                client = WhisperAloudClient()
                if client.is_connected:
                    GLib.idle_add(self._on_daemon_connected, client)
                else:
                    GLib.idle_add(self._on_daemon_unavailable)
            except Exception as e:
                logger.error(f"Failed to connect to daemon: {e}", exc_info=True)
                GLib.idle_add(self._on_load_error, str(e))

        threading.Thread(target=_connect_in_thread, daemon=True).start()
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
        logger.info("Connected to WhisperAloud daemon")

        # Subscribe to daemon signals
        self.client.on_status_changed(self._on_daemon_status_changed)
        self.client.on_transcription_ready(self._on_daemon_transcription_ready)
        self.client.on_level_update(self._on_daemon_level_update)
        self.client.on_error(self._on_daemon_error)

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

        # Initialize local read-only HistoryManager for the history panel
        try:
            self.history_manager = HistoryManager(self.config.persistence)
        except Exception as e:
            logger.warning(f"Failed to init history manager: {e}")

        # Hide loading dialog
        self._hide_loading_dialog()

        # Update UI state
        self.status_label.set_text("Ready")
        self.record_button.set_sensitive(True)
        self.set_state(AppState.IDLE)

        # Update language dropdown from config
        if self.config and self.config.transcription.language:
            current_lang = self.config.transcription.language
            if current_lang in self._language_codes:
                self.lang_dropdown.set_selected(self._language_codes.index(current_lang))

        # Initialize History Panel with local read-only HistoryManager
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
        self.status_label.set_text("Reconnecting...")
        try:
            self.config = WhisperAloudConfig.load()
            self._update_model_info()
        except Exception:
            pass
        self.status_label.set_text("Ready")
        self.record_button.set_sensitive(True)
        self.set_state(AppState.IDLE)
        logger.info("Reconnected to daemon after restart")
        return False

    def _on_daemon_lost(self) -> None:
        """Called when daemon vanishes from the bus (crash or quit)."""
        logger.warning("Daemon connection lost")
        GLib.idle_add(self._handle_disconnection)

    def _handle_disconnection(self) -> bool:
        """Update UI for lost daemon connection on the main thread."""
        self._daemon_available = False
        self._timer_active = False
        self.record_button.set_sensitive(False)
        self.status_label.set_text("Daemon disconnected - waiting for restart...")
        self.set_state(AppState.ERROR)
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

        self.status_label.set_text("Daemon not running")
        self.set_state(AppState.ERROR)

        # Show diagnostic dialog with retry option
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

        title = Gtk.Label(label="WhisperAloud daemon not running")
        title.add_css_class("title-2")
        box.append(title)

        has_user_service = (Path.home() / ".config/systemd/user/whisper-aloud.service").exists()
        if has_user_service:
            hint_text = (
                "Start the daemon with:\n\n"
                "  systemctl --user start whisper-aloud\n\n"
                "Or run directly:\n\n"
                "  whisper-aloud-daemon\n"
                "  whisper-aloud --daemon"
            )
        else:
            hint_text = (
                "Start the daemon with:\n\n"
                "  whisper-aloud-daemon\n"
                "  whisper-aloud --daemon\n\n"
                "Tip: run ./install.sh to enable systemd user service integration."
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
        dialog.present()

        return False

    def _retry_daemon_connection(self, dialog: Gtk.Window) -> None:
        """
        Retry connecting to the daemon.

        Args:
            dialog: The unavailable dialog to close on success
        """
        dialog.close()
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
        self.status_label.set_text("Error connecting to daemon")
        self.set_state(AppState.ERROR)

        handle_model_load_error(self, Exception(error_msg))
        return False

    # ─── Daemon signal handlers ───────────────────────────────────────────

    def _on_daemon_status_changed(self, state: str) -> None:
        """Handle StatusChanged signal from daemon (may be called from any thread)."""
        GLib.idle_add(self._handle_status_change, state)

    def _handle_status_change(self, state: str) -> bool:
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
            if self._state != AppState.TRANSCRIBING:
                self.set_state(AppState.IDLE)
        elif state == "recording":
            self.set_state(AppState.RECORDING)
        elif state == "transcribing":
            self.set_state(AppState.TRANSCRIBING)
            self.status_label.set_text("Transcribing... (press Ctrl+X to cancel)")
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

        # Display transcription text
        buffer = self.text_view.get_buffer()
        buffer.set_text(text)

        # Update status with metadata
        duration = meta.get("duration", 0.0) if isinstance(meta, dict) else 0.0
        confidence = meta.get("confidence", 0.0) if isinstance(meta, dict) else 0.0
        confidence_pct = int(confidence * 100)
        self.status_label.set_text(
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

    def _on_daemon_level_update(self, level: float) -> None:
        """Handle LevelUpdate signal from daemon."""
        GLib.idle_add(self.level_meter.update_level, level, level, 0.0)

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
        self.status_label.set_text(f"Error: {message}")
        self.set_state(AppState.ERROR)
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
                self.status_label.set_text("Transcribing... (press Ctrl+X to cancel)")

            elif new_state == AppState.CANCELLING:
                logger.debug("Setting UI for CANCELLING state")
                self.cancel_button.set_label("Cancelling...")
                self.cancel_button.set_sensitive(False)
                self.status_label.set_text("Cancelling transcription...")

            elif new_state == AppState.READY:
                logger.debug("Setting UI for READY state")
                # Show record button, hide cancel button
                self.cancel_button.set_visible(False)
                self.record_button.set_visible(True)
                self.record_button.set_label("Record Again")
                self.record_button.set_sensitive(self._daemon_available)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
                self.status_label.set_text("Ready")
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
                self.status_label.set_text("Daemon not connected")
                self.set_state(AppState.ERROR)
                return

            # Reset level meter
            self.level_meter.reset()

            success = self.client.start_recording()
            if success:
                self.set_state(AppState.RECORDING)
                self.status_label.set_text("Recording...")

                # Start local timer
                self._timer_active = True
                self._timer_seconds = 0
                GLib.timeout_add(1000, self._update_timer)
            else:
                self.status_label.set_text("Failed to start recording")
                self.set_state(AppState.ERROR)

        except Exception as e:
            logger.error(f"Failed to start recording: {e}", exc_info=True)
            self.status_label.set_text("Recording failed")
            self.set_state(AppState.ERROR)

    def _stop_recording_and_transcribe(self) -> None:
        """Stop recording via daemon. Result comes back via TranscriptionReady signal."""
        logger.info("Stopping recording via daemon")

        # Stop local timer
        self._timer_active = False

        # Update UI
        self.set_state(AppState.TRANSCRIBING)

        # Tell daemon to stop recording (non-blocking; transcription result arrives via signal)
        self.client.stop_recording()

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

        self.set_state(AppState.CANCELLING)
        self.client.cancel_recording()

    # ─── Clipboard ────────────────────────────────────────────────────────

    def _on_copy_clicked(self, button: Gtk.Button) -> None:
        """
        Handle copy button click.

        Args:
            button: The button that was clicked
        """
        logger.debug("Copy button clicked")
        self._copy_to_clipboard()

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
            self.status_label.set_text("Copied to clipboard")
            logger.info(f"Copied {len(text)} characters to clipboard")
        except Exception as e:
            logger.error(f"Clipboard error: {e}", exc_info=True)
            self.status_label.set_text("Clipboard error")

    # ─── Clear / History / Settings / Help ────────────────────────────────

    def _on_clear_clicked(self, button: Gtk.Button) -> None:
        """
        Handle clear button click.

        Args:
            button: The button that was clicked
        """
        logger.debug("Clear button clicked")
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
        selected_idx = dropdown.get_selected()
        if selected_idx < 0 or selected_idx >= len(self._language_codes):
            return

        # Guard: don't change language if config not loaded yet
        if not self.config:
            return

        new_lang = self._language_codes[selected_idx]
        # "auto" means None for transcription config
        new_lang_config = None if new_lang == "auto" else new_lang
        current_lang = self.config.transcription.language

        # Skip if same language
        if new_lang_config == current_lang:
            return

        logger.info(f"Language changed: {current_lang} -> {new_lang}")

        # Apply via daemon SetConfig (daemon owns config persistence)
        try:
            if not self.client or not self.client.is_connected or not self._daemon_available:
                logger.warning("Ignoring language change while daemon is unavailable")
                self.status_label.set_text("Daemon unavailable: language not changed")
                previous_lang = current_lang or "auto"
                if previous_lang in self._language_codes:
                    dropdown.set_selected(self._language_codes.index(previous_lang))
                return

            lang_value = new_lang_config if new_lang_config is not None else "auto"
            changed = self.client.set_config({"transcription.language": lang_value})
            if not changed:
                logger.warning("Daemon rejected language update")
                self.status_label.set_text("Daemon rejected language update")
                previous_lang = current_lang or "auto"
                if previous_lang in self._language_codes:
                    dropdown.set_selected(self._language_codes.index(previous_lang))
                return

            # Update local reference to stay in sync
            self.config.transcription.language = new_lang_config
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

        # Create and show settings dialog
        dialog = SettingsDialog(self, self.config, on_save_callback=self._on_settings_saved)
        dialog.present()

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
                self.status_label.set_text("Saved locally; reconnect daemon to apply")

            # Update model info display
            self._update_model_info()

        except Exception as e:
            logger.error(f"Error in _on_settings_saved: {e}", exc_info=True)
            self.status_label.set_text("Error updating settings")
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
        """Handle history entry selection."""
        buffer = self.text_view.get_buffer()
        buffer.set_text(entry.text)

        # Update status
        confidence_pct = int(entry.confidence * 100)
        self.status_label.set_text(
            f"Loaded from history (Confidence: {confidence_pct}%, "
            f"Duration: {entry.duration:.1f}s)"
        )

        self.set_state(AppState.READY)
        self.copy_button.set_sensitive(True)
        self.clear_button.set_sensitive(True)

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

        # Disconnect D-Bus client
        if self.client:
            self.client.disconnect()

        logger.info("Cleanup complete")
