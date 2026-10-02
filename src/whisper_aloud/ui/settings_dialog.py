"""Settings dialog for WhisperAloud configuration."""

import logging
import os
from pathlib import Path
from typing import Optional

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk

from ..config import (
    NotificationConfig,
    PersistenceConfig,
    WhisperAloudConfig,
)
from ..stt import catalog
from . import dialogs
from .download_logic import compute_ui_state, requires_license_consent
from .error_handler import InputValidator, ValidationError
from .model_picker_logic import (
    badges_for,
    default_index,
    device_for_index,
    device_row_index,
    download_state_for,
    entries_for_engine,
    needs_download_note,
    row_label,
    save_selection,
    selectable_entries,
)
from .settings_logic import (
    has_unsaved_changes,
    normalize_language_input,
    should_block_close,
)


def _fmt_size(size_mb: Optional[int]) -> str:
    """Human-facing size string for a catalog entry, or "—" when unknown."""
    return f"{size_mb} MB" if size_mb is not None else "—"


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
        self.set_default_size(600, 700)
        self.set_modal(False)
        self.set_resizable(True)
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
        self.connect("close-request", self._on_close_request)

        logger.info("Settings dialog initialized")

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
        dialogs.confirm(
            parent=self,
            heading="Discard unsaved changes?",
            body="You have unsaved changes in Settings.",
            accept_label="Discard",
            on_accept=self._on_discard_confirmed,
            destructive=True,
            cancel_label="Keep Editing",
            on_response=self._on_discard_confirmation_response,
        )

    def _on_discard_confirmation_response(self, _response: str) -> None:
        """Runs for every response (Keep Editing or Discard)."""
        self._child_dialog_open = False

    def _on_discard_confirmed(self) -> None:
        """Runs only for the 'Discard' (accept) response."""
        self._allow_close = True
        self.close()

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

        # Add pages
        self._add_general_page()
        self._add_history_page()
        self._add_advanced_page()

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.stack)
        main_box.append(scrolled)

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

    def _on_register_hotkey_clicked(self, _btn: Gtk.Button) -> None:
        """Check hotkey status and give actionable feedback."""
        # Query daemon
        backend = "unknown"
        try:
            from whisper_aloud.service.client import WhisperAloudClient

            client = WhisperAloudClient()
            status = client.get_status()
            backend = status.get("hotkey_backend", "none")
        except Exception:
            self._hotkey_status_label.set_text("Daemon not running")
            return

        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()

        if backend == "keybinder":
            self._hotkey_status_label.set_text("Active (keybinder)")
        elif backend == "portal" and "GNOME" not in desktop:
            # Portal works on KDE/wlroots
            self._hotkey_status_label.set_text("Active (XDG Portal)")
        elif "GNOME" in desktop:
            # On GNOME, portal silently rejects native apps — use extension
            self._hotkey_status_label.set_text("Use GNOME extension")
        else:
            self._hotkey_status_label.set_text("Set system shortcut manually")

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

        # Model selector — catalog-driven boxed-list (WHISP-42)
        self._model_list_box = Gtk.ListBox()
        self._model_list_box.add_css_class("boxed-list")
        self._model_list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        self.model_row = Adw.ComboRow()
        self.model_row.set_title("Model")
        self._model_entries: list = []  # populated by _refresh_model_row()
        self._suppress_model_sync = False

        # Notice widgets must exist *before* the first _refresh_model_row() call below:
        # it calls _update_notices(), which reads these (WHISP-42 audit BLOCKER #1 —
        # previously built after _refresh_model_row(), causing an AttributeError on
        # every dialog open).
        self._download_notice = Gtk.Label(wrap=True, xalign=0)
        self._download_notice.add_css_class("dim-label")
        self._download_notice.set_visible(False)

        self._license_notice = Gtk.Label(wrap=True, xalign=0)
        self._license_notice.add_css_class("dim-label")
        self._license_notice.set_visible(False)

        # Shown when an Advanced->Engine switch has no entries for the configured
        # language and _refresh_model_row() fell back to that engine's full list
        # (model_picker_logic.entries_for_engine, WHISP-42 audit HIGH #2).
        self._language_fallback_notice = Gtk.Label(wrap=True, xalign=0)
        self._language_fallback_notice.add_css_class("dim-label")
        self._language_fallback_notice.set_visible(False)

        # In-app model download row (WHISP-68): Download button, progress bar,
        # Cancel, and an error label. Visibility/content is entirely driven by
        # download_logic.compute_ui_state() -- see _update_download_row().
        # State must also exist before _refresh_model_row() below, since it
        # ends in _update_notices() -> _update_download_row().
        self._download_client = None
        self._downloading = False
        self._download_active_entry: Optional[tuple] = None  # (engine, name)
        self._download_progress_state: Optional[tuple] = None  # (bytes_done, bytes_total)
        self._download_error: Optional[str] = None

        self._download_row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._download_row_box.set_visible(False)
        self._download_button = Gtk.Button(label="Download")
        self._download_button.connect("clicked", self._on_download_clicked)
        self._download_progress = Gtk.ProgressBar()
        self._download_progress.set_hexpand(True)
        self._download_progress.set_valign(Gtk.Align.CENTER)
        self._download_cancel_button = Gtk.Button(label="Cancel")
        self._download_cancel_button.connect("clicked", self._on_download_cancel_clicked)
        self._download_row_box.append(self._download_button)
        self._download_row_box.append(self._download_progress)
        self._download_row_box.append(self._download_cancel_button)

        self._download_error_label = Gtk.Label(wrap=True, xalign=0)
        self._download_error_label.add_css_class("error")
        self._download_error_label.set_visible(False)

        self._refresh_model_row()
        self._model_list_box.append(self.model_row)
        page.append(self._model_list_box)
        page.append(self._download_notice)
        page.append(self._download_row_box)
        page.append(self._download_error_label)
        page.append(self._license_notice)
        page.append(self._language_fallback_notice)

        self.model_row.connect("notify::selected", self._on_model_row_changed)

        # Advanced override: Engine, Device, Compute type (WHISP-42)
        self.advanced_row = Adw.ExpanderRow()
        self.advanced_row.set_title("Advanced")
        self.advanced_row.set_expanded(False)

        self._engine_keys = catalog.engines()
        self.engine_row = Adw.ComboRow()
        self.engine_row.set_title("Engine")
        self.engine_row.set_model(
            Gtk.StringList.new([catalog.ENGINES[k] for k in self._engine_keys])
        )
        if self._config.model.engine in self._engine_keys:
            self.engine_row.set_selected(self._engine_keys.index(self._config.model.engine))
        self.advanced_row.add_row(self.engine_row)

        self.device_row = Adw.ComboRow()
        self.device_row.set_title("Device")
        # Order must match model_picker_logic._DEVICE_OPTIONS = ("auto", "cpu", "cuda").
        # "auto" previously had no row option and was silently coerced to "cpu" on
        # Save (WHISP-42 audit MEDIUM #5) — it's now a first-class, round-trippable
        # choice.
        self.device_row.set_model(Gtk.StringList.new(["Auto", "CPU", "CUDA (GPU)"]))
        self.device_row.set_selected(device_row_index(self._config.model.device))
        self.advanced_row.add_row(self.device_row)

        self.compute_type_row = Adw.ComboRow()
        self.compute_type_row.set_title("Compute type")
        compute_types = ["int8", "float16", "float32"]  # mirrors config.validate()'s allowlist
        self.compute_type_row.set_model(Gtk.StringList.new(compute_types))
        self.compute_type_row.set_selected(
            compute_types.index(self._config.model.compute_type)
            if self._config.model.compute_type in compute_types
            else 0
        )
        self.advanced_row.add_row(self.compute_type_row)

        self._model_list_box.append(self.advanced_row)

        self.engine_row.connect("notify::selected", self._on_engine_row_changed)
        # Set initial compute-type sensitivity for whatever engine is configured, without
        # triggering a spurious model-row repopulation (the row is already correctly
        # populated from _refresh_model_row() above).
        self._suppress_model_sync = True
        try:
            self._on_engine_row_changed(self.engine_row, None)
        finally:
            self._suppress_model_sync = False

        # "View full catalog…" — reachable even when the language/device filter above
        # hides a model the user still wants to pick manually (WHISP-42 §1.6).
        catalog_row = Adw.ActionRow()
        catalog_row.set_title("View full catalog…")
        catalog_row.set_activatable(True)
        catalog_row.connect("activated", self._on_view_catalog_clicked)
        self._model_list_box.append(catalog_row)

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

        # Terminal paste mode
        self.terminal_paste_switch = Gtk.Switch()
        self.terminal_paste_switch.set_active(
            self._config.clipboard.paste_shortcut == "ctrl+shift+v"
        )

        terminal_paste_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        terminal_paste_label = Gtk.Label(label="Paste into terminal (Ctrl+Shift+V):")
        terminal_paste_label.set_halign(Gtk.Align.START)
        terminal_paste_label.set_hexpand(True)
        terminal_paste_label.set_tooltip_text(
            "Use Ctrl+Shift+V instead of Ctrl+V — required for GNOME Terminal and other terminal emulators"
        )

        terminal_paste_box.append(terminal_paste_label)
        terminal_paste_box.append(self.terminal_paste_switch)
        page.append(terminal_paste_box)

        # Paste to captured window toggle
        captured_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        captured_label = Gtk.Label(label="Paste to captured window")
        captured_label.set_hexpand(True)
        captured_label.set_halign(Gtk.Align.START)
        captured_label.set_tooltip_text(
            "Paste goes to the window active when you stopped recording, "
            "even if you switch windows during transcription."
        )
        self._paste_captured_switch = Gtk.Switch()
        self._paste_captured_switch.set_active(self._config.clipboard.paste_to_captured_window)
        captured_row.append(captured_label)
        captured_row.append(self._paste_captured_switch)
        page.append(captured_row)

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

        # --- Global Shortcut Section ---
        hotkey_section = Gtk.Label(label="Global Shortcut")
        hotkey_section.set_halign(Gtk.Align.START)
        hotkey_section.add_css_class("heading")
        hotkey_section.add_css_class("wa-section-title")
        hotkey_section.set_margin_top(12)
        page.append(hotkey_section)

        # Status row: configured accel + backend hint
        configured_accel = self._config.hotkey.toggle_recording or ""
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        status_label = Gtk.Label(label="Global shortcut:")
        status_label.set_halign(Gtk.Align.START)
        status_label.set_hexpand(True)
        status_value_label = Gtk.Label(label=configured_accel)
        status_value_label.set_halign(Gtk.Align.END)
        status_box.append(status_label)
        status_box.append(status_value_label)
        page.append(status_box)

        # Accel entry row
        accel_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        accel_label = Gtk.Label(label="Shortcut key:")
        accel_label.set_halign(Gtk.Align.START)
        accel_label.set_hexpand(True)
        self.hotkey_accel_entry = Gtk.Entry()
        self.hotkey_accel_entry.set_text(configured_accel)
        self.hotkey_accel_entry.set_placeholder_text("<Super><Alt>r")
        accel_box.append(accel_label)
        accel_box.append(self.hotkey_accel_entry)
        page.append(accel_box)

        # Hotkey status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        backend_label = Gtk.Label(label="Backend status:")
        backend_label.set_halign(Gtk.Align.START)
        backend_label.set_hexpand(True)
        self._hotkey_status_label = Gtk.Label(label="")
        self._hotkey_status_label.set_halign(Gtk.Align.END)
        self._register_hotkey_btn = Gtk.Button(label="Check Status")
        self._register_hotkey_btn.connect("clicked", self._on_register_hotkey_clicked)
        status_row.append(backend_label)
        status_row.append(self._hotkey_status_label)
        status_row.append(self._register_hotkey_btn)
        page.append(status_row)

        # Help box (fallback for when portal is not available)
        hotkey_help_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hotkey_help_box.add_css_class("wa-help")

        hotkey_help_label = Gtk.Label()
        hotkey_help_label.set_markup(
            "If registration fails, create a system keyboard shortcut\n"
            "pointing to the command:  <tt>whisper-aloud toggle</tt>"
        )
        hotkey_help_label.set_halign(Gtk.Align.START)
        hotkey_help_label.set_wrap(True)
        hotkey_help_label.set_xalign(0.0)

        copy_cmd_button = Gtk.Button(label="Copy command")
        copy_cmd_button.set_halign(Gtk.Align.START)

        def _on_copy_command_clicked(_btn: Gtk.Button) -> None:
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set("whisper-aloud toggle")

        copy_cmd_button.connect("clicked", _on_copy_command_clicked)

        hotkey_help_box.append(hotkey_help_label)
        hotkey_help_box.append(copy_cmd_button)
        page.append(hotkey_help_box)

        self._style_setting_rows_in_page(page)

        self.stack.add_titled(page, "general", "General")

    def _refresh_model_row(
        self,
        preferred_engine: Optional[str] = None,
        preferred_name: Optional[str] = None,
    ) -> None:
        """Repopulate the Model row's list from the catalog, filtered by the current
        language/device, and (re)select an entry.

        `preferred_engine` narrows the list to one engine's entries (used when the
        Advanced→Engine row changes); `preferred_name` additionally pins the exact
        entry to select (used when a catalog-view row is explicitly chosen), bypassing
        the default_index() recommendation fallback since the user already made an
        explicit choice.

        When both `preferred_engine` and `preferred_name` are given (an explicit
        catalog-view pick), the language/device filter is bypassed entirely for that
        engine's entries — the whole point of the full-catalog view (spec §1.6) is to
        let the user cross a language filter that would otherwise hide the entry they
        just chose (e.g. picking Parakeet from the catalog while language="ja").

        When only `preferred_engine` is given (an Advanced->Engine switch), uses
        model_picker_logic.entries_for_engine so a switch to an engine with zero
        entries for the configured language falls back to that engine's full list
        instead of silently keeping the *previous* engine's entries on screen (and on
        Save) — WHISP-42 audit HIGH #2. The language-fallback notice is shown/hidden
        accordingly.
        """
        used_language_fallback = False
        if preferred_engine is not None and preferred_name is not None:
            entries = catalog.models_for(preferred_engine)
        elif preferred_engine is not None:
            entries, used_language_fallback = entries_for_engine(
                self._config.transcription.language, self._config.model.device, preferred_engine
            )
        else:
            entries = selectable_entries(
                self._config.transcription.language, self._config.model.device
            )

        self._model_entries = entries
        if used_language_fallback:
            self._language_fallback_notice.set_text(
                f"No {self._config.transcription.language} models for this engine; showing all."
            )
        self._language_fallback_notice.set_visible(used_language_fallback)

        # TODO(WHISP-42-followup): per-row badge pills via Gtk.SignalListItemFactory,
        # per spec §1.2 — badges are plain text appended to the label for now
        # (model_picker_logic.row_label), not separate pill widgets.
        labels = [row_label(e, download_state_for(e).downloaded) for e in entries]
        self.model_row.set_model(Gtk.StringList.new(labels))

        if not entries:
            self.model_row.set_selected(Gtk.INVALID_LIST_POSITION)
            self._update_notices()
            return

        if preferred_name is not None:
            index = next((i for i, e in enumerate(entries) if e.name == preferred_name), 0)
        else:
            index = default_index(
                entries,
                self._config.model.engine,
                self._config.model.name,
                self._config.transcription.language,
            )
        self.model_row.set_selected(index)
        self._update_notices()

    def _update_notices(self) -> None:
        """Refresh the not-downloaded and license notices for the currently selected
        Model row entry."""
        index = self.model_row.get_selected()
        if not self._model_entries or index == Gtk.INVALID_LIST_POSITION or index < 0:
            self._download_notice.set_visible(False)
            self._license_notice.set_visible(False)
            self._update_download_row()
            return

        entry = self._model_entries[index]
        state = download_state_for(entry)
        if needs_download_note(state.downloaded):
            self._download_notice.set_text(
                f"{entry.label} ({_fmt_size(entry.size_mb)}) downloads the first time you use it."
            )
            self._download_notice.set_visible(True)
        else:
            self._download_notice.set_visible(False)

        badges = badges_for(entry)
        if entry.license in badges:
            self._license_notice.set_text(
                f"This model's license ({entry.license}) differs from this app's MIT license."
            )
            self._license_notice.set_visible(True)
        else:
            self._license_notice.set_visible(False)

        self._update_download_row()

    # ─── WHISP-68: in-app model download ───────────────────────────────────

    def _selected_entry(self) -> Optional["catalog.ModelEntry"]:
        index = self.model_row.get_selected()
        if not self._model_entries or index == Gtk.INVALID_LIST_POSITION or index < 0:
            return None
        return self._model_entries[index]

    def _get_download_client(self):
        """Lazily create (and keep) one WhisperAloudClient for the download
        signals, subscribing to DownloadProgress/DownloadFinished exactly
        once -- mirrors _on_register_hotkey_clicked's lazy-client pattern."""
        if self._download_client is None:
            from whisper_aloud.service.client import WhisperAloudClient

            client = WhisperAloudClient()
            client.on_download_progress(self._on_download_progress_signal)
            client.on_download_finished(self._on_download_finished_signal)
            self._download_client = client
        return self._download_client

    def _update_download_row(self) -> None:
        """Render the download row for the currently selected Model-row entry,
        entirely from download_logic.compute_ui_state() -- no visibility/label
        decision is made here directly."""
        entry = self._selected_entry()
        if entry is None:
            self._download_row_box.set_visible(False)
            self._download_error_label.set_visible(False)
            return

        is_current = self._download_active_entry == (entry.engine, entry.name)
        state = compute_ui_state(
            downloaded=download_state_for(entry).downloaded,
            is_downloading=is_current and self._downloading,
            size_mb=entry.size_mb,
            progress=self._download_progress_state if is_current else None,
            error=self._download_error if is_current else None,
        )

        self._download_button.set_visible(state.button_visible)
        self._download_button.set_label(state.button_label)
        self._download_button.set_sensitive(state.button_sensitive)
        self._download_progress.set_visible(state.progress_visible)
        if state.progress_visible:
            if state.progress_fraction is None:
                self._download_progress.pulse()
            else:
                self._download_progress.set_fraction(state.progress_fraction)
        self._download_cancel_button.set_visible(state.cancel_visible)
        self._download_row_box.set_visible(state.button_visible or state.progress_visible)
        self._download_error_label.set_text(state.error_text)
        self._download_error_label.set_visible(state.error_visible)

    def _on_download_clicked(self, _button: Gtk.Button) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if requires_license_consent(entry):
            # WHISP-68 audit BLOCKER #1: a needs_review or non-standard-license
            # entry must never start downloading from a plain click -- show an
            # explicit consent dialog first. Only its "Accept and download"
            # response calls _start_download(..., accept_license=True); every
            # other outcome (Cancel, Esc, closing the dialog) sends nothing at
            # all, not even accept_license=False.
            self._show_license_consent_dialog(entry)
            return
        self._start_download(entry, accept_license=False)

    # Findings doc referenced by the license-consent dialog for the one
    # catalog entry that needs it today (nemotron-streaming's OpenMDW-1.1
    # pending-review license). Relative to the repo root (settings_dialog.py
    # lives at src/whisper_aloud/ui/, three parents up).
    _LICENSE_FINDINGS_DOC = "docs/superpowers/specs/2026-10-01-whisp55-nemotron-sherpa-findings.md"

    def _license_findings_doc_markup(self) -> str:
        """A clickable `file://` link to the findings doc when it can be
        resolved on disk (a source checkout), else just its repo-relative
        path as plain text (e.g. an installed package without docs/) --
        never a broken link."""
        repo_root = Path(__file__).resolve().parents[3]
        doc_path = repo_root / self._LICENSE_FINDINGS_DOC
        escaped_rel = GLib.markup_escape_text(self._LICENSE_FINDINGS_DOC)
        if doc_path.is_file():
            return f'<a href="file://{doc_path}">{escaped_rel}</a>'
        return escaped_rel

    def _show_license_consent_dialog(self, entry: "catalog.ModelEntry") -> None:
        """Explicit license-consent dialog for a needs_review/non-standard-
        license entry (WHISP-68 audit BLOCKER #1). Shows the model label, the
        license id, the pending-review status, and a link to the findings doc
        (or the license itself) -- never started implicitly from a notice
        merely being visible."""
        status = (
            "This model's license is pending human legal review."
            if entry.needs_review
            else "This model's license is non-standard for this app (MIT)."
        )
        label = GLib.markup_escape_text(entry.label)
        license_id = GLib.markup_escape_text(entry.license)
        body = (
            f"<b>{label}</b>\n"
            f"License: {license_id}\n"
            f"{status}\n\n"
            f"See {self._license_findings_doc_markup()} for details."
        )
        dialogs.confirm(
            parent=self,
            heading=entry.label,
            body=body,
            body_use_markup=True,
            accept_label="Accept and download",
            on_accept=lambda: self._on_license_consent_response(None, "accept", entry),
        )

    def _on_license_consent_response(
        self, _dialog: object, response: str, entry: "catalog.ModelEntry"
    ) -> None:
        if response != "accept":
            return  # Cancel/Esc/close: nothing is sent to the daemon at all.
        self._start_download(entry, accept_license=True)

    def _start_download(self, entry: "catalog.ModelEntry", accept_license: bool) -> None:
        client = self._get_download_client()
        self._download_error = None
        started = client.download_model(entry.engine, entry.name, accept_license)
        if not started:
            self._download_error = (
                "Could not start download (another download may already be running)."
            )
            self._update_download_row()
            return
        self._downloading = True
        self._download_active_entry = (entry.engine, entry.name)
        self._download_progress_state = (0, 0)
        self._update_download_row()

    def _on_download_cancel_clicked(self, _button: Gtk.Button) -> None:
        # Cancel is only ever visible/clickable while a download this dialog
        # itself started is in flight (see compute_ui_state's cancel_visible),
        # so a client always already exists by the time this can fire --
        # still routed through _get_download_client() (not self._download_client
        # directly) so it's consistent with _on_download_clicked and trivially
        # fakeable in tests.
        self._get_download_client().cancel_download()

    def _on_download_progress_signal(
        self, engine: str, name: str, bytes_done: int, bytes_total: int
    ) -> None:
        if self._download_active_entry != (engine, name):
            return
        self._download_progress_state = (bytes_done, bytes_total)
        self._update_download_row()

    def _on_download_finished_signal(
        self, engine: str, name: str, ok: bool, error: str
    ) -> None:
        if self._download_active_entry != (engine, name):
            return
        self._downloading = False
        self._download_progress_state = None
        self._download_error = None if ok else (error or "Download failed")
        # Prefer refreshing the download state after DownloadFinished over
        # tracking "downloaded" locally -- re-derives it from disk via
        # model_picker_logic.download_state_for (the single source of
        # truth), which also keeps the row's badges/notices consistent.
        self._refresh_model_row()

    def _on_model_row_changed(self, row: "Adw.ComboRow", _pspec: object) -> None:
        """Handle a (user- or programmatically-driven) Model row selection change."""
        self._update_notices()
        if self._suppress_model_sync:
            return
        index = row.get_selected()
        if not self._model_entries or index == Gtk.INVALID_LIST_POSITION or index < 0:
            return
        entry = self._model_entries[index]
        # Guard like _on_catalog_entry_chosen does: entry.engine may not be in
        # self._engine_keys for a catalog entry whose engine isn't registered in
        # catalog.ENGINES yet (e.g. WHISP-56's nemotron-streaming) — WHISP-42 audit
        # MEDIUM #3. Such an entry is reachable via the full-catalog view even though
        # it's never in the Advanced->Engine dropdown.
        if hasattr(self, "engine_row") and entry.engine in self._engine_keys:
            self._suppress_model_sync = True
            try:
                self.engine_row.set_selected(self._engine_keys.index(entry.engine))
            finally:
                self._suppress_model_sync = False

    def _on_engine_row_changed(self, row: "Adw.ComboRow", _pspec: object) -> None:
        """Handle an Advanced→Engine row change: repopulate/re-sync the Model row and
        update the Compute type row's sensitivity (sherpa-onnx/Parakeet ignores it)."""
        engine = self._engine_keys[row.get_selected()]
        self.compute_type_row.set_sensitive(engine != "parakeet")
        self.compute_type_row.set_tooltip_text(
            "Not used by this engine" if engine == "parakeet" else None
        )
        if self._suppress_model_sync:
            return
        self._suppress_model_sync = True
        try:
            self._refresh_model_row(preferred_engine=engine)
        finally:
            self._suppress_model_sync = False

    def _on_view_catalog_clicked(self, _row: "Adw.ActionRow") -> None:
        """Open a read-only window listing every catalog.entries() row, unfiltered by
        language/device — the only way to cross the Model row's language filter."""
        window = Gtk.Window(transient_for=self, modal=True)
        window.set_title("Model Catalog")
        window.set_default_size(560, 480)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        for entry in catalog.entries():
            row = Adw.ActionRow()
            row.set_title(entry.label)
            langs = (
                "multilingual" if entry.languages is None else f"{len(entry.languages)} languages"
            )
            size = _fmt_size(entry.size_mb)
            state = download_state_for(entry)
            downloaded = "downloaded" if state.downloaded else "not downloaded"
            row.set_subtitle(f"{entry.engine} · {size} · {langs} · {entry.license} · {downloaded}")
            row.set_activatable(True)
            row.connect("activated", self._on_catalog_entry_chosen, entry, window)
            list_box.append(row)
        scrolled.set_child(list_box)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.HeaderBar())
        box.append(scrolled)
        window.set_child(box)
        window.present()

    def _on_catalog_entry_chosen(
        self, _row: "Adw.ActionRow", entry: "catalog.ModelEntry", window: Gtk.Window
    ) -> None:
        """Honor an explicit catalog-view pick: select that exact entry (bypassing the
        default_index() recommendation fallback) and sync Advanced→Engine to match."""
        window.close()
        self._suppress_model_sync = True
        try:
            self._refresh_model_row(preferred_engine=entry.engine, preferred_name=entry.name)
            if entry.engine in self._engine_keys:
                self.engine_row.set_selected(self._engine_keys.index(entry.engine))
        finally:
            self._suppress_model_sync = False
        self._update_notices()
        self._mark_dirty()

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
        engine_idx = self.engine_row.get_selected()
        return {
            "model_engine": (
                self._engine_keys[engine_idx]
                if 0 <= engine_idx < len(self._engine_keys)
                else ""
            ),
            "model_name": (
                self._model_entries[self.model_row.get_selected()].name
                if self._model_entries
                and self.model_row.get_selected() != Gtk.INVALID_LIST_POSITION
                else ""
            ),
            "model_device": self.device_row.get_selected(),
            "model_compute_type": self.compute_type_row.get_selected(),
            "language": self.language_entry.get_text(),
            "audio_device": self.audio_device_dropdown.get_selected(),
            "auto_copy": self.auto_copy_switch.get_active(),
            "auto_paste": self.auto_paste_switch.get_active(),
            "terminal_paste": self.terminal_paste_switch.get_active(),
            "paste_captured": self._paste_captured_switch.get_active(),
            "notifications_enabled": self.notifications_enabled_switch.get_active(),
            "notif_start": self.notification_recording_started_switch.get_active(),
            "notif_stop": self.notification_recording_stopped_switch.get_active(),
            "notif_done": self.notification_transcription_completed_switch.get_active(),
            "notif_error": self.notification_error_switch.get_active(),
            "sample_rate": self.sample_rate_entry.get_text(),
            "vad_enabled": self.vad_switch.get_active(),
            "vad_threshold": self.vad_threshold_entry.get_text(),
            "normalize": self.normalize_switch.get_active(),
            "paste_delay": self.paste_delay_entry.get_text(),
            "save_audio": self.save_audio_switch.get_active(),
            "dedupe_audio": self.deduplicate_audio_switch.get_active(),
            "auto_cleanup": self.auto_cleanup_switch.get_active(),
            "edit_history": self.edit_history_switch.get_active(),
            "cleanup_days": self.cleanup_days_entry.get_text(),
            "max_entries": self.max_entries_entry.get_text(),
            "db_path": self.db_path_entry.get_text(),
            "audio_archive_path": self.audio_archive_entry.get_text(),
            "hotkey_accel": self.hotkey_accel_entry.get_text(),
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
            self.model_row,
            self.engine_row,
            self.device_row,
            self.compute_type_row,
            self.language_entry,
            self.audio_device_dropdown,
            self.auto_copy_switch,
            self.auto_paste_switch,
            self.terminal_paste_switch,
            self._paste_captured_switch,
            self.notifications_enabled_switch,
            self.notification_recording_started_switch,
            self.notification_recording_stopped_switch,
            self.notification_transcription_completed_switch,
            self.notification_error_switch,
            self.sample_rate_entry,
            self.vad_switch,
            self.vad_threshold_entry,
            self.normalize_switch,
            self.paste_delay_entry,
            self.save_audio_switch,
            self.deduplicate_audio_switch,
            self.auto_cleanup_switch,
            self.edit_history_switch,
            self.cleanup_days_entry,
            self.max_entries_entry,
            self.db_path_entry,
            self.audio_archive_entry,
            self.hotkey_accel_entry,
        ]

        for widget in tracked_widgets:
            if isinstance(widget, Gtk.Switch):
                widget.connect("notify::active", self._mark_dirty)
            elif isinstance(widget, (Gtk.DropDown, Adw.ComboRow)):
                widget.connect("notify::selected", self._mark_dirty)
            elif isinstance(widget, Gtk.Entry):
                widget.connect("changed", self._mark_dirty)

    def _add_history_page(self) -> None:
        """Add History & Storage settings page."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        scrolled.set_child(page)

        # --- History Behaviour ---
        behaviour_section = Gtk.Label(label="History Behaviour")
        behaviour_section.set_halign(Gtk.Align.START)
        behaviour_section.add_css_class("heading")
        behaviour_section.add_css_class("wa-section-title")
        page.append(behaviour_section)

        # Allow editing transcriptions
        self.edit_history_switch = Gtk.Switch()
        if self._config.persistence:
            self.edit_history_switch.set_active(self._config.persistence.edit_history_enabled)

        edit_history_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        edit_history_label = Gtk.Label(label="Allow editing transcriptions:")
        edit_history_label.set_halign(Gtk.Align.START)
        edit_history_label.set_hexpand(True)
        edit_history_box.append(edit_history_label)
        edit_history_box.append(self.edit_history_switch)
        page.append(edit_history_box)

        # Auto-cleanup toggle
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

        # Cleanup after N days
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

        # --- Audio Archive ---
        audio_section = Gtk.Label(label="Audio Archive")
        audio_section.set_halign(Gtk.Align.START)
        audio_section.add_css_class("heading")
        audio_section.add_css_class("wa-section-title")
        audio_section.set_margin_top(12)
        page.append(audio_section)

        # Save audio recordings
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

        # Deduplicate audio
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

        # --- Storage Paths ---
        paths_section = Gtk.Label(label="Storage Paths")
        paths_section.set_halign(Gtk.Align.START)
        paths_section.add_css_class("heading")
        paths_section.add_css_class("wa-section-title")
        paths_section.set_margin_top(12)
        page.append(paths_section)

        # Database path
        db_path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        db_path_label = Gtk.Label(label="Database path:")
        db_path_label.set_halign(Gtk.Align.START)
        db_path_label.set_hexpand(True)
        self.db_path_entry = Gtk.Entry()
        if self._config.persistence and self._config.persistence.db_path:
            self.db_path_entry.set_text(str(self._config.persistence.db_path))
        else:
            self.db_path_entry.set_text(
                str(Path.home() / ".local/share/whisper_aloud/history.db")
            )
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
            self.audio_archive_entry.set_text(
                str(Path.home() / ".local/share/whisper_aloud/audio")
            )
        self.audio_archive_entry.set_hexpand(True)
        audio_path_box.append(audio_path_label)
        audio_path_box.append(self.audio_archive_entry)
        page.append(audio_path_box)

        self._style_setting_rows_in_page(page)
        self.stack.add_titled(scrolled, "history", "History")

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

        # --- Audio Processing Section ---
        # (Compute device moved to Settings → General → Model → Advanced, WHISP-42 —
        # it's now grouped with Engine/Compute type as a single manual-override block.)
        audio_section = Gtk.Label(label="Audio Processing")
        audio_section.set_halign(Gtk.Align.START)
        audio_section.add_css_class("heading")
        audio_section.add_css_class("wa-section-title")
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

        # --- Audio Pipeline Section ---
        pipeline_section = Gtk.Label(label="Audio Pipeline")
        pipeline_section.set_halign(Gtk.Align.START)
        pipeline_section.add_css_class("heading")
        pipeline_section.add_css_class("wa-section-title")
        pipeline_section.set_margin_top(12)
        page.append(pipeline_section)

        # Noise gate
        self.noise_gate_switch = Gtk.Switch()
        self.noise_gate_switch.set_active(self._config.audio_processing.noise_gate_enabled)
        ng_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ng_label = Gtk.Label(label="Noise gate:")
        ng_label.set_halign(Gtk.Align.START)
        ng_label.set_hexpand(True)
        ng_box.append(ng_label)
        ng_box.append(self.noise_gate_switch)
        page.append(ng_box)

        # Noise gate threshold
        ng_thresh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ng_thresh_label = Gtk.Label(label="Noise gate threshold (dB):")
        ng_thresh_label.set_halign(Gtk.Align.START)
        ng_thresh_label.set_hexpand(True)
        self.noise_gate_threshold_entry = Gtk.Entry()
        self.noise_gate_threshold_entry.set_text(str(self._config.audio_processing.noise_gate_threshold_db))
        self.noise_gate_threshold_entry.set_width_chars(8)
        ng_thresh_box.append(ng_thresh_label)
        ng_thresh_box.append(self.noise_gate_threshold_entry)
        page.append(ng_thresh_box)

        # AGC
        self.agc_switch = Gtk.Switch()
        self.agc_switch.set_active(self._config.audio_processing.agc_enabled)
        agc_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        agc_label = Gtk.Label(label="Auto gain control (AGC):")
        agc_label.set_halign(Gtk.Align.START)
        agc_label.set_hexpand(True)
        agc_box.append(agc_label)
        agc_box.append(self.agc_switch)
        page.append(agc_box)

        # AGC target
        agc_target_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        agc_target_label = Gtk.Label(label="AGC target level (dBFS):")
        agc_target_label.set_halign(Gtk.Align.START)
        agc_target_label.set_hexpand(True)
        self.agc_target_entry = Gtk.Entry()
        self.agc_target_entry.set_text(str(self._config.audio_processing.agc_target_db))
        self.agc_target_entry.set_width_chars(8)
        agc_target_box.append(agc_target_label)
        agc_target_box.append(self.agc_target_entry)
        page.append(agc_target_box)

        # AGC max gain
        agc_max_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        agc_max_label = Gtk.Label(label="AGC max gain (dB):")
        agc_max_label.set_halign(Gtk.Align.START)
        agc_max_label.set_hexpand(True)
        self.agc_max_gain_entry = Gtk.Entry()
        self.agc_max_gain_entry.set_text(str(self._config.audio_processing.agc_max_gain_db))
        self.agc_max_gain_entry.set_width_chars(8)
        agc_max_box.append(agc_max_label)
        agc_max_box.append(self.agc_max_gain_entry)
        page.append(agc_max_box)

        # Denoiser
        self.denoising_switch = Gtk.Switch()
        self.denoising_switch.set_active(self._config.audio_processing.denoising_enabled)
        denoise_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        denoise_label = Gtk.Label(label="Noise reduction:")
        denoise_label.set_halign(Gtk.Align.START)
        denoise_label.set_hexpand(True)
        denoise_box.append(denoise_label)
        denoise_box.append(self.denoising_switch)
        page.append(denoise_box)

        # Denoiser strength
        denoise_str_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        denoise_str_label = Gtk.Label(label="Noise reduction strength (0–1):")
        denoise_str_label.set_halign(Gtk.Align.START)
        denoise_str_label.set_hexpand(True)
        self.denoising_strength_entry = Gtk.Entry()
        self.denoising_strength_entry.set_text(str(self._config.audio_processing.denoising_strength))
        self.denoising_strength_entry.set_width_chars(8)
        denoise_str_box.append(denoise_str_label)
        denoise_str_box.append(self.denoising_strength_entry)
        page.append(denoise_str_box)

        # Peak limiter
        self.limiter_switch = Gtk.Switch()
        self.limiter_switch.set_active(self._config.audio_processing.limiter_enabled)
        limiter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        limiter_label = Gtk.Label(label="Peak limiter:")
        limiter_label.set_halign(Gtk.Align.START)
        limiter_label.set_hexpand(True)
        limiter_box.append(limiter_label)
        limiter_box.append(self.limiter_switch)
        page.append(limiter_box)

        # --- Recording Section ---
        recording_section = Gtk.Label(label="Recording")
        recording_section.set_halign(Gtk.Align.START)
        recording_section.add_css_class("heading")
        recording_section.add_css_class("wa-section-title")
        recording_section.set_margin_top(12)
        page.append(recording_section)

        # Pause media
        self.pause_media_switch = Gtk.Switch()
        self.pause_media_switch.set_active(self._config.recording_flow.pause_media)
        pause_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        pause_label = Gtk.Label(label="Pause media on record:")
        pause_label.set_halign(Gtk.Align.START)
        pause_label.set_hexpand(True)
        pause_box.append(pause_label)
        pause_box.append(self.pause_media_switch)
        page.append(pause_box)

        # Raise mic gain
        self.raise_mic_gain_switch = Gtk.Switch()
        self.raise_mic_gain_switch.set_active(self._config.recording_flow.raise_mic_gain)
        gain_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        gain_label = Gtk.Label(label="Raise mic gain on record:")
        gain_label.set_halign(Gtk.Align.START)
        gain_label.set_hexpand(True)
        gain_box.append(gain_label)
        gain_box.append(self.raise_mic_gain_switch)
        page.append(gain_box)

        # Target gain
        target_gain_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        target_gain_label = Gtk.Label(label="Target mic gain (0.0–1.5):")
        target_gain_label.set_halign(Gtk.Align.START)
        target_gain_label.set_hexpand(True)
        self.target_gain_entry = Gtk.Entry()
        self.target_gain_entry.set_text(str(self._config.recording_flow.target_gain_linear))
        self.target_gain_entry.set_width_chars(8)
        target_gain_box.append(target_gain_label)
        target_gain_box.append(self.target_gain_entry)
        page.append(target_gain_box)

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
            # Update model config (catalog-driven — WHISP-42)
            model_idx = self.model_row.get_selected()
            if self._model_entries and model_idx != Gtk.INVALID_LIST_POSITION and model_idx >= 0:
                selected_entry = self._model_entries[model_idx]
                engine, name = save_selection(selected_entry.engine, selected_entry.name)
                self._config.model.engine = engine
                self._config.model.name = name

            self._config.model.device = device_for_index(self.device_row.get_selected())

            compute_types = ["int8", "float16", "float32"]
            selected_compute_type_idx = self.compute_type_row.get_selected()
            if selected_compute_type_idx != -1 and selected_compute_type_idx < len(compute_types):
                self._config.model.compute_type = compute_types[selected_compute_type_idx]

            # Validate language code
            lang = self.language_entry.get_text()
            try:
                self._config.transcription.language = normalize_language_input(lang)
            except ValueError as e:
                raise ValidationError(str(e)) from e

            logger.debug("Updating audio config")
            # Update audio config
            selected_device_idx = self.audio_device_dropdown.get_selected()
            # Check if a valid device is selected (index != -1)
            if selected_device_idx != -1 and selected_device_idx < len(self._devices):
                selected_device = self._devices[selected_device_idx]
                # Store None for the default device so the daemon always uses
                # the current system default (robust across hardware changes)
                if selected_device.is_default:
                    self._config.audio.device_id = None
                else:
                    self._config.audio.device_id = selected_device.id

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
            self._config.clipboard.paste_shortcut = (
                "ctrl+shift+v" if self.terminal_paste_switch.get_active() else "ctrl+v"
            )
            self._config.clipboard.paste_to_captured_window = self._paste_captured_switch.get_active()
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

            logger.debug("Updating hotkey config")
            self._config.hotkey.toggle_recording = self.hotkey_accel_entry.get_text().strip()

            logger.debug("Updating persistence config")
            # Update persistence config
            if not self._config.persistence:
                self._config.persistence = PersistenceConfig()

            self._config.persistence.save_audio = self.save_audio_switch.get_active()
            self._config.persistence.deduplicate_audio = self.deduplicate_audio_switch.get_active()
            self._config.persistence.auto_cleanup_enabled = self.auto_cleanup_switch.get_active()
            self._config.persistence.edit_history_enabled = self.edit_history_switch.get_active()
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

            logger.debug("Updating audio pipeline config")
            self._config.audio_processing.noise_gate_enabled = self.noise_gate_switch.get_active()
            self._config.audio_processing.noise_gate_threshold_db = InputValidator.validate_float(
                self.noise_gate_threshold_entry.get_text(),
                min_value=-80.0,
                max_value=0.0,
                field_name="Noise gate threshold"
            )
            self._config.audio_processing.agc_enabled = self.agc_switch.get_active()
            self._config.audio_processing.agc_target_db = InputValidator.validate_float(
                self.agc_target_entry.get_text(),
                min_value=-60.0,
                max_value=0.0,
                field_name="AGC target level"
            )
            self._config.audio_processing.agc_max_gain_db = InputValidator.validate_float(
                self.agc_max_gain_entry.get_text(),
                min_value=0.0,
                max_value=40.0,
                field_name="AGC max gain"
            )
            self._config.audio_processing.denoising_enabled = self.denoising_switch.get_active()
            self._config.audio_processing.denoising_strength = InputValidator.validate_float(
                self.denoising_strength_entry.get_text(),
                min_value=0.0,
                max_value=1.0,
                field_name="Noise reduction strength"
            )
            self._config.audio_processing.limiter_enabled = self.limiter_switch.get_active()

            logger.debug("Updating recording flow config")
            self._config.recording_flow.pause_media = self.pause_media_switch.get_active()
            self._config.recording_flow.raise_mic_gain = self.raise_mic_gain_switch.get_active()
            self._config.recording_flow.target_gain_linear = InputValidator.validate_float(
                self.target_gain_entry.get_text(),
                min_value=0.0,
                max_value=1.5,
                field_name="Target mic gain"
            )

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
