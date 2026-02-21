"""Settings dialog for WhisperAloud configuration."""

import logging
from pathlib import Path
from typing import Optional

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk

from ..config import (
    NotificationConfig,
    PersistenceConfig,
    WhisperAloudConfig,
)
from .error_handler import InputValidator, ValidationError
from .settings_logic import (
    has_unsaved_changes,
    normalize_language_input,
    should_auto_close_on_focus_loss,
    should_block_close,
)

logger = logging.getLogger(__name__)


class SettingsDialog(Gtk.Window):
    """Settings dialog for configuring WhisperAloud."""

    def __init__(
        self,
        parent: Gtk.Window,
        config: WhisperAloudConfig,
        on_save_callback: Optional[callable] = None
    ) -> None:
        """
        Initialize the settings dialog.

        Args:
            parent: Parent window
            config: Current configuration
            on_save_callback: Callback function to run after saving
        """
        super().__init__()

        self.set_title("Settings")
        self.set_default_size(600, 500)
        self.set_modal(False)
        self.set_transient_for(parent)
        self.add_css_class("wa-dialog-window")

        # Store configuration
        self._config = config
        self._parent = parent
        self._on_save_callback = on_save_callback

        # Build UI
        self._build_ui()
        self._setup_keyboard_shortcuts()
        self._allow_close = False
        self._child_dialog_open = False
        self._dirty = False
        self._initial_ui_state = self._capture_ui_state()
        self._connect_dirty_tracking()
        self._focus_controller = Gtk.EventControllerFocus()
        self._focus_controller.connect("notify::contains-focus", self._on_focus_changed)
        self.add_controller(self._focus_controller)
        self.connect("close-request", self._on_close_request)

        logger.info("Settings dialog initialized")

    def _on_focus_changed(self, controller: Gtk.EventControllerFocus, _param: object) -> None:
        """Auto-close settings when focus leaves the entire window widget tree."""
        if should_auto_close_on_focus_loss(
            child_dialog_open=self._child_dialog_open,
            contains_focus=controller.get_contains_focus(),
            is_visible=self.is_visible(),
        ):
            GLib.idle_add(self.close)

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        """Intercept close to protect unsaved changes."""
        if should_block_close(
            allow_close=self._allow_close,
            unsaved_changes=self._has_unsaved_changes(),
        ):
            self._show_discard_confirmation()
            return True
        return False

    def _show_discard_confirmation(self) -> None:
        """Ask user to confirm discarding unsaved changes."""
        self._child_dialog_open = True
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Discard unsaved changes?",
        )
        dialog.format_secondary_text("You have unsaved changes in Settings.")
        dialog.add_button("Keep Editing", Gtk.ResponseType.CANCEL)
        dialog.add_button("Discard", Gtk.ResponseType.OK)

        def on_response(d: Gtk.MessageDialog, response: int) -> None:
            self._child_dialog_open = False
            d.close()
            if response == Gtk.ResponseType.OK:
                self._allow_close = True
                self.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _build_ui(self) -> None:
        """Build the settings dialog UI."""
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # Header bar
        header_bar = Gtk.HeaderBar()
        header_bar.add_css_class("wa-headerbar")
        header_bar.set_title_widget(Gtk.Label(label="Settings"))

        # Cancel button
        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.add_css_class("wa-ghost")
        cancel_button.set_tooltip_text("Discard and close (Esc)")
        cancel_button.connect("clicked", self._on_cancel_clicked)
        header_bar.pack_start(cancel_button)

        # Save button
        save_button = Gtk.Button(label="Save")
        save_button.add_css_class("suggested-action")
        save_button.add_css_class("wa-primary-action")
        save_button.set_tooltip_text("Save settings (Ctrl+S)")
        save_button.connect("clicked", self._on_save_clicked)
        header_bar.pack_end(save_button)
        self.set_titlebar(header_bar)

        # Stack for tabbed interface
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        # Stack switcher
        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.add_css_class("wa-tabs")
        stack_switcher.set_stack(self.stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        stack_switcher.set_margin_top(12)
        stack_switcher.set_margin_bottom(12)
        main_box.append(stack_switcher)

        # Add pages (simplified 2-tab layout)
        self._add_general_page()
        self._add_advanced_page()

        main_box.append(self.stack)

    def _setup_keyboard_shortcuts(self) -> None:
        """Enable dialog-local keyboard shortcuts for accessibility."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle keyboard shortcuts in settings dialog."""
        ctrl_pressed = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if keyval == Gdk.KEY_Escape:
            self._on_cancel_clicked(None)
            return True

        if ctrl_pressed and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self._on_save_clicked(None)
            return True

        return False

    def _add_general_page(self) -> None:
        """Add general settings page with common options."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)

        # --- Model Section ---
        model_section = Gtk.Label(label="Transcription")
        model_section.set_halign(Gtk.Align.START)
        model_section.add_css_class("heading")
        model_section.add_css_class("wa-section-title")
        page.append(model_section)

        # Model selector
        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        model_label = Gtk.Label(label="Model:")
        model_label.set_halign(Gtk.Align.START)
        model_label.set_hexpand(True)

        self.model_dropdown = Gtk.DropDown.new_from_strings([
            "tiny (fastest)", "base", "small", "medium", "large (most accurate)"
        ])
        models = ["tiny", "base", "small", "medium", "large"]
        if self._config.model.name in models:
            self.model_dropdown.set_selected(models.index(self._config.model.name))

        model_box.append(model_label)
        model_box.append(self.model_dropdown)
        page.append(model_box)

        # Language (hidden - managed by main window dropdown now)
        self.language_entry = Gtk.Entry()
        self.language_entry.set_text(self._config.transcription.language or "")
        self.language_entry.set_visible(False)  # Hidden, managed by main window

        # --- Audio Section ---
        audio_section = Gtk.Label(label="Audio Input")
        audio_section.set_halign(Gtk.Align.START)
        audio_section.add_css_class("heading")
        audio_section.add_css_class("wa-section-title")
        audio_section.set_margin_top(12)
        page.append(audio_section)

        # Input device selector
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        device_label = Gtk.Label(label="Microphone:")
        device_label.set_halign(Gtk.Align.START)
        device_label.set_hexpand(True)

        from ..audio import DeviceManager
        self._devices = DeviceManager.list_input_devices()
        device_names = [
            f"{d.name}" + (" ⭐" if d.is_default else "")
            for d in self._devices
        ]

        self.audio_device_dropdown = Gtk.DropDown.new_from_strings(device_names)

        current_device_id = self._config.audio.device_id
        if current_device_id is not None:
            for i, device in enumerate(self._devices):
                if device.id == current_device_id:
                    self.audio_device_dropdown.set_selected(i)
                    break
        else:
            for i, device in enumerate(self._devices):
                if device.is_default:
                    self.audio_device_dropdown.set_selected(i)
                    break

        device_box.append(device_label)
        device_box.append(self.audio_device_dropdown)
        page.append(device_box)

        # --- Clipboard Section ---
        clipboard_section = Gtk.Label(label="Clipboard")
        clipboard_section.set_halign(Gtk.Align.START)
        clipboard_section.add_css_class("heading")
        clipboard_section.add_css_class("wa-section-title")
        clipboard_section.set_margin_top(12)
        page.append(clipboard_section)

        # Auto-copy
        self.auto_copy_switch = Gtk.Switch()
        self.auto_copy_switch.set_active(self._config.clipboard.auto_copy)

        auto_copy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto_copy_label = Gtk.Label(label="Auto-copy to clipboard:")
        auto_copy_label.set_halign(Gtk.Align.START)
        auto_copy_label.set_hexpand(True)

        auto_copy_box.append(auto_copy_label)
        auto_copy_box.append(self.auto_copy_switch)
        page.append(auto_copy_box)

        # Auto-paste
        self.auto_paste_switch = Gtk.Switch()
        self.auto_paste_switch.set_active(self._config.clipboard.auto_paste)

        auto_paste_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto_paste_label = Gtk.Label(label="Auto-paste after transcription:")
        auto_paste_label.set_halign(Gtk.Align.START)
        auto_paste_label.set_hexpand(True)

        auto_paste_box.append(auto_paste_label)
        auto_paste_box.append(self.auto_paste_switch)
        page.append(auto_paste_box)

        # --- OSD Notifications Section ---
        notifications_section = Gtk.Label(label="OSD Notifications")
        notifications_section.set_halign(Gtk.Align.START)
        notifications_section.add_css_class("heading")
        notifications_section.add_css_class("wa-section-title")
        notifications_section.set_margin_top(12)
        page.append(notifications_section)

        notifications_help = Gtk.Label(
            label="Choose which desktop popups are shown while using WhisperAloud."
        )
        notifications_help.set_halign(Gtk.Align.START)
        notifications_help.add_css_class("wa-help")
        page.append(notifications_help)

        self.notifications_enabled_switch = Gtk.Switch()
        self.notifications_enabled_switch.set_active(self._config.notifications.enabled)
        self.notifications_enabled_switch.connect(
            "notify::active", self._on_notifications_master_toggled
        )

        notifications_enabled_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        notifications_enabled_label = Gtk.Label(label="Enable OSD notifications:")
        notifications_enabled_label.set_halign(Gtk.Align.START)
        notifications_enabled_label.set_hexpand(True)
        notifications_enabled_box.append(notifications_enabled_label)
        notifications_enabled_box.append(self.notifications_enabled_switch)
        page.append(notifications_enabled_box)

        self.notification_recording_started_switch = Gtk.Switch()
        self.notification_recording_started_switch.set_active(
            self._config.notifications.recording_started
        )
        self.notification_recording_stopped_switch = Gtk.Switch()
        self.notification_recording_stopped_switch.set_active(
            self._config.notifications.recording_stopped
        )
        self.notification_transcription_completed_switch = Gtk.Switch()
        self.notification_transcription_completed_switch.set_active(
            self._config.notifications.transcription_completed
        )
        self.notification_error_switch = Gtk.Switch()
        self.notification_error_switch.set_active(self._config.notifications.error)

        recording_started_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        recording_started_label = Gtk.Label(label="Recording started:")
        recording_started_label.set_halign(Gtk.Align.START)
        recording_started_label.set_hexpand(True)
        recording_started_box.append(recording_started_label)
        recording_started_box.append(self.notification_recording_started_switch)
        page.append(recording_started_box)

        recording_stopped_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        recording_stopped_label = Gtk.Label(label="Recording stopped:")
        recording_stopped_label.set_halign(Gtk.Align.START)
        recording_stopped_label.set_hexpand(True)
        recording_stopped_box.append(recording_stopped_label)
        recording_stopped_box.append(self.notification_recording_stopped_switch)
        page.append(recording_stopped_box)

        transcription_completed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        transcription_completed_label = Gtk.Label(label="Transcription completed:")
        transcription_completed_label.set_halign(Gtk.Align.START)
        transcription_completed_label.set_hexpand(True)
        transcription_completed_box.append(transcription_completed_label)
        transcription_completed_box.append(self.notification_transcription_completed_switch)
        page.append(transcription_completed_box)

        error_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        error_label = Gtk.Label(label="Errors:")
        error_label.set_halign(Gtk.Align.START)
        error_label.set_hexpand(True)
        error_box.append(error_label)
        error_box.append(self.notification_error_switch)
        page.append(error_box)

        self._set_notification_type_switches_sensitive(self._config.notifications.enabled)
        self._style_setting_rows_in_page(page)

        self.stack.add_titled(page, "general", "General")

    def _style_setting_rows_in_page(self, page: Gtk.Box) -> None:
        """Apply a shared style class to horizontal setting rows."""
        child = page.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Box) and child.get_orientation() == Gtk.Orientation.HORIZONTAL:
                child.add_css_class("wa-setting-row")
            child = child.get_next_sibling()

    def _set_notification_type_switches_sensitive(self, enabled: bool) -> None:
        """Enable/disable per-type notification switches from master toggle."""
        self.notification_recording_started_switch.set_sensitive(enabled)
        self.notification_recording_stopped_switch.set_sensitive(enabled)
        self.notification_transcription_completed_switch.set_sensitive(enabled)
        self.notification_error_switch.set_sensitive(enabled)

    def _on_notifications_master_toggled(self, switch: Gtk.Switch, _param: object) -> None:
        """Handle master notifications toggle changes."""
        self._set_notification_type_switches_sensitive(switch.get_active())

    def _capture_ui_state(self) -> dict:
        """Capture current form state for unsaved-change detection."""
        return {
            "model": self.model_dropdown.get_selected(),
            "language": self.language_entry.get_text(),
            "audio_device": self.audio_device_dropdown.get_selected(),
            "auto_copy": self.auto_copy_switch.get_active(),
            "auto_paste": self.auto_paste_switch.get_active(),
            "notifications_enabled": self.notifications_enabled_switch.get_active(),
            "notif_start": self.notification_recording_started_switch.get_active(),
            "notif_stop": self.notification_recording_stopped_switch.get_active(),
            "notif_done": self.notification_transcription_completed_switch.get_active(),
            "notif_error": self.notification_error_switch.get_active(),
            "compute_device": self.compute_device_dropdown.get_selected(),
            "sample_rate": self.sample_rate_entry.get_text(),
            "vad_enabled": self.vad_switch.get_active(),
            "vad_threshold": self.vad_threshold_entry.get_text(),
            "normalize": self.normalize_switch.get_active(),
            "paste_delay": self.paste_delay_entry.get_text(),
            "save_audio": self.save_audio_switch.get_active(),
            "dedupe_audio": self.deduplicate_audio_switch.get_active(),
            "auto_cleanup": self.auto_cleanup_switch.get_active(),
            "cleanup_days": self.cleanup_days_entry.get_text(),
            "max_entries": self.max_entries_entry.get_text(),
            "db_path": self.db_path_entry.get_text(),
            "audio_archive_path": self.audio_archive_entry.get_text(),
        }

    def _has_unsaved_changes(self) -> bool:
        """Return True when current UI state differs from initial state."""
        return has_unsaved_changes(self._initial_ui_state, self._capture_ui_state())

    def _mark_dirty(self, *_args: object) -> None:
        """Update dirty flag based on current form state."""
        self._dirty = self._has_unsaved_changes()

    def _connect_dirty_tracking(self) -> None:
        """Connect form inputs to unsaved-change tracking."""
        tracked_widgets = [
            self.model_dropdown,
            self.language_entry,
            self.audio_device_dropdown,
            self.auto_copy_switch,
            self.auto_paste_switch,
            self.notifications_enabled_switch,
            self.notification_recording_started_switch,
            self.notification_recording_stopped_switch,
            self.notification_transcription_completed_switch,
            self.notification_error_switch,
            self.compute_device_dropdown,
            self.sample_rate_entry,
            self.vad_switch,
            self.vad_threshold_entry,
            self.normalize_switch,
            self.paste_delay_entry,
            self.save_audio_switch,
            self.deduplicate_audio_switch,
            self.auto_cleanup_switch,
            self.cleanup_days_entry,
            self.max_entries_entry,
            self.db_path_entry,
            self.audio_archive_entry,
        ]

        for widget in tracked_widgets:
            if isinstance(widget, Gtk.Switch):
                widget.connect("notify::active", self._mark_dirty)
            elif isinstance(widget, Gtk.DropDown):
                widget.connect("notify::selected", self._mark_dirty)
            elif isinstance(widget, Gtk.Entry):
                widget.connect("changed", self._mark_dirty)

    def _add_advanced_page(self) -> None:
        """Add advanced settings page."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        scrolled.set_child(page)

        # --- Performance Section ---
        perf_section = Gtk.Label(label="Performance")
        perf_section.set_halign(Gtk.Align.START)
        perf_section.add_css_class("heading")
        perf_section.add_css_class("wa-section-title")
        page.append(perf_section)

        # Compute device
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        device_label = Gtk.Label(label="Compute device:")
        device_label.set_halign(Gtk.Align.START)
        device_label.set_hexpand(True)

        self.compute_device_dropdown = Gtk.DropDown.new_from_strings(["CPU", "CUDA (GPU)"])
        if self._config.model.device == "cuda":
            self.compute_device_dropdown.set_selected(1)

        device_box.append(device_label)
        device_box.append(self.compute_device_dropdown)
        page.append(device_box)

        # --- Audio Processing Section ---
        audio_section = Gtk.Label(label="Audio Processing")
        audio_section.set_halign(Gtk.Align.START)
        audio_section.add_css_class("heading")
        audio_section.add_css_class("wa-section-title")
        audio_section.set_margin_top(12)
        page.append(audio_section)

        # Sample rate
        rate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        rate_label = Gtk.Label(label="Sample rate (Hz):")
        rate_label.set_halign(Gtk.Align.START)
        rate_label.set_hexpand(True)

        self.sample_rate_entry = Gtk.Entry()
        self.sample_rate_entry.set_text(str(self._config.audio.sample_rate))

        rate_box.append(rate_label)
        rate_box.append(self.sample_rate_entry)
        page.append(rate_box)

        # VAD
        self.vad_switch = Gtk.Switch()
        self.vad_switch.set_active(self._config.audio.vad_enabled)

        vad_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vad_label = Gtk.Label(label="Voice activity detection:")
        vad_label.set_halign(Gtk.Align.START)
        vad_label.set_hexpand(True)

        vad_box.append(vad_label)
        vad_box.append(self.vad_switch)
        page.append(vad_box)

        # VAD threshold
        threshold_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        threshold_label = Gtk.Label(label="VAD threshold:")
        threshold_label.set_halign(Gtk.Align.START)
        threshold_label.set_hexpand(True)

        self.vad_threshold_entry = Gtk.Entry()
        self.vad_threshold_entry.set_text(str(self._config.audio.vad_threshold))

        threshold_box.append(threshold_label)
        threshold_box.append(self.vad_threshold_entry)
        page.append(threshold_box)

        # Normalize
        self.normalize_switch = Gtk.Switch()
        self.normalize_switch.set_active(self._config.audio.normalize_audio)

        normalize_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        normalize_label = Gtk.Label(label="Normalize audio:")
        normalize_label.set_halign(Gtk.Align.START)
        normalize_label.set_hexpand(True)

        normalize_box.append(normalize_label)
        normalize_box.append(self.normalize_switch)
        page.append(normalize_box)

        # Paste delay
        delay_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        delay_label = Gtk.Label(label="Paste delay (ms):")
        delay_label.set_halign(Gtk.Align.START)
        delay_label.set_hexpand(True)

        self.paste_delay_entry = Gtk.Entry()
        self.paste_delay_entry.set_text(str(self._config.clipboard.paste_delay_ms))

        delay_box.append(delay_label)
        delay_box.append(self.paste_delay_entry)
        page.append(delay_box)

        # --- History Section ---
        history_section = Gtk.Label(label="History & Storage")
        history_section.set_halign(Gtk.Align.START)
        history_section.add_css_class("heading")
        history_section.add_css_class("wa-section-title")
        history_section.set_margin_top(12)
        page.append(history_section)

        # Save audio
        self.save_audio_switch = Gtk.Switch()
        if self._config.persistence:
            self.save_audio_switch.set_active(self._config.persistence.save_audio)

        save_audio_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        save_audio_label = Gtk.Label(label="Save audio recordings:")
        save_audio_label.set_halign(Gtk.Align.START)
        save_audio_label.set_hexpand(True)

        save_audio_box.append(save_audio_label)
        save_audio_box.append(self.save_audio_switch)
        page.append(save_audio_box)

        # Deduplicate
        self.deduplicate_audio_switch = Gtk.Switch()
        if self._config.persistence:
            self.deduplicate_audio_switch.set_active(self._config.persistence.deduplicate_audio)

        dedupe_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        dedupe_label = Gtk.Label(label="Deduplicate audio:")
        dedupe_label.set_halign(Gtk.Align.START)
        dedupe_label.set_hexpand(True)

        dedupe_box.append(dedupe_label)
        dedupe_box.append(self.deduplicate_audio_switch)
        page.append(dedupe_box)

        # Auto-cleanup
        self.auto_cleanup_switch = Gtk.Switch()
        if self._config.persistence:
            self.auto_cleanup_switch.set_active(self._config.persistence.auto_cleanup_enabled)

        cleanup_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cleanup_label = Gtk.Label(label="Auto-cleanup old entries:")
        cleanup_label.set_halign(Gtk.Align.START)
        cleanup_label.set_hexpand(True)

        cleanup_box.append(cleanup_label)
        cleanup_box.append(self.auto_cleanup_switch)
        page.append(cleanup_box)

        # Cleanup days
        cleanup_days_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cleanup_days_label = Gtk.Label(label="Cleanup after (days):")
        cleanup_days_label.set_halign(Gtk.Align.START)
        cleanup_days_label.set_hexpand(True)

        self.cleanup_days_entry = Gtk.Entry()
        if self._config.persistence:
            self.cleanup_days_entry.set_text(str(self._config.persistence.auto_cleanup_days))
        else:
            self.cleanup_days_entry.set_text("90")

        cleanup_days_box.append(cleanup_days_label)
        cleanup_days_box.append(self.cleanup_days_entry)
        page.append(cleanup_days_box)

        # Max entries
        max_entries_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        max_entries_label = Gtk.Label(label="Maximum entries:")
        max_entries_label.set_halign(Gtk.Align.START)
        max_entries_label.set_hexpand(True)

        self.max_entries_entry = Gtk.Entry()
        if self._config.persistence:
            self.max_entries_entry.set_text(str(self._config.persistence.max_entries))
        else:
            self.max_entries_entry.set_text("10000")

        max_entries_box.append(max_entries_label)
        max_entries_box.append(self.max_entries_entry)
        page.append(max_entries_box)

        # Database path
        db_path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        db_path_label = Gtk.Label(label="Database path:")
        db_path_label.set_halign(Gtk.Align.START)
        db_path_label.set_hexpand(True)

        self.db_path_entry = Gtk.Entry()
        if self._config.persistence and self._config.persistence.db_path:
            self.db_path_entry.set_text(str(self._config.persistence.db_path))
        else:
            default_path = Path.home() / ".local/share/whisper_aloud/history.db"
            self.db_path_entry.set_text(str(default_path))
        self.db_path_entry.set_hexpand(True)

        db_path_box.append(db_path_label)
        db_path_box.append(self.db_path_entry)
        page.append(db_path_box)

        # Audio archive path
        audio_path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        audio_path_label = Gtk.Label(label="Audio archive path:")
        audio_path_label.set_halign(Gtk.Align.START)
        audio_path_label.set_hexpand(True)

        self.audio_archive_entry = Gtk.Entry()
        if self._config.persistence and self._config.persistence.audio_archive_path:
            self.audio_archive_entry.set_text(str(self._config.persistence.audio_archive_path))
        else:
            default_path = Path.home() / ".local/share/whisper_aloud/audio"
            self.audio_archive_entry.set_text(str(default_path))
        self.audio_archive_entry.set_hexpand(True)

        audio_path_box.append(audio_path_label)
        audio_path_box.append(self.audio_archive_entry)
        page.append(audio_path_box)

        self._style_setting_rows_in_page(page)
        self.stack.add_titled(scrolled, "advanced", "Advanced")

    def _on_save_clicked(self, button: Optional[Gtk.Button]) -> None:
        """
        Handle save button click.

        Args:
            button: The button that was clicked (or None from keyboard shortcut)
        """
        logger.info("Saving settings")

        try:
            logger.debug("Updating model config")
            # Update model config
            models = ["tiny", "base", "small", "medium", "large"]
            self._config.model.name = models[self.model_dropdown.get_selected()]

            # Validate language code
            lang = self.language_entry.get_text()
            try:
                self._config.transcription.language = normalize_language_input(lang)
            except ValueError as e:
                raise ValidationError(str(e)) from e

            devices = ["cpu", "cuda"]
            selected_compute_idx = self.compute_device_dropdown.get_selected()
            if selected_compute_idx != -1 and selected_compute_idx < len(devices):
                self._config.model.device = devices[selected_compute_idx]

            logger.debug("Updating audio config")
            # Update audio config
            selected_device_idx = self.audio_device_dropdown.get_selected()
            # Check if a valid device is selected (index != -1)
            if selected_device_idx != -1 and selected_device_idx < len(self._devices):
                self._config.audio.device_id = self._devices[selected_device_idx].id

            # Update channels based on selected device
            if self._config.audio.device_id is not None:
                from ..audio import DeviceManager
                device = DeviceManager.get_device_by_id(self._config.audio.device_id)
                # If device is mono-only, force mono. If stereo-capable, respect config or default to stereo?
                # For now, let's just ensure we don't ask for more channels than available
                if device.channels < self._config.audio.channels:
                    self._config.audio.channels = device.channels

            # Validate audio settings
            self._config.audio.sample_rate = InputValidator.validate_integer(
                self.sample_rate_entry.get_text(),
                min_value=8000,
                max_value=48000,
                field_name="Sample rate"
            )
            self._config.audio.vad_enabled = self.vad_switch.get_active()
            self._config.audio.vad_threshold = InputValidator.validate_float(
                self.vad_threshold_entry.get_text(),
                min_value=0.0,
                max_value=1.0,
                field_name="VAD threshold"
            )
            self._config.audio.normalize_audio = self.normalize_switch.get_active()

            logger.debug("Updating clipboard config")
            # Update clipboard config
            self._config.clipboard.auto_copy = self.auto_copy_switch.get_active()
            self._config.clipboard.auto_paste = self.auto_paste_switch.get_active()
            self._config.clipboard.paste_delay_ms = InputValidator.validate_integer(
                self.paste_delay_entry.get_text(),
                min_value=0,
                max_value=5000,
                field_name="Paste delay"
            )

            logger.debug("Updating notifications config")
            if not self._config.notifications:
                self._config.notifications = NotificationConfig()
            self._config.notifications.enabled = self.notifications_enabled_switch.get_active()
            self._config.notifications.recording_started = (
                self.notification_recording_started_switch.get_active()
            )
            self._config.notifications.recording_stopped = (
                self.notification_recording_stopped_switch.get_active()
            )
            self._config.notifications.transcription_completed = (
                self.notification_transcription_completed_switch.get_active()
            )
            self._config.notifications.error = self.notification_error_switch.get_active()

            logger.debug("Updating persistence config")
            # Update persistence config
            if not self._config.persistence:
                self._config.persistence = PersistenceConfig()

            self._config.persistence.save_audio = self.save_audio_switch.get_active()
            self._config.persistence.deduplicate_audio = self.deduplicate_audio_switch.get_active()
            self._config.persistence.auto_cleanup_enabled = self.auto_cleanup_switch.get_active()
            self._config.persistence.auto_cleanup_days = InputValidator.validate_integer(
                self.cleanup_days_entry.get_text(),
                min_value=1,
                max_value=36500,  # ~100 years
                field_name="Cleanup days"
            )
            self._config.persistence.max_entries = InputValidator.validate_integer(
                self.max_entries_entry.get_text(),
                min_value=100,
                max_value=1000000,
                field_name="Maximum entries"
            )

            # Update paths (empty string means use defaults)
            db_path_text = self.db_path_entry.get_text().strip()
            if db_path_text:
                self._config.persistence.db_path = Path(db_path_text)

            audio_path_text = self.audio_archive_entry.get_text().strip()
            if audio_path_text:
                self._config.persistence.audio_archive_path = Path(audio_path_text)

            logger.debug("Saving config to file")
            # Save to file using config's built-in save method
            self._config.save()

            logger.debug("Triggering save callback")
            # Trigger callback
            if self._on_save_callback:
                self._on_save_callback()

            # Reset unsaved-change baseline after successful persistence.
            self._initial_ui_state = self._capture_ui_state()
            self._dirty = False

            logger.debug("Showing success message")
            # Show success message
            self._show_message("Settings saved successfully", Gtk.MessageType.INFO, on_close=self.close)

        except ValidationError as e:
            logger.error(f"Validation error: {e}")
            self._show_message(f"Invalid input: {e}", Gtk.MessageType.ERROR)
        except ValueError as e:
            logger.error(f"Invalid input: {e}")
            self._show_message(f"Invalid input: {e}", Gtk.MessageType.ERROR)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
            self._show_message(f"Error saving settings: {e}", Gtk.MessageType.ERROR)


    def _on_cancel_clicked(self, button: Optional[Gtk.Button]) -> None:
        """
        Handle cancel button click.

        Args:
            button: The button that was clicked (or None from keyboard shortcut)
        """
        logger.info("Settings dialog cancelled")
        self.close()

    def _show_message(self, message: str, message_type: Gtk.MessageType, on_close: Optional[callable] = None) -> None:
        """
        Show a message dialog.

        Args:
            message: Message to display
            message_type: Type of message (INFO, WARNING, ERROR)
            on_close: Optional callback to run when dialog closes
        """
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=message_type,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        self._child_dialog_open = True

        def on_response(d, r):
            self._child_dialog_open = False
            d.close()
            if on_close:
                on_close()

        dialog.connect("response", on_response)
        dialog.present()
