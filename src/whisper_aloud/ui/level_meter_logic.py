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
