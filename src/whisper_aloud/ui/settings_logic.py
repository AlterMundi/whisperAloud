"""Pure view-model helpers for the Preferences dialog (no GTK).

The instant-apply mechanics (row <-> config key <-> SetConfig) live in
prefs_binding.py; this module maps config values to row options and back.
"""

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from ..config import PASTE_BACKENDS
from ..utils.validation_helpers import sanitize_language_code

Option = Tuple[Any, str]

# Shared with the main window's language selector (same codes, same labels).
LANGUAGE_OPTIONS: Tuple[Option, ...] = (
    ("auto", "Auto"),
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("ja", "日本語"),
    ("zh", "中文"),
    ("ko", "한국어"),
    ("ru", "Русский"),
    ("ar", "العربية"),
    ("hi", "हिन्दी"),
)

# clipboard.paste_backend (WHISP-119, ADR 0001). Order follows PASTE_BACKENDS.
_PASTE_BACKEND_LABELS = {
    "auto": "Automatic",
    "extension": "GNOME Shell Extension",
    "remote-desktop": "Remote Desktop Portal",
    "clipboard": "Clipboard Only",
}
PASTE_BACKEND_OPTIONS: Tuple[Option, ...] = tuple(
    (value, _PASTE_BACKEND_LABELS.get(value, value)) for value in PASTE_BACKENDS
)
PASTE_BACKEND_SUBTITLE = (
    "Automatic uses the GNOME Shell extension when available. "
    "Remote Desktop Portal shows GNOME's screen-sharing indicator."
)

TERMINAL_PASTE_SHORTCUT = "ctrl+shift+v"
DEFAULT_PASTE_SHORTCUT = "ctrl+v"

SYSTEM_DEFAULT_MICROPHONE = "System Default"

# Adw.PreferencesDialog was introduced in libadwaita 1.5.
PREFERENCES_DIALOG_MIN_ADW_MINOR = 5


def uses_preferences_dialog(adw_minor_version: int) -> bool:
    """Adw.PreferencesDialog on libadwaita >= 1.5, else Adw.PreferencesWindow."""
    return adw_minor_version >= PREFERENCES_DIALOG_MIN_ADW_MINOR


def option_index(options: Sequence[Option], value: Any, default: Optional[int] = 0):
    """Index of ``value`` in ``options`` (value, label) pairs, else ``default``."""
    for i, (option, _label) in enumerate(options):
        if option == value:
            return i
    return default


def option_value(options: Sequence[Option], index: int) -> Any:
    """Value at ``index``, or None for an out-of-range/invalid index."""
    if index is None or index < 0 or index >= len(options):
        return None
    return options[index][0]


def paste_shortcut_for(terminal: bool) -> str:
    return TERMINAL_PASTE_SHORTCUT if terminal else DEFAULT_PASTE_SHORTCUT


def uses_terminal_shortcut(paste_shortcut: str) -> bool:
    return (paste_shortcut or "").lower() == TERMINAL_PASTE_SHORTCUT


def language_options(current: Optional[str]) -> List[Option]:
    """The language list, plus the configured code if it is not listed."""
    options = list(LANGUAGE_OPTIONS)
    if current and option_index(options, current, default=None) is None:
        options.append((current, current))
    return options


def microphone_options(devices: Iterable[Any]) -> List[Option]:
    """"System Default" (stored as None) first, then every input device."""
    return [(None, SYSTEM_DEFAULT_MICROPHONE)] + [(d.id, d.name) for d in devices]


def microphone_changes(device_id: Optional[int], devices: Iterable[Any], current_channels: int):
    """Config changes for a microphone choice: never ask a device for more
    channels than it has (same rule the old Save path applied)."""
    changes = {"audio.device_id": device_id}
    if device_id is None:
        return changes
    for device in devices:
        if device.id == device_id and device.channels < current_channels:
            changes["audio.channels"] = device.channels
    return changes


def include_current_entry(entries: list, engine: str, name: str) -> list:
    """Model-row entries, with the configured model appended when the
    language/device filter hid it: the row must show what is configured, since
    every selection applies immediately."""
    from ..stt import catalog

    for entry in entries:
        if entry.engine == engine and (entry.name == name or name in entry.aliases):
            return entries
    current = catalog.get(engine, name)
    if current is None:
        return entries
    return list(entries) + [current]


def should_auto_close_on_focus_loss(
    child_dialog_open: bool,
    contains_focus: bool,
    is_visible: bool,
) -> bool:
    """Return True when settings should close after focus leaves the widget tree.

    Uses ``contains_focus`` (from EventControllerFocus) rather than the window's
    ``is-active`` property.  On Wayland, ``is-active`` becomes False when a
    child Gtk.DropDown opens its list popup (the popup takes compositor surface
    focus), which incorrectly triggers auto-close before the user can select an
    item.  ``contains_focus`` stays True as long as any descendant widget —
    including DropDown list popovers — holds focus, so it only fires when focus
    genuinely leaves the settings window hierarchy.
    """
    if child_dialog_open:
        return False
    return (not contains_focus) and is_visible


def normalize_language_input(raw_value: str) -> str:
    """Normalize settings language input to 'auto' or a validated language code."""
    value = (raw_value or "").strip()
    if not value:
        return "auto"

    normalized = sanitize_language_code(value)
    if normalized is None:
        raise ValueError(
            f"Invalid language code '{value}'. Use 'auto' or a 2-letter ISO code "
            "(e.g., 'en', 'es')."
        )
    return normalized
