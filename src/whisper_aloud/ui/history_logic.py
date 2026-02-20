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
