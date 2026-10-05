"""Pure logic helpers for audio level meter behavior."""


def clamp_normalized_level(value: float) -> float:
    """Clamp a level value to [0.0, 1.0]."""
    return max(0.0, min(1.0, float(value)))


def normalize_meter_levels(rms: float, peak: float) -> tuple[float, float]:
    """Normalize and clamp RMS/peak levels."""
    return clamp_normalized_level(rms), clamp_normalized_level(peak)


def format_db_label(db: float) -> str:
    """Format decibel text for the panel label."""
    if db <= -60:
        return "-∞ dB"
    return f"{db:.1f} dB"


def level_color_zone(rms: float) -> str:
    """Return visual zone for a normalized RMS value."""
    value = clamp_normalized_level(rms)
    if value < 0.5:
        return "green"
    if value < 0.8:
        return "yellow"
    return "red"


RGBA = tuple[float, float, float, float]

# Alpha applied to the widget's foreground colour for each neutral part of the
# meter (HIG-01 / audit F-3): the meter follows light and dark style instead of
# drawing a hard-coded dark bar.
_NEUTRAL_ALPHAS = {
    "track": 0.10,
    "border": 0.25,
    "tick": 0.35,
    "peak": 0.90,
}


def meter_neutral_colors(foreground: RGBA) -> dict[str, RGBA]:
    """Return track/border/tick/peak colours derived from the foreground RGBA."""
    r, g, b, a = foreground
    return {name: (r, g, b, a * alpha) for name, alpha in _NEUTRAL_ALPHAS.items()}
