"""Pure logic helpers for MainWindow behavior."""

from typing import Sequence


def is_daemon_interaction_ready(
    *,
    client_present: bool,
    client_connected: bool,
    daemon_available: bool,
) -> bool:
    """Return True when UI actions can safely call daemon methods."""
    return client_present and client_connected and daemon_available


def resolve_language_change(
    *,
    selected_idx: int,
    language_codes: Sequence[str],
    current_language: str | None,
) -> tuple[str, str] | None:
    """Return (new_lang, previous_lang) when a valid language change exists."""
    if selected_idx < 0 or selected_idx >= len(language_codes):
        return None

    previous_lang = current_language or "auto"
    new_lang = language_codes[selected_idx]
    if new_lang == previous_lang:
        return None
    return new_lang, previous_lang


def should_enter_transcribing(stop_result: str) -> bool:
    """Return True only when daemon acknowledged transcribing transition."""
    return stop_result == "transcribing"


def should_restore_transcribing_after_cancel(cancel_result: bool) -> bool:
    """Return True when cancel was rejected and UI should roll back to transcribing."""
    return not cancel_result
