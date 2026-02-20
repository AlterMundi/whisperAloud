"""Pure logic helpers for settings dialog behavior."""

from typing import Mapping

from ..utils.validation_helpers import sanitize_language_code


def has_unsaved_changes(
    initial_state: Mapping[str, str | bool],
    current_state: Mapping[str, str | bool],
) -> bool:
    """Return True when current form state differs from initial state."""
    return dict(initial_state) != dict(current_state)


def should_block_close(allow_close: bool, unsaved_changes: bool) -> bool:
    """Return True when dialog close should be intercepted."""
    return not allow_close and unsaved_changes


def should_auto_close_on_focus_loss(
    child_dialog_open: bool,
    is_active: bool,
    is_visible: bool,
) -> bool:
    """Return True when settings should close after focus is lost."""
    if child_dialog_open:
        return False
    return (not is_active) and is_visible


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
