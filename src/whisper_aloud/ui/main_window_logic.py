"""Pure logic helpers for MainWindow behavior."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Sequence

TRANSCRIBING_STATUS_TEXT = "Transcribing... (press Ctrl+X to cancel)"
_MAX_MODEL_ERROR_CHARS = 80


@dataclass(frozen=True)
class ModelStateView:
    """How the status bar presents the daemon's model readiness (API 1.6.0)."""

    text: str
    show_download: bool
    is_ready: bool


def _unpack(value: Any) -> Any:
    if hasattr(value, "unpack"):
        try:
            return value.unpack()
        except Exception:
            return value
    return value


def resolve_model_state(status_payload: Mapping[str, Any] | None) -> tuple[str, str]:
    """Return (model_state, model_error) from a GetStatus payload; ("", "")
    for an older daemon that does not report model readiness."""
    if not isinstance(status_payload, Mapping):
        return "", ""
    state = str(_unpack(status_payload.get("model_state", "")) or "")
    error = str(_unpack(status_payload.get("model_error", "")) or "")
    return state, error


def describe_model_state(state: str, detail: str = "") -> ModelStateView:
    """Status-bar text for a model state. The record button is never disabled
    by this: a batch transcription simply waits for the model, and the
    daemon reports an explicit Error if it cannot."""
    if state == "loading":
        return ModelStateView("Loading model…", False, False)
    if state == "ready":
        return ModelStateView("Model ready", False, True)
    if state == "missing":
        return ModelStateView("Model missing — Download", True, False)
    if state == "downloading":
        return ModelStateView("Downloading model…", False, False)
    if state == "error":
        reason = detail or "failed to load"
        if len(reason) > _MAX_MODEL_ERROR_CHARS:
            reason = reason[: _MAX_MODEL_ERROR_CHARS - 1] + "…"
        return ModelStateView(f"Model error: {reason}", False, False)
    if state == "unloaded":
        return ModelStateView("Model not loaded", False, False)
    return ModelStateView("", False, True)


def transcribing_status_text(model_state: str) -> str:
    """Transient status while transcribing; makes a wait for the model visible."""
    if model_state == "loading":
        return "Transcribing… waiting for the model to load (press Ctrl+X to cancel)"
    return TRANSCRIBING_STATUS_TEXT


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


def resolve_daemon_status_state(status_payload: Mapping[str, Any] | None) -> str:
    """Return normalized daemon state extracted from GetStatus payload."""
    if not isinstance(status_payload, Mapping):
        return "idle"

    raw_state = status_payload.get("state", "idle")
    if hasattr(raw_state, "unpack"):
        try:
            raw_state = raw_state.unpack()
        except Exception:
            pass

    state = str(raw_state or "").strip().lower()
    if state in {"idle", "recording", "transcribing"}:
        return state
    return "idle"
