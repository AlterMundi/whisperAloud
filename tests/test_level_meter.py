"""Tests for level meter widget."""

import os

import pytest

# Note: GTK4 tests are tricky in headless environments
# These are basic structural tests that don't require display
RUN_GTK_WIDGET_TESTS = os.getenv("WHISPER_ALOUD_RUN_GTK_WIDGET_TESTS") == "1"

def test_level_meter_import():
    """Test that level meter modules can be imported."""
    from whisper_aloud.ui.level_meter import LevelMeterPanel, LevelMeterWidget

    assert LevelMeterWidget is not None
    assert LevelMeterPanel is not None


@pytest.mark.skipif(
    not RUN_GTK_WIDGET_TESTS,
    reason="GTK widget tests are opt-in (set WHISPER_ALOUD_RUN_GTK_WIDGET_TESTS=1)",
)
def test_level_meter_widget_creation():
    """Test creating a LevelMeterWidget (requires display)."""
    import gi

    gi.require_version('Gtk', '4.0')
    from whisper_aloud.ui.level_meter import LevelMeterWidget

    widget = LevelMeterWidget()

    # Test initial state
    assert widget._rms == 0.0
    assert widget._peak == 0.0
    assert widget._db == -60.0

    # Test update
    widget.update_level(0.5, 0.7, -12.0)
    assert widget._rms == 0.5
    assert widget._peak == 0.7
    assert widget._db == -12.0

    # Test clamping
    widget.update_level(1.5, 2.0, 10.0)
    assert widget._rms == 1.0  # Clamped
    assert widget._peak == 1.0  # Clamped

    # Test reset
    widget.reset()
    assert widget._rms == 0.0
    assert widget._peak == 0.0
    assert widget._db == -60.0


@pytest.mark.skipif(
    not RUN_GTK_WIDGET_TESTS,
    reason="GTK widget tests are opt-in (set WHISPER_ALOUD_RUN_GTK_WIDGET_TESTS=1)",
)
def test_level_meter_panel_creation():
    """Test creating a LevelMeterPanel (requires display)."""
    import gi

    gi.require_version('Gtk', '4.0')
    from whisper_aloud.ui.level_meter import LevelMeterPanel

    panel = LevelMeterPanel()

    # Test update
    panel.update_level(0.5, 0.7, -12.0)
    assert panel.db_label.get_text() == "-12.0 dB"

    # Test -infinity dB
    panel.update_level(0.0, 0.0, -70.0)
    assert panel.db_label.get_text() == "-∞ dB"

    # Test reset
    panel.reset()
    assert panel.db_label.get_text() == "-∞ dB"


def test_level_value_clamping():
    """Test that level values are properly clamped (headless-safe logic)."""
    from whisper_aloud.ui.level_meter_logic import normalize_meter_levels

    # Values should be clamped to 0.0-1.0 range
    test_cases = [
        # (input_rms, input_peak, expected_rms, expected_peak)
        (0.5, 0.7, 0.5, 0.7),       # Normal values
        (1.5, 1.2, 1.0, 1.0),       # Above max
        (-0.5, -0.2, 0.0, 0.0),     # Below min
        (0.0, 1.0, 0.0, 1.0),       # Boundary values
    ]

    for rms_in, peak_in, rms_out, peak_out in test_cases:
        rms, peak = normalize_meter_levels(rms_in, peak_in)
        assert rms == rms_out, f"RMS clamping failed for {rms_in}"
        assert peak == peak_out, f"Peak clamping failed for {peak_in}"
