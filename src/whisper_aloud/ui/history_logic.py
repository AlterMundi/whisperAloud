"""Pure logic helpers for history UI behavior."""

import textwrap


def format_transcription_preview(
    text: str,
    max_lines: int = 5,
    line_width: int = 25,
) -> str:
    """Format transcription preview as constrained multi-line text."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""

    lines = textwrap.wrap(
        cleaned,
        width=line_width,
        break_long_words=True,
        replace_whitespace=True,
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if len(lines[-1]) >= 3:
            lines[-1] = lines[-1][:-3] + "..."
        else:
            lines[-1] = lines[-1] + "..."
    return "\n".join(lines)


def should_emit_favorite_toggle(previous_value: bool, new_value: bool) -> bool:
    """Return True only when favorite state actually changed."""
    return previous_value != new_value


def build_history_metadata(
    language: str | None,
    confidence: float | None,
    duration: float | None,
) -> str:
    """Format compact metadata line for a history entry."""
    lang_value = (language or "auto").strip() or "auto"
    confidence_value = max(0.0, min(1.0, float(confidence or 0.0)))
    duration_value = max(0.0, float(duration or 0.0))
    confidence_pct = int(confidence_value * 100)
    return f"{lang_value} • {confidence_pct}% • {duration_value:.1f}s"
