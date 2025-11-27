"""Main application window for WhisperAloud."""

import logging
import threading
from typing import Optional
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from ..config import WhisperAloudConfig
from ..transcriber import Transcriber, TranscriptionResult
from ..audio import AudioRecorder, AudioLevel
from ..clipboard import ClipboardManager
from .utils import AppState, format_duration
from .level_meter import LevelMeterPanel
from .settings_dialog import SettingsDialog
from .history_panel import HistoryPanel
from .status_bar import StatusBar
from ..persistence.history_manager import HistoryManager
from .error_handler import (
    ErrorDialog,
    ErrorSeverity,
    handle_audio_device_error,
    handle_model_load_error,
    handle_transcription_error,
    handle_clipboard_error
)
from ..utils.config_persistence import save_config_to_file

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

        # Initialize backend components (will be lazy-loaded)
        self.config: Optional[WhisperAloudConfig] = None
        self.transcriber: Optional[Transcriber] = None
        self.recorder: Optional[AudioRecorder] = None
        self.clipboard_manager: Optional[ClipboardManager] = None
        self.history_manager: Optional[HistoryManager] = None
        self.session_id: Optional[str] = None

        # Build UI
        self._build_ui()

        # Set window properties
        self.set_title("WhisperAloud")
        self.set_default_size(600, 500)

        # Set up keyboard shortcuts
        self._setup_keyboard_shortcuts()

        # Load configuration and initialize components in background
        GLib.idle_add(self._init_components_async)

        logger.info("Main window initialized")

    def _build_ui(self) -> None:
        """Build the user interface."""
        # Create main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # Create header bar
        header_bar = Gtk.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label="WhisperAloud"))

        # History toggle button
        self.history_toggle = Gtk.ToggleButton()
        self.history_toggle.set_icon_name("sidebar-show-symbolic")
        self.history_toggle.set_tooltip_text("Toggle History")
        self.history_toggle.set_active(True)
        self.history_toggle.connect("toggled", self._on_history_toggled)
        header_bar.pack_start(self.history_toggle)

        # Settings button
        settings_button = Gtk.Button()
        settings_button.set_icon_name("preferences-system-symbolic")
        settings_button.set_tooltip_text("Settings")
        settings_button.connect("clicked", self._on_settings_clicked)
        header_bar.pack_end(settings_button)

        # Language toggle button
        self.lang_button = Gtk.Button(label="ES")
        self.lang_button.set_tooltip_text("Toggle Language (ES/EN)")
        self.lang_button.connect("clicked", self._on_language_toggle_clicked)
        header_bar.pack_end(self.lang_button)

        main_box.append(header_bar)

        # Main content area (Paned)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_position(400)  # Initial split position
        self.paned.set_wide_handle(True)
        main_box.append(self.paned)

        # Left side: Recording and Transcription
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        # Status label
        self.status_label = Gtk.Label(label="Loading...")
        self.status_label.set_margin_top(12)
        self.status_label.set_margin_bottom(12)
        left_box.append(self.status_label)

        # Recording panel placeholder
        recording_panel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        recording_panel_box.set_margin_start(24)
        recording_panel_box.set_margin_end(24)
        recording_panel_box.set_margin_top(12)
        recording_panel_box.set_margin_bottom(12)

        # Record button
        self.record_button = Gtk.Button(label="Start Recording")
        self.record_button.add_css_class("suggested-action")
        self.record_button.add_css_class("pill")
        self.record_button.set_size_request(-1, 60)
        self.record_button.set_sensitive(False)  # Disabled until model loads
        self.record_button.set_tooltip_text("Start/stop recording (Space)")
        self.record_button.connect("clicked", self._on_record_button_clicked)
        recording_panel_box.append(self.record_button)

        # Timer label
        self.timer_label = Gtk.Label(label="0:00")
        self.timer_label.add_css_class("title-1")
        recording_panel_box.append(self.timer_label)

        # Level meter
        self.level_meter = LevelMeterPanel()
        self.level_meter.set_margin_top(12)
        recording_panel_box.append(self.level_meter)

        left_box.append(recording_panel_box)

        # Separator
        left_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Transcription view placeholder
        transcription_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        transcription_box.set_margin_start(24)
        transcription_box.set_margin_end(24)
        transcription_box.set_margin_top(12)
        transcription_box.set_margin_bottom(12)
        transcription_box.set_vexpand(True)

        # Scrolled window with text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.text_view = Gtk.TextView()
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
        self.copy_button.set_sensitive(False)
        self.copy_button.set_tooltip_text("Copy transcription to clipboard (Ctrl+C)")
        self.copy_button.connect("clicked", self._on_copy_clicked)
        button_box.append(self.copy_button)

        self.clear_button = Gtk.Button(label="Clear")
        self.clear_button.set_sensitive(False)
        self.clear_button.set_tooltip_text("Clear transcription text (Escape)")
        self.clear_button.connect("clicked", self._on_clear_clicked)
        button_box.append(self.clear_button)

        transcription_box.append(button_box)
        left_box.append(transcription_box)

        # Add left box to paned
        self.paned.set_start_child(left_box)
        
        # Right side: History Panel (placeholder until initialized)
        self.history_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.history_container.set_size_request(250, -1)
        self.paned.set_end_child(self.history_container)

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
        state: 'Gdk.ModifierType'
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

        return False

    def _init_components_async(self) -> bool:
        """
        Initialize backend components asynchronously.

        Returns:
            False to remove this idle callback
        """
        def _load_in_thread():
            """Load components in background thread."""
            try:
                logger.info("Loading configuration and model...")
                self.config = WhisperAloudConfig.load()
                self.transcriber = Transcriber(self.config)

                # Load model (blocking operation)
                self.transcriber.load_model()

                # Initialize other components
                self.recorder = AudioRecorder(
                    self.config.audio,
                    level_callback=self._on_audio_level
                )
                self.clipboard_manager = ClipboardManager(self.config.clipboard)
                
                # Initialize history manager
                import uuid
                self.session_id = str(uuid.uuid4())
                self.history_manager = HistoryManager(self.config.persistence)

                GLib.idle_add(self._on_components_loaded)

            except Exception as e:
                logger.error(f"Failed to initialize components: {e}", exc_info=True)
                GLib.idle_add(self._on_load_error, str(e))

        # Start loading in background thread
        threading.Thread(target=_load_in_thread, daemon=True).start()
        return False

    def _on_components_loaded(self) -> bool:
        """
        Called when components finish loading (main thread).

        Returns:
            False to remove this idle callback
        """
        logger.info("Components loaded successfully")
        self.status_label.set_text("Ready")
        self.record_button.set_sensitive(True)
        self.set_state(AppState.IDLE)
        
        # Initialize History Panel
        if self.history_manager:
            self.history_panel = HistoryPanel(self.history_manager)
            self.history_panel.connect("entry-selected", self._on_history_entry_selected)
            self.history_container.append(self.history_panel)

        # Update model info display
        self._update_model_info()
            
        return False

    def _on_load_error_sync(self, error_msg: str) -> None:
        """
        Called when component loading fails (synchronous version).

        Args:
            error_msg: Error message
        """
        logger.error(f"Component load error: {error_msg}")
        self.status_label.set_text("Error loading components")
        self.set_state(AppState.ERROR)

        # Show detailed error dialog
        handle_model_load_error(self, Exception(error_msg))

    def _on_load_error(self, error_msg: str) -> bool:
        """
        Called when component loading fails (asynchronous version).

        Args:
            error_msg: Error message

        Returns:
            False to remove this idle callback
        """
        self._on_load_error_sync(error_msg)
        return False

    def set_state(self, new_state: AppState) -> None:
        """
        Update application state and UI accordingly.

        Args:
            new_state: The new state to transition to
        """
        logger.info(f"State transition: {self._state.value} -> {new_state.value}")
        self._state = new_state

        try:
            # Update UI based on state
            if new_state == AppState.IDLE:
                logger.debug("Setting UI for IDLE state")
                self.record_button.set_label("Start Recording")
                self.record_button.set_sensitive(True)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
                self.timer_label.set_text("0:00")

            elif new_state == AppState.RECORDING:
                logger.debug("Setting UI for RECORDING state")
                self.record_button.set_label("Stop Recording")
                self.record_button.set_sensitive(True)
                self.record_button.remove_css_class("suggested-action")
                self.record_button.add_css_class("destructive-action")

            elif new_state == AppState.TRANSCRIBING:
                logger.debug("Setting UI for TRANSCRIBING state")
                self.record_button.set_label("Transcribing...")
                self.record_button.set_sensitive(False)
                self.status_label.set_text("Transcribing...")

            elif new_state == AppState.READY:
                logger.debug("Setting UI for READY state")
                self.record_button.set_label("Record Again")
                self.record_button.set_sensitive(True)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
                self.status_label.set_text("Ready")
                self.copy_button.set_sensitive(True)
                self.clear_button.set_sensitive(True)

            elif new_state == AppState.ERROR:
                logger.debug("Setting UI for ERROR state")
                self.record_button.set_label("Try Again")
                self.record_button.set_sensitive(True)
                self.record_button.remove_css_class("destructive-action")
                self.record_button.add_css_class("suggested-action")
        except Exception as e:
            logger.error(f"Error in set_state: {e}", exc_info=True)

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

    def _on_audio_level(self, level: AudioLevel) -> None:
        """
        Handle audio level updates from recorder (audio thread).

        Args:
            level: Audio level information
        """
        # Update level meter from main thread
        GLib.idle_add(self._update_level_meter, level.rms, level.peak, level.db)

    def _update_level_meter(self, rms: float, peak: float, db: float) -> bool:
        """
        Update level meter (main thread).

        Args:
            rms: RMS level
            peak: Peak level
            db: Decibel level

        Returns:
            False to remove this idle callback
        """
        self.level_meter.update_level(rms, peak, db)
        return False

    def _start_recording(self) -> None:
        """Start audio recording."""
        try:
            logger.info("Starting audio recording")

            # Reset level meter
            self.level_meter.reset()

            self.recorder.start()
            self.set_state(AppState.RECORDING)
            self.status_label.set_text("Recording...")

            # Start timer
            self._timer_active = True
            GLib.timeout_add(100, self._update_timer)

        except Exception as e:
            logger.error(f"Failed to start recording: {e}", exc_info=True)
            self.status_label.set_text("Recording failed")
            self.set_state(AppState.ERROR)

            # Show detailed error dialog
            handle_audio_device_error(self, e)

    def _stop_recording_and_transcribe(self) -> None:
        """Stop recording and start transcription in background thread."""
        logger.info("Stopping recording and starting transcription")

        # Stop timer
        self._timer_active = False

        # Update UI
        self.set_state(AppState.TRANSCRIBING)

        # Start transcription in background thread
        threading.Thread(target=self._transcribe_async, daemon=True).start()

    def _update_timer(self) -> bool:
        """
        Update recording timer.

        Returns:
            True to continue timer, False to stop
        """
        if not self._timer_active:
            return False

        if self.recorder and self.recorder.is_recording:
            duration = self.recorder.recording_duration
            self.timer_label.set_text(format_duration(duration))

        return True  # Continue timer

    def _transcribe_async(self) -> None:
        """
        Transcribe audio in background thread.

        IMPORTANT: This runs in a worker thread, not the main thread.
        All UI updates must use GLib.idle_add().
        """
        try:
            # Stop recording and get audio data
            logger.info("Stopping recorder and getting audio data")
            audio = self.recorder.stop()

            if audio is None or len(audio) == 0:
                logger.warning("No audio data recorded")
                GLib.idle_add(self._on_transcription_error, "No audio recorded")
                return

            logger.info(f"Transcribing {len(audio)} audio samples...")

            # Transcribe (blocking operation, 5-30 seconds)
            result = self.transcriber.transcribe_numpy(
                audio,
                sample_rate=self.config.audio.sample_rate
            )

            logger.info(f"Transcription complete: {len(result.text)} characters")
            
            # Save to history (IN THIS THREAD)
            if self.history_manager:
                try:
                    self.history_manager.add_transcription(
                        result=result,
                        audio=audio if self.config.persistence.save_audio else None,
                        sample_rate=self.config.audio.sample_rate,
                        session_id=self.session_id
                    )
                except Exception as e:
                    logger.error(f"Failed to save history: {e}", exc_info=True)

            # Update UI from main thread
            GLib.idle_add(self._on_transcription_complete, result)

        except Exception as e:
            logger.error(f"Transcription failed: {e}", exc_info=True)
            GLib.idle_add(self._on_transcription_error, str(e))

    def _on_transcription_complete(self, result: TranscriptionResult) -> bool:
        """
        Called when transcription completes (main thread).

        Args:
            result: Transcription result

        Returns:
            False to remove this idle callback
        """
        logger.info("Transcription result received")

        # Display transcription text
        buffer = self.text_view.get_buffer()
        buffer.set_text(result.text)

        # Update status
        confidence_pct = int(result.confidence * 100)
        self.status_label.set_text(
            f"Ready (Confidence: {confidence_pct}%, "
            f"Duration: {result.duration:.1f}s)"
        )

        # Update state
        self.set_state(AppState.READY)

        # Auto-copy if enabled
        if self.config.clipboard.auto_copy:
            self._copy_to_clipboard()

        # Refresh history panel
        if hasattr(self, 'history_panel'):
            self.history_panel.refresh_recent()

        return False

    def _on_transcription_error(self, error_msg: str) -> bool:
        """
        Called when transcription fails (main thread).

        Args:
            error_msg: Error message

        Returns:
            False to remove this idle callback
        """
        logger.error(f"Transcription error: {error_msg}")
        self.status_label.set_text("Transcription failed")
        self.set_state(AppState.ERROR)

        # Show detailed error dialog
        handle_transcription_error(self, Exception(error_msg))

        return False

    def _on_copy_clicked(self, button: Gtk.Button) -> None:
        """
        Handle copy button click.

        Args:
            button: The button that was clicked
        """
        logger.debug("Copy button clicked")
        self._copy_to_clipboard()

    def _copy_to_clipboard(self) -> None:
        """Copy transcription text to clipboard."""
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
            success = self.clipboard_manager.copy(text)
            if success:
                self.status_label.set_text("Copied to clipboard")
                logger.info(f"Copied {len(text)} characters to clipboard")
            else:
                self.status_label.set_text("Saved to fallback file")
                logger.warning("Clipboard copy failed, using fallback")
                ErrorDialog.show_error(
                    parent=self,
                    title="Clipboard Warning",
                    message="Text saved to fallback file:\n/tmp/whisper_aloud_clipboard.txt",
                    severity=ErrorSeverity.WARNING
                )
        except Exception as e:
            logger.error(f"Clipboard error: {e}", exc_info=True)
            self.status_label.set_text("Clipboard error")
            handle_clipboard_error(self, e)

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

    def _on_language_toggle_clicked(self, button: Gtk.Button) -> None:
        """
        Handle language toggle button click.
        
        Args:
            button: The button that was clicked
        """
        current_lang = self.config.transcription.language
        new_lang = "en" if current_lang == "es" else "es"
        
        logger.info(f"Toggling language: {current_lang} -> {new_lang}")
        
        # Update config
        self.config.transcription.language = new_lang

        # Save config to file
        try:
            save_config_to_file(self.config)

            # Update UI
            self.lang_button.set_label(new_lang.upper())
            self._update_model_info()

            # Reload model in background
            logger.info("Reloading model with new language")
            threading.Thread(target=self._reload_model, daemon=True).start()

        except Exception as e:
            logger.error(f"Failed to toggle language: {e}", exc_info=True)

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
        """Handle settings saved event."""
        logger.info("Settings saved, checking for changes...")

        try:
            # Reload configuration from file to get latest changes
            logger.debug("Reloading configuration from file")
            new_config = WhisperAloudConfig.load()
            logger.debug("Configuration reloaded successfully")

            # Check if model settings changed
            model_changed = (
                new_config.model.name != self.config.model.name or
                new_config.model.device != self.config.model.device or
                new_config.model.compute_type != self.config.model.compute_type or
                new_config.transcription.language != self.config.transcription.language
            )

            # Check if audio settings changed (that require recorder re-init)
            audio_changed = (
                new_config.audio.device_id != self.config.audio.device_id or
                new_config.audio.sample_rate != self.config.audio.sample_rate or
                new_config.audio.channels != self.config.audio.channels
            )

            logger.info(f"Model changed: {model_changed}, Audio changed: {audio_changed}")

            self.config = new_config

            if model_changed:
                logger.info("Model settings changed, reloading model")
                # Reload model in background
                threading.Thread(target=self._reload_model, daemon=True).start()

            if audio_changed:
                logger.info("Audio settings changed, re-initializing recorder")
                self._reinit_recorder()

            # Update model info display
            self._update_model_info()
            
        except Exception as e:
            logger.error(f"Error in _on_settings_saved: {e}", exc_info=True)
            self.status_label.set_text("Error updating settings")
            self.set_state(AppState.ERROR)

    def _reinit_recorder(self) -> None:
        """Re-initialize the audio recorder with new config."""
        try:
            if self.recorder:
                logger.debug("Re-initializing recorder")
                # Stop any active recording first
                if self.recorder.is_recording:
                    logger.debug("Cancel active recording")
                    self.recorder.cancel()

                # Ensure the old recorder releases resources
                logger.debug("Delete old recorder")
                del self.recorder

            # Create new recorder instance with updated config
            logger.debug("Create new recorder instance")
            self.recorder = AudioRecorder(
                self.config.audio,
                level_callback=self._on_audio_level
            )
            logger.info("Recorder re-initialized successfully")
        except Exception as e:
            logger.error(f"Failed to re-init recorder: {e}", exc_info=True)
            # Show error dialog
            handle_audio_device_error(self, e)

    def _reload_model(self) -> None:
        """Reload the Whisper model synchronously."""
        logger.info("Starting model reload")
        self.status_label.set_text("Reloading model...")
        self.record_button.set_sensitive(False)

        # Stop resource monitoring to avoid conflicts
        logger.debug("Stopping resource monitoring")
        self.status_bar.cleanup()

        try:
            logger.debug("Unload existing model")
            if self.transcriber:
                self.transcriber.unload_model()

            logger.debug("Create new transcriber")
            self.transcriber = Transcriber(self.config)

            logger.debug("Load new model")
            self.transcriber.load_model()
            logger.info("Model loaded successfully")

            # Re-initialize recorder with new config if needed
            self._reinit_recorder()

            logger.debug("Model reload complete, updating UI")
            self._on_model_reloaded_sync()
        except Exception as e:
            logger.error(f"Failed to reload model: {e}", exc_info=True)
            # Revert to previous valid config if possible, or just show error
            # For now, show error dialog
            GLib.idle_add(self._on_load_error_sync, str(e))

    def _on_model_reloaded_sync(self) -> None:
        """Called when model is reloaded (synchronous version)."""
        try:
            logger.info("Model reloaded successfully")
            logger.debug("Setting status label to success message")
            self.status_label.set_text("Model reloaded successfully")
            logger.debug("Enabling record button")
            self.record_button.set_sensitive(True)
            logger.debug("Setting state to IDLE")
            self.set_state(AppState.IDLE)  # Reset state to IDLE
            logger.debug("Updating model info display")
            self._update_model_info()
            logger.debug("Restarting resource monitoring")
            self.status_bar.start_monitoring()
            logger.debug("Model reload UI update complete")
        except Exception as e:
            logger.error(f"Error in _on_model_reloaded_sync: {e}", exc_info=True)

    def _on_model_reloaded(self) -> bool:
        """Called when model is reloaded (asynchronous version)."""
        self._on_model_reloaded_sync()
        return False

    def _on_history_toggled(self, button: Gtk.ToggleButton) -> None:
        """Handle history toggle button."""
        is_visible = button.get_active()
        button.set_icon_name("sidebar-show-symbolic" if is_visible else "sidebar-hide-symbolic")
        
        if is_visible:
            self.paned.set_end_child(self.history_container)
        else:
            self.paned.set_end_child(None)

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
        self.status_bar.set_model_info(
            self.config.model.name,
            self.config.model.device,
            self.config.transcription.language
        )

        # Update language button
        lang = self.config.transcription.language or "auto"
        self.lang_button.set_label(lang.upper())

    def cleanup(self) -> None:
        """Clean up resources before shutdown."""
        logger.info("Cleaning up main window resources")

        # Stop status bar monitoring
        if hasattr(self, 'status_bar'):
            self.status_bar.cleanup()

        # Stop recording if active
        if self.recorder and hasattr(self.recorder, 'is_recording') and self.recorder.is_recording:
            logger.info("Stopping active recording")
            self.recorder.cancel()

        # Unload model to free memory
        if self.transcriber and hasattr(self.transcriber, 'unload_model'):
            logger.info("Unloading Whisper model")
            self.transcriber.unload_model()

        logger.info("Cleanup complete")
