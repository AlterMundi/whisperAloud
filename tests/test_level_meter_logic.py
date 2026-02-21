"""Logic tests for level meter behavior without GTK runtime."""

from whisper_aloud.ui.level_meter_logic import (
    clamp_normalized_level,
    format_db_label,
    level_color_zone,
    normalize_meter_levels,
)


def test_clamp_normalized_level_bounds():
    assert clamp_normalized_level(-0.5) == 0.0
    assert clamp_normalized_level(0.25) == 0.25
    assert clamp_normalized_level(1.5) == 1.0


def test_normalize_meter_levels():
    assert normalize_meter_levels(0.4, 0.8) == (0.4, 0.8)
    assert normalize_meter_levels(-1.0, 2.0) == (0.0, 1.0)


def test_format_db_label():
    assert format_db_label(-80.0) == "-∞ dB"
    assert format_db_label(-60.0) == "-∞ dB"
    assert format_db_label(-12.34) == "-12.3 dB"


def test_level_color_zone_thresholds():
    assert level_color_zone(0.1) == "green"
    assert level_color_zone(0.5) == "yellow"
    assert level_color_zone(0.79) == "yellow"
    assert level_color_zone(0.8) == "red"
