"""Preferences dialog for WhisperAloud (HIG-07, WHISP-102).

An ``Adw.PreferencesDialog`` (libadwaita >= 1.5; ``Adw.PreferencesWindow`` on
older libadwaita) with General, Audio and History pages. Every row applies
immediately: there is no Save/Cancel. Each change goes to the daemon through
``SetConfig`` via ``prefs_binding.PrefsBinding``, which validates it first,
puts the row back and shows a toast when it is rejected, and updates rows when
the daemon reports ``ConfigChanged`` (the CLI or another window changed a
setting). Text rows apply on Enter / the apply button / focus leaving the row;
spin rows apply when their value is committed.

The Startup and Top Bar Indicator groups apply on their own (Background
portal / GNOME Shell), see startup_section.py and shell_extension_section.py.

The Audio and History pages and the model, shortcut and catalog rows are a
direct port of the previous Settings window; their redesign is HIG-08/09/10.
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..config import WhisperAloudConfig  # noqa: E402
from ..stt import catalog  # noqa: E402
from . import dialogs  # noqa: E402
from .download_logic import compute_ui_state, requires_license_consent  # noqa: E402
from .model_picker_logic import (  # noqa: E402
    badges_for,
    default_index,
    download_state_for,
    entries_for_engine,
    needs_download_note,
    row_label,
    save_selection,
    selectable_entries,
)
from .prefs_binding import PrefsBinding, initial_values  # noqa: E402
from .settings_logic import (  # noqa: E402
    PASTE_BACKEND_OPTIONS,
    PASTE_BACKEND_SUBTITLE,
    include_current_entry,
    language_options,
    microphone_changes,
    microphone_options,
    option_index,
    option_value,
    paste_shortcut_for,
    uses_preferences_dialog,
    uses_terminal_shortcut,
)
from .startup_section import StartupSection  # noqa: E402

logger = logging.getLogger(__name__)

# Adw.PreferencesDialog needs libadwaita 1.5 (same gate as ui/dialogs.py).
USE_PREFERENCES_DIALOG = uses_preferences_dialog(Adw.get_minor_version())
_PreferencesBase = Adw.PreferencesDialog if USE_PREFERENCES_DIALOG else Adw.PreferencesWindow

# Order must match model_picker_logic._DEVICE_OPTIONS = ("auto", "cpu", "cuda").
DEVICE_OPTIONS = (("auto", "Auto"), ("cpu", "CPU"), ("cuda", "CUDA (GPU)"))
# Mirrors config.validate()'s allowlist.
COMPUTE_TYPE_OPTIONS = (("int8", "int8"), ("float16", "float16"), ("float32", "float32"))

FALLBACK_COMMAND = "whisper-aloud toggle"


def _build_shell_extension_section():
    """The optional "Top Bar Indicator" group; never breaks the dialog."""
    try:
        from .shell_extension_section import ShellExtensionSection

        return ShellExtensionSection()
    except Exception as e:
        logger.warning(f"Top bar indicator section unavailable: {e}")
        return None


def _fmt_size(size_mb: Optional[int]) -> str:
    """Human-facing size string for a catalog entry, or "—" when unknown."""
    return f"{size_mb} MB" if size_mb is not None else "—"


def _int(value: float) -> int:
    return int(round(value))


def _decimal(digits: int) -> Callable[[float], float]:
    return lambda value: round(float(value), digits)


class SettingsDialog(_PreferencesBase):
    """The Preferences dialog. Instant apply; no Save/Cancel."""

    def __init__(
        self,
        parent: Gtk.Window,
        config: WhisperAloudConfig,
        client=None,
        autostart_controller=None,
    ) -> None:
        """
        Args:
            parent: Window the dialog is presented over.
            config: The GUI's config copy, used when the daemon's GetConfig is
                unavailable.
            client: The connected WhisperAloudClient (SetConfig/GetConfig).
                Without one every change is refused with a toast.
            autostart_controller: ui.startup_logic.AutostartController for the
                Startup group (WHISP-87); None shows it unavailable.
        """
        super().__init__()
        self.set_title("Preferences")
        self.set_search_enabled(True)
        if USE_PREFERENCES_DIALOG:
            self.set_content_width(640)
            self.set_content_height(720)
        else:
            self.set_default_size(640, 720)
            self.set_modal(True)
            self.set_transient_for(parent)

        self._parent = parent
        self._client = client
        self._autostart_controller = autostart_controller
        self._syncing = 0
        self._committing_key: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_toast = None

        self.binding = PrefsBinding(
            client, initial_values(config, client), on_error=self._show_error
        )

        self._add_general_page()
        self._add_audio_page()
        self._add_history_page()

        if USE_PREFERENCES_DIALOG:
            self.connect("closed", self._on_closed)
        else:
            self.connect("close-request", self._on_close_request)

        logger.info("Preferences dialog initialized")

    # ── presenting / closing ────────────────────────────────────────────────

    def present_for(self, parent: Gtk.Widget) -> None:
        """Show the dialog over ``parent`` (Adw.Dialog or window fallback)."""
        if USE_PREFERENCES_DIALOG:
            self.present(parent)
        else:
            self.set_transient_for(parent)
            self.present()

    def connect_closed(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` once the dialog has been closed."""
        if USE_PREFERENCES_DIALOG:
            self.connect("closed", lambda *_: callback())
        else:
            self.connect("close-request", lambda *_: callback() or False)

    def _on_closed(self, *_args) -> None:
        self._startup_section.detach()
        if self._shell_extension_section is not None:
            self._shell_extension_section.detach()

    def _on_close_request(self, *_args) -> bool:
        self._on_closed()
        return False

    def _transient_window(self) -> Optional[Gtk.Window]:
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else self._parent

    # ── instant apply plumbing ──────────────────────────────────────────────

    def config_changed(self, changes: dict) -> None:
        """Daemon ConfigChanged (forwarded by the main window): update rows."""
        self.binding.config_changed(changes)

    def _show_error(self, message: str) -> None:
        self.last_error = message
        logger.warning("Preference not applied: %s", message)
        toast = Adw.Toast.new(GLib.markup_escape_text(message))
        self.last_toast = toast
        self.add_toast(toast)

    @contextmanager
    def _quiet(self):
        """Programmatic row updates must not be sent back to the daemon."""
        self._syncing += 1
        try:
            yield
        finally:
            self._syncing -= 1

    def _apply(self, changes: dict) -> bool:
        if self._syncing:
            return True
        return self.binding.apply(changes).ok

    def _bind_bool(
        self,
        row: Gtk.Widget,
        key: str,
        prop: str = "active",
        to_config: Callable[[bool], Any] = bool,
        from_config: Callable[[Any], bool] = bool,
    ) -> None:
        """A boolean row property (SwitchRow.active, ExpanderRow
        .enable-expansion) bound to ``key``."""
        row.set_property(prop, from_config(self.binding.get(key)))

        def _on_notify(widget, _pspec):
            self._apply({key: to_config(widget.get_property(prop))})

        def _on_config(value):
            with self._quiet():
                row.set_property(prop, from_config(value))

        row.connect(f"notify::{prop}", _on_notify)
        self.binding.watch(key, _on_config)

    def _bind_spin(self, row: "Adw.SpinRow", key: str, cast: Callable[[float], Any]) -> None:
        """SpinRow bound to ``key``; the value is committed by the +/- buttons,
        Enter or focus leaving the row, never on every keystroke."""
        row.set_value(float(self.binding.get(key) or 0))

        def _on_notify(widget, _pspec):
            self._apply({key: cast(widget.get_value())})

        def _on_config(value):
            with self._quiet():
                row.set_value(float(value or 0))

        row.connect("notify::value", _on_notify)
        self.binding.watch(key, _on_config)

    def _bind_combo(
        self,
        row: "Adw.ComboRow",
        key: str,
        options: Sequence,
        changes_for: Optional[Callable[[Any], dict]] = None,
        after_apply: Optional[Callable[[], None]] = None,
    ) -> None:
        """ComboRow over (value, label) ``options`` bound to ``key``."""
        row.set_model(Gtk.StringList.new([label for _value, label in options]))
        row.set_selected(option_index(options, self.binding.get(key)))

        def _on_notify(widget, _pspec):
            if self._syncing:
                return
            value = option_value(options, widget.get_selected())
            if value is None and widget.get_selected() >= len(options):
                return
            changes = changes_for(value) if changes_for else {key: value}
            if self._apply(changes) and after_apply is not None:
                after_apply()

        def _on_config(value):
            with self._quiet():
                row.set_selected(option_index(options, value))

        row.connect("notify::selected", _on_notify)
        self.binding.watch(key, _on_config)

    def _bind_entry(
        self,
        row: "Adw.EntryRow",
        key: str,
        validate: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        """EntryRow bound to ``key``. Applied on Enter/the apply button or when
        focus leaves the row; an invalid value keeps the text, marks the row
        with the error style and shows a toast."""
        row.set_text(str(self.binding.get(key) or ""))
        row.set_show_apply_button(True)

        def _commit(*_args):
            text = row.get_text().strip()
            if text == str(self.binding.get(key) or ""):
                row.remove_css_class("error")
                return
            error = validate(text) if validate else None
            if error is not None:
                row.add_css_class("error")
                self._show_error(error)
                return
            self._committing_key = key
            try:
                ok = self._apply({key: text})
            finally:
                self._committing_key = None
            if ok:
                row.remove_css_class("error")
            else:
                row.add_css_class("error")

        def _on_config(value):
            if self._committing_key == key:
                return  # keep the user's text on a rejection; row shows the error
            with self._quiet():
                row.set_text(str(value or ""))
            row.remove_css_class("error")

        row.connect("apply", _commit)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", _commit)
        row.add_controller(focus)
        self.binding.watch(key, _on_config)

    @staticmethod
    def _spin_row(title, subtitle, lower, upper, step, digits=0) -> "Adw.SpinRow":
        row = Adw.SpinRow.new_with_range(lower, upper, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_digits(digits)
        return row

    @staticmethod
    def _switch_row(title, subtitle=None) -> "Adw.SwitchRow":
        row = Adw.SwitchRow()
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        return row

    # ── General page ────────────────────────────────────────────────────────

    def _add_general_page(self) -> None:
        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")
        self.general_page = page

        self._build_model_group(page)
        self._build_after_transcribing_group(page)
        self._build_notifications_group(page)
        self._build_shortcut_group(page)

        # Startup (WHISP-87): applies through the Background portal.
        self._startup_section = StartupSection(self._autostart_controller)
        page.add(self._startup_section.widget)

        # Top bar indicator (GNOME Shell extension): applies on its own.
        self._shell_extension_section = _build_shell_extension_section()
        if self._shell_extension_section is not None:
            page.add(self._shell_extension_section.widget)

        self.add(page)

    def _build_model_group(self, page: "Adw.PreferencesPage") -> None:
        group = Adw.PreferencesGroup()
        group.set_title("Speech Model")
        self.model_group = group

        # Model selector — catalog-driven (WHISP-42). The subpage replacing it
        # is HIG-08.
        self.model_row = Adw.ComboRow()
        self.model_row.set_title("Model")
        self._model_entries: list = []  # populated by _refresh_model_row()
        self._suppress_model_sync = False

        # Notice widgets must exist before the first _refresh_model_row() call:
        # it calls _update_notices(), which reads them (WHISP-42 audit BLOCKER #1).
        self._download_notice = Gtk.Label(wrap=True, xalign=0)
        self._download_notice.add_css_class("dim-label")
        self._download_notice.set_visible(False)

        self._license_notice = Gtk.Label(wrap=True, xalign=0)
        self._license_notice.add_css_class("dim-label")
        self._license_notice.set_visible(False)

        # Shown when an Advanced->Engine switch has no entries for the configured
        # language and _refresh_model_row() fell back to that engine's full list.
        self._language_fallback_notice = Gtk.Label(wrap=True, xalign=0)
        self._language_fallback_notice.add_css_class("dim-label")
        self._language_fallback_notice.set_visible(False)

        # In-app model download row (WHISP-68), driven entirely by
        # download_logic.compute_ui_state() -- see _update_download_row().
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
        self.advanced_row.add_row(self.engine_row)

        self.device_row = Adw.ComboRow()
        self.device_row.set_title("Device")
        self._bind_combo(self.device_row, "model.device", DEVICE_OPTIONS)
        self.advanced_row.add_row(self.device_row)

        self.compute_type_row = Adw.ComboRow()
        self.compute_type_row.set_title("Compute Type")
        self._bind_combo(self.compute_type_row, "model.compute_type", COMPUTE_TYPE_OPTIONS)
        self.advanced_row.add_row(self.compute_type_row)

        self._sync_model_rows()

        # "View Full Catalog…" — reachable even when the language/device filter
        # hides a model the user still wants to pick (WHISP-42 §1.6).
        catalog_row = Adw.ActionRow()
        catalog_row.set_title("View Full Catalog…")
        catalog_row.set_activatable(True)
        catalog_row.connect("activated", self._on_view_catalog_clicked)

        # Language: same key as the main window's selector, kept in sync
        # through ConfigChanged.
        self.language_row = Adw.ComboRow()
        self.language_row.set_title("Language")
        self._language_options = language_options(self.binding.get("transcription.language"))
        self._bind_combo(
            self.language_row,
            "transcription.language",
            self._language_options,
            after_apply=self._sync_model_rows,
        )

        group.add(self.model_row)
        group.add(self.language_row)
        group.add(self.advanced_row)
        group.add(catalog_row)
        for widget in (
            self._download_notice,
            self._download_row_box,
            self._download_error_label,
            self._license_notice,
            self._language_fallback_notice,
        ):
            widget.set_margin_top(6)
            group.add(widget)

        self.model_row.connect("notify::selected", self._on_model_row_changed)
        self.engine_row.connect("notify::selected", self._on_engine_row_changed)
        for key in ("model.engine", "model.name", "transcription.language"):
            self.binding.watch(key, lambda _value: self._sync_model_rows())

        page.add(group)

    def _build_after_transcribing_group(self, page: "Adw.PreferencesPage") -> None:
        group = Adw.PreferencesGroup()
        group.set_title("After Transcribing")

        self.auto_copy_row = self._switch_row("Copy to Clipboard")
        self._bind_bool(self.auto_copy_row, "clipboard.auto_copy")
        group.add(self.auto_copy_row)

        self.auto_paste_row = Adw.ExpanderRow()
        self.auto_paste_row.set_title("Paste Into Active Window")
        self.auto_paste_row.set_subtitle("Types the text where the cursor is")
        self.auto_paste_row.set_show_enable_switch(True)
        self._bind_bool(self.auto_paste_row, "clipboard.auto_paste", prop="enable-expansion")

        self.terminal_paste_row = self._switch_row(
            "Use Terminal Shortcut (Ctrl+Shift+V)",
            "Required by GNOME Terminal and other terminal emulators",
        )
        self._bind_bool(
            self.terminal_paste_row,
            "clipboard.paste_shortcut",
            to_config=paste_shortcut_for,
            from_config=uses_terminal_shortcut,
        )
        self.auto_paste_row.add_row(self.terminal_paste_row)

        self.paste_captured_row = self._switch_row(
            "Paste Into Window Where Recording Stopped",
            "Even if you switch windows while the text is transcribed",
        )
        self._bind_bool(self.paste_captured_row, "clipboard.paste_to_captured_window")
        self.auto_paste_row.add_row(self.paste_captured_row)

        self.paste_backend_row = Adw.ComboRow()
        self.paste_backend_row.set_title("Typing Method")
        self.paste_backend_row.set_subtitle(PASTE_BACKEND_SUBTITLE)
        self._bind_combo(self.paste_backend_row, "clipboard.paste_backend", PASTE_BACKEND_OPTIONS)
        self.auto_paste_row.add_row(self.paste_backend_row)

        self.paste_delay_row = self._spin_row("Paste Delay", "Milliseconds", 0, 5000, 10)
        self._bind_spin(self.paste_delay_row, "clipboard.paste_delay_ms", _int)
        self.auto_paste_row.add_row(self.paste_delay_row)

        group.add(self.auto_paste_row)
        page.add(group)

    def _build_notifications_group(self, page: "Adw.PreferencesPage") -> None:
        group = Adw.PreferencesGroup()
        group.set_title("Notifications")

        self.notifications_row = Adw.ExpanderRow()
        self.notifications_row.set_title("Desktop Notifications")
        self.notifications_row.set_subtitle("Choose which notifications are shown")
        self.notifications_row.set_show_enable_switch(True)
        self._bind_bool(self.notifications_row, "notifications.enabled", prop="enable-expansion")

        self.notification_rows = {}
        for key, title in (
            ("notifications.recording_started", "Recording Started"),
            ("notifications.recording_stopped", "Recording Stopped"),
            ("notifications.transcription_completed", "Transcription Ready"),
            ("notifications.error", "Errors"),
        ):
            row = self._switch_row(title)
            self._bind_bool(row, key)
            self.notifications_row.add_row(row)
            self.notification_rows[key] = row

        group.add(self.notifications_row)
        page.add(group)

    def _build_shortcut_group(self, page: "Adw.PreferencesPage") -> None:
        # A direct port of the previous section; the redesign is HIG-10.
        group = Adw.PreferencesGroup()
        group.set_title("Dictation Shortcut")

        self.hotkey_accel_row = Adw.EntryRow()
        self.hotkey_accel_row.set_title("Shortcut (e.g. &lt;Super&gt;&lt;Alt&gt;r)")
        self._bind_entry(self.hotkey_accel_row, "hotkey.toggle_recording", self._validate_accel)
        group.add(self.hotkey_accel_row)

        self._hotkey_status_label = Gtk.Label(label="")
        self._hotkey_status_label.add_css_class("dim-label")
        self._register_hotkey_btn = Gtk.Button(label="Check Status")
        self._register_hotkey_btn.set_valign(Gtk.Align.CENTER)
        self._register_hotkey_btn.connect("clicked", self._on_register_hotkey_clicked)
        status_row = Adw.ActionRow()
        status_row.set_title("Backend Status")
        status_row.add_suffix(self._hotkey_status_label)
        status_row.add_suffix(self._register_hotkey_btn)
        group.add(status_row)

        copy_button = Gtk.Button(label="Copy")
        copy_button.set_valign(Gtk.Align.CENTER)
        copy_button.connect("clicked", self._on_copy_command_clicked)
        command_row = Adw.ActionRow()
        command_row.set_title("Fallback Command")
        command_row.set_subtitle(
            "If registration fails, create a system keyboard shortcut that runs "
            f"“{FALLBACK_COMMAND}”"
        )
        command_row.add_suffix(copy_button)
        group.add(command_row)

        page.add(group)

    @staticmethod
    def _validate_accel(text: str) -> Optional[str]:
        if not text:
            return None
        parsed = Gtk.accelerator_parse(text)
        ok = parsed[0] if isinstance(parsed, tuple) and len(parsed) == 3 else bool(parsed)
        if not ok:
            return f"“{text}” is not a valid shortcut (example: <Super><Alt>r)"
        return None

    @staticmethod
    def _on_copy_command_clicked(_button: Gtk.Button) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(FALLBACK_COMMAND)

    def _on_register_hotkey_clicked(self, _btn: Gtk.Button) -> None:
        """Check hotkey status and give actionable feedback."""
        import os

        client = self._client
        try:
            if client is None:
                from whisper_aloud.service.client import WhisperAloudClient

                client = WhisperAloudClient()
            backend = client.get_status().get("hotkey_backend", "none")
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

    # ── Speech model rows (WHISP-42 / WHISP-68), instant apply ──────────────

    def _sync_model_rows(self) -> None:
        """Re-render Model and Engine rows from the stored config."""
        self._suppress_model_sync = True
        try:
            self._refresh_model_row()
            engine = self.binding.get("model.engine")
            if engine in self._engine_keys:
                with self._quiet():
                    self.engine_row.set_selected(self._engine_keys.index(engine))
            self._update_compute_type_sensitivity()
        finally:
            self._suppress_model_sync = False

    def _refresh_model_row(
        self,
        preferred_engine: Optional[str] = None,
        preferred_name: Optional[str] = None,
    ) -> None:
        """Repopulate the Model row's list from the catalog, filtered by the
        configured language/device, and (re)select an entry.

        `preferred_engine` narrows the list to one engine's entries (an
        Advanced→Engine change); with `preferred_name` too (an explicit
        catalog-view pick) the language/device filter is bypassed for that
        engine. Without either, the configured model is always listed (even
        when the filter would hide it), since every selection applies at once.
        """
        language = self.binding.get("transcription.language") or "auto"
        device = self.binding.get("model.device") or "auto"
        engine = self.binding.get("model.engine")
        name = self.binding.get("model.name")

        used_language_fallback = False
        if preferred_engine is not None and preferred_name is not None:
            entries = catalog.models_for(preferred_engine)
        elif preferred_engine is not None:
            entries, used_language_fallback = entries_for_engine(
                language, device, preferred_engine
            )
        else:
            entries = include_current_entry(selectable_entries(language, device), engine, name)

        self._model_entries = entries
        if used_language_fallback:
            self._language_fallback_notice.set_text(
                f"No {language} models for this engine; showing all."
            )
        self._language_fallback_notice.set_visible(used_language_fallback)

        labels = [row_label(e, download_state_for(e).downloaded) for e in entries]
        with self._quiet():
            self.model_row.set_model(Gtk.StringList.new(labels))

            if not entries:
                self.model_row.set_selected(Gtk.INVALID_LIST_POSITION)
                self._update_notices()
                return

            if preferred_name is not None:
                index = next((i for i, e in enumerate(entries) if e.name == preferred_name), 0)
            else:
                index = default_index(entries, engine, name, language)
            self.model_row.set_selected(index)
        self._update_notices()

    def _update_compute_type_sensitivity(self) -> None:
        index = self.engine_row.get_selected()
        engine = self._engine_keys[index] if 0 <= index < len(self._engine_keys) else None
        self.compute_type_row.set_sensitive(engine != "parakeet")
        self.compute_type_row.set_tooltip_text(
            "Not used by this engine" if engine == "parakeet" else None
        )

    def _apply_model_entry(self, entry: "catalog.ModelEntry") -> None:
        engine, name = save_selection(entry.engine, entry.name)
        if not self.binding.apply({"model.engine": engine, "model.name": name}).ok:
            self._sync_model_rows()

    def _on_model_row_changed(self, row: "Adw.ComboRow", _pspec: object) -> None:
        """A Model row selection change: notices, Engine sync, apply."""
        self._update_notices()
        if self._suppress_model_sync or self._syncing:
            return
        entry = self._selected_entry()
        if entry is None:
            return
        # entry.engine may be missing from the Engine row (e.g. a catalog entry
        # whose engine isn't in catalog.ENGINES) — WHISP-42 audit MEDIUM #3.
        if entry.engine in self._engine_keys:
            self._suppress_model_sync = True
            try:
                with self._quiet():
                    self.engine_row.set_selected(self._engine_keys.index(entry.engine))
                self._update_compute_type_sensitivity()
            finally:
                self._suppress_model_sync = False
        self._apply_model_entry(entry)

    def _on_engine_row_changed(self, row: "Adw.ComboRow", _pspec: object) -> None:
        """An Advanced→Engine change: list that engine's models and apply the
        preselected one (compute type is unused by sherpa-onnx/Parakeet)."""
        self._update_compute_type_sensitivity()
        if self._suppress_model_sync or self._syncing:
            return
        engine = self._engine_keys[row.get_selected()]
        self._suppress_model_sync = True
        try:
            self._refresh_model_row(preferred_engine=engine)
        finally:
            self._suppress_model_sync = False
        entry = self._selected_entry()
        if entry is not None:
            self._apply_model_entry(entry)

    def _update_notices(self) -> None:
        """Refresh the not-downloaded and license notices for the currently
        selected Model row entry."""
        entry = self._selected_entry()
        if entry is None:
            self._download_notice.set_visible(False)
            self._license_notice.set_visible(False)
            self._update_download_row()
            return

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

    def _selected_entry(self) -> Optional["catalog.ModelEntry"]:
        index = self.model_row.get_selected()
        if not self._model_entries or index == Gtk.INVALID_LIST_POSITION or index < 0:
            return None
        if index >= len(self._model_entries):
            return None
        return self._model_entries[index]

    def _get_download_client(self):
        """Lazily create (and keep) one WhisperAloudClient for the download
        signals, subscribing to DownloadProgress/DownloadFinished once."""
        if self._download_client is None:
            from whisper_aloud.service.client import WhisperAloudClient

            client = WhisperAloudClient()
            client.on_download_progress(self._on_download_progress_signal)
            client.on_download_finished(self._on_download_finished_signal)
            self._download_client = client
        return self._download_client

    def _update_download_row(self) -> None:
        """Render the download row for the selected Model-row entry, entirely
        from download_logic.compute_ui_state()."""
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
            # WHISP-68 audit BLOCKER #1: never download a needs_review or
            # non-standard-license entry from a plain click.
            self._show_license_consent_dialog(entry)
            return
        self._start_download(entry, accept_license=False)

    # Findings doc referenced by the license-consent dialog. Relative to the
    # repo root (this file lives at src/whisper_aloud/ui/, three parents up).
    _LICENSE_FINDINGS_DOC = "docs/superpowers/specs/2026-10-01-whisp55-nemotron-sherpa-findings.md"

    def _license_findings_doc_markup(self) -> str:
        """A clickable `file://` link to the findings doc in a source checkout,
        else its repo-relative path as plain text -- never a broken link."""
        repo_root = Path(__file__).resolve().parents[3]
        doc_path = repo_root / self._LICENSE_FINDINGS_DOC
        escaped_rel = GLib.markup_escape_text(self._LICENSE_FINDINGS_DOC)
        if doc_path.is_file():
            return f'<a href="file://{doc_path}">{escaped_rel}</a>'
        return escaped_rel

    def _show_license_consent_dialog(self, entry: "catalog.ModelEntry") -> None:
        """Explicit license-consent dialog (WHISP-68 audit BLOCKER #1)."""
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
        # Re-derive "downloaded" from disk (model_picker_logic.download_state_for).
        self._sync_model_rows()

    def _on_view_catalog_clicked(self, _row: "Adw.ActionRow") -> None:
        """A read-only window listing every catalog entry, unfiltered by
        language/device — the only way to cross the Model row's language
        filter. Replaced by a subpage in HIG-08."""
        window = Gtk.Window(transient_for=self._transient_window(), modal=True)
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
        """Honor an explicit catalog-view pick: show and apply that exact entry."""
        window.close()
        self._suppress_model_sync = True
        try:
            self._refresh_model_row(preferred_engine=entry.engine, preferred_name=entry.name)
            if entry.engine in self._engine_keys:
                with self._quiet():
                    self.engine_row.set_selected(self._engine_keys.index(entry.engine))
            self._update_compute_type_sensitivity()
        finally:
            self._suppress_model_sync = False
        self._update_notices()
        self._apply_model_entry(entry)

    # ── Audio page (direct port; redesign is HIG-09) ────────────────────────

    def _add_audio_page(self) -> None:
        page = Adw.PreferencesPage()
        page.set_title("Audio")
        page.set_icon_name("audio-input-microphone-symbolic")
        self.audio_page = page

        # Input
        group = Adw.PreferencesGroup()
        group.set_title("Input")

        try:
            from ..audio import DeviceManager

            self._devices = DeviceManager.list_input_devices()
        except Exception as e:
            logger.warning(f"Could not list input devices: {e}")
            self._devices = []
        self._microphone_options = microphone_options(self._devices)
        self.microphone_row = Adw.ComboRow()
        self.microphone_row.set_title("Microphone")
        self._bind_combo(
            self.microphone_row,
            "audio.device_id",
            self._microphone_options,
            changes_for=lambda device_id: microphone_changes(
                device_id, self._devices, int(self.binding.get("audio.channels") or 1)
            ),
        )
        group.add(self.microphone_row)

        self.pause_media_row = self._switch_row("Pause Media While Recording")
        self._bind_bool(self.pause_media_row, "recording_flow.pause_media")
        group.add(self.pause_media_row)

        self.raise_mic_gain_row = self._switch_row("Raise Microphone Volume While Recording")
        self._bind_bool(self.raise_mic_gain_row, "recording_flow.raise_mic_gain")
        group.add(self.raise_mic_gain_row)

        self.target_gain_row = self._spin_row(
            "Target Microphone Volume", "From 0.0 to 1.5", 0.0, 1.5, 0.05, digits=2
        )
        self._bind_spin(self.target_gain_row, "recording_flow.target_gain_linear", _decimal(2))
        group.add(self.target_gain_row)
        page.add(group)

        # Processing
        group = Adw.PreferencesGroup()
        group.set_title("Processing")

        self.sample_rate_row = self._spin_row("Sample Rate", "Hz", 8000, 48000, 1000)
        self._bind_spin(self.sample_rate_row, "audio.sample_rate", _int)
        group.add(self.sample_rate_row)

        self.vad_row = self._switch_row("Voice Activity Detection")
        self._bind_bool(self.vad_row, "audio.vad_enabled")
        group.add(self.vad_row)

        self.vad_threshold_row = self._spin_row(
            "Voice Detection Threshold", None, 0.01, 0.99, 0.01, digits=2
        )
        self._bind_spin(self.vad_threshold_row, "audio.vad_threshold", _decimal(2))
        group.add(self.vad_threshold_row)

        self.normalize_row = self._switch_row("Normalize Audio")
        self._bind_bool(self.normalize_row, "audio.normalize_audio")
        group.add(self.normalize_row)
        page.add(group)

        # Pipeline
        group = Adw.PreferencesGroup()
        group.set_title("Audio Pipeline")

        self.noise_gate_row = self._switch_row("Noise Gate")
        self._bind_bool(self.noise_gate_row, "audio_processing.noise_gate_enabled")
        group.add(self.noise_gate_row)

        self.noise_gate_threshold_row = self._spin_row(
            "Noise Gate Threshold", "dB", -80.0, 0.0, 1.0, digits=1
        )
        self._bind_spin(
            self.noise_gate_threshold_row, "audio_processing.noise_gate_threshold_db", _decimal(1)
        )
        group.add(self.noise_gate_threshold_row)

        self.agc_row = self._switch_row("Automatic Gain Control")
        self._bind_bool(self.agc_row, "audio_processing.agc_enabled")
        group.add(self.agc_row)

        self.agc_target_row = self._spin_row("Gain Target Level", "dBFS", -60.0, 0.0, 1.0, digits=1)
        self._bind_spin(self.agc_target_row, "audio_processing.agc_target_db", _decimal(1))
        group.add(self.agc_target_row)

        self.agc_max_gain_row = self._spin_row("Maximum Gain", "dB", 0.0, 40.0, 1.0, digits=1)
        self._bind_spin(self.agc_max_gain_row, "audio_processing.agc_max_gain_db", _decimal(1))
        group.add(self.agc_max_gain_row)

        self.denoising_row = self._switch_row("Noise Reduction")
        self._bind_bool(self.denoising_row, "audio_processing.denoising_enabled")
        group.add(self.denoising_row)

        self.denoising_strength_row = self._spin_row(
            "Noise Reduction Strength", "From 0 to 1", 0.0, 1.0, 0.05, digits=2
        )
        self._bind_spin(
            self.denoising_strength_row, "audio_processing.denoising_strength", _decimal(2)
        )
        group.add(self.denoising_strength_row)

        self.limiter_row = self._switch_row("Peak Limiter")
        self._bind_bool(self.limiter_row, "audio_processing.limiter_enabled")
        group.add(self.limiter_row)
        page.add(group)

        self.add(page)

    # ── History page (direct port; redesign is HIG-09) ──────────────────────

    def _add_history_page(self) -> None:
        page = Adw.PreferencesPage()
        page.set_title("History")
        page.set_icon_name("document-open-recent-symbolic")
        self.history_page = page

        group = Adw.PreferencesGroup()
        group.set_title("History")

        self.edit_history_row = self._switch_row("Allow Editing Transcriptions")
        self._bind_bool(self.edit_history_row, "persistence.edit_history_enabled")
        group.add(self.edit_history_row)

        self.auto_cleanup_row = self._switch_row("Delete Old Transcriptions")
        self._bind_bool(self.auto_cleanup_row, "persistence.auto_cleanup_enabled")
        group.add(self.auto_cleanup_row)

        self.cleanup_days_row = self._spin_row("Delete After", "Days", 1, 36500, 1)
        self._bind_spin(self.cleanup_days_row, "persistence.auto_cleanup_days", _int)
        group.add(self.cleanup_days_row)

        self.max_entries_row = self._spin_row("Keep at Most", "Transcriptions", 100, 1000000, 100)
        self._bind_spin(self.max_entries_row, "persistence.max_entries", _int)
        group.add(self.max_entries_row)
        page.add(group)

        group = Adw.PreferencesGroup()
        group.set_title("Audio Archive")

        self.save_audio_row = self._switch_row("Save Audio Recordings")
        self._bind_bool(self.save_audio_row, "persistence.save_audio")
        group.add(self.save_audio_row)

        self.deduplicate_audio_row = self._switch_row("Skip Duplicate Audio")
        self._bind_bool(self.deduplicate_audio_row, "persistence.deduplicate_audio")
        group.add(self.deduplicate_audio_row)
        page.add(group)

        group = Adw.PreferencesGroup()
        group.set_title("Storage Paths")

        self.db_path_row = Adw.EntryRow()
        self.db_path_row.set_title("Database")
        self._bind_entry(self.db_path_row, "persistence.db_path", self._validate_path)
        group.add(self.db_path_row)

        self.audio_archive_row = Adw.EntryRow()
        self.audio_archive_row.set_title("Audio Archive Folder")
        self._bind_entry(
            self.audio_archive_row, "persistence.audio_archive_path", self._validate_path
        )
        group.add(self.audio_archive_row)
        page.add(group)

        self.add(page)

    @staticmethod
    def _validate_path(text: str) -> Optional[str]:
        if not text:
            return "Enter a path"
        return None
