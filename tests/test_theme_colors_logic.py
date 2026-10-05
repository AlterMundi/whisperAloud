"""HIG-01 (audit finding F-3): drawn colours follow the theme foreground.

The level meter and the tentative part of a live partial transcript used literal
colours (a dark meter bar even in light style; a fixed grey for tentative text).
Under Adw.StyleManager they must be derived from the widget's own foreground so
light and dark style both work. Pure helpers, no GTK needed.
"""

from whisper_aloud.ui.level_meter_logic import meter_neutral_colors
from whisper_aloud.ui.partial_view_logic import TENTATIVE_ALPHA, tentative_rgba


def test_meter_neutral_colors_derive_from_foreground():
    light_fg = (0.0, 0.0, 0.0, 0.8)
    dark_fg = (1.0, 1.0, 1.0, 1.0)
    for fg in (light_fg, dark_fg):
        colors = meter_neutral_colors(fg)
        assert set(colors) == {"track", "border", "tick", "peak"}
        for r, g, b, a in colors.values():
            assert (r, g, b) == fg[:3]
            assert 0.0 < a <= 1.0
        # The track is the faintest element, the peak marker the strongest.
        assert colors["track"][3] < colors["border"][3] < colors["peak"][3]


def test_meter_neutral_colors_scale_with_foreground_alpha():
    opaque = meter_neutral_colors((0.2, 0.2, 0.2, 1.0))
    half = meter_neutral_colors((0.2, 0.2, 0.2, 0.5))
    for key in opaque:
        assert half[key][3] == opaque[key][3] * 0.5


def test_tentative_rgba_keeps_foreground_hue_and_reduces_alpha():
    assert 0.0 < TENTATIVE_ALPHA < 1.0
    assert tentative_rgba((1.0, 1.0, 1.0, 1.0)) == (1.0, 1.0, 1.0, TENTATIVE_ALPHA)
    r, g, b, a = tentative_rgba((0.0, 0.0, 0.0, 0.8))
    assert (r, g, b) == (0.0, 0.0, 0.0)
    assert a == 0.8 * TENTATIVE_ALPHA
