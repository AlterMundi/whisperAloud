"""Audio level meter widget for real-time visualization."""

import logging
from typing import Any

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from .level_meter_logic import (
    format_db_label,
    level_color_zone,
    normalize_meter_levels,
)

logger = logging.getLogger(__name__)


class LevelMeterWidget(Gtk.DrawingArea):
    """Custom widget for visualizing audio levels in real-time."""

    def __init__(self) -> None:
        """Initialize the level meter widget."""
        super().__init__()

        # Audio level state
        self._rms: float = 0.0
        self._peak: float = 0.0
        self._db: float = -60.0

        # Visual parameters
        self._bar_height: int = 20
        self._padding: int = 4

        # Set size
        self.set_content_width(400)
        self.set_content_height(self._bar_height + 2 * self._padding)

        # Set draw function
        self.set_draw_func(self._draw)

        logger.debug("Level meter widget initialized")

    def update_level(self, rms: float, peak: float, db: float) -> None:
        """
        Update the audio level values and trigger redraw.

        Args:
            rms: RMS level (0.0 to 1.0)
            peak: Peak level (0.0 to 1.0)
            db: Decibel level (typically -60 to 0)
        """
        self._rms, self._peak = normalize_meter_levels(rms, peak)
        self._db = db

        # Trigger redraw
        self.queue_draw()

    def reset(self) -> None:
        """Reset the level meter to zero."""
        self._rms = 0.0
        self._peak = 0.0
        self._db = -60.0
        self.queue_draw()

    def _draw(
        self,
        area: Gtk.DrawingArea,
        ctx: Any,
        width: int,
        height: int
    ) -> None:
        """
        Draw the level meter.

        Args:
            area: The drawing area
            ctx: Cairo context
            width: Widget width
            height: Widget height
        """
        # Calculate dimensions
        bar_width = width - 2 * self._padding
        bar_height = self._bar_height
        x = self._padding
        y = self._padding

        # Draw background
        ctx.set_source_rgb(0.2, 0.2, 0.2)
        ctx.rectangle(x, y, bar_width, bar_height)
        ctx.fill()

        # Draw RMS level bar
        rms_width = bar_width * self._rms

        # Color based on level (green -> yellow -> red)
        zone = level_color_zone(self._rms)
        if zone == "green":
            # Green zone
            ctx.set_source_rgb(0.3, 0.8, 0.3)
        elif zone == "yellow":
            # Yellow zone
            ctx.set_source_rgb(0.9, 0.9, 0.2)
        else:
            # Red zone (clipping warning)
            ctx.set_source_rgb(0.9, 0.2, 0.2)

        ctx.rectangle(x, y, rms_width, bar_height)
        ctx.fill()

        # Draw peak indicator (thin vertical line)
        if self._peak > 0.01:
            peak_x = x + bar_width * self._peak
            ctx.set_source_rgb(1.0, 1.0, 1.0)
            ctx.set_line_width(2.0)
            ctx.move_to(peak_x, y)
            ctx.line_to(peak_x, y + bar_height)
            ctx.stroke()

        # Draw dB markers
        self._draw_db_markers(ctx, x, y, bar_width, bar_height)

        # Draw border
        ctx.set_source_rgb(0.5, 0.5, 0.5)
        ctx.set_line_width(1.0)
        ctx.rectangle(x, y, bar_width, bar_height)
        ctx.stroke()

    def _draw_db_markers(
        self,
        ctx: Any,
        x: float,
        y: float,
        width: float,
        height: float
    ) -> None:
        """
        Draw decibel scale markers.

        Args:
            ctx: Cairo context
            x: Bar x position
            y: Bar y position
            width: Bar width
            height: Bar height
        """
        # dB markers: -60, -40, -20, -10, -6, -3, 0
        db_marks = [
            (-60, 0.0),
            (-40, 0.33),
            (-20, 0.67),
            (-10, 0.83),
            (-6, 0.90),
            (-3, 0.95),
            (0, 1.0),
        ]

        ctx.set_source_rgba(0.7, 0.7, 0.7, 0.5)
        ctx.set_line_width(1.0)

        for db, position in db_marks:
            mark_x = x + width * position

            # Draw tick mark
            ctx.move_to(mark_x, y)
            ctx.line_to(mark_x, y + 5)
            ctx.stroke()

            # Draw bottom tick
            ctx.move_to(mark_x, y + height - 5)
            ctx.line_to(mark_x, y + height)
            ctx.stroke()


class LevelMeterPanel(Gtk.Box):
    """Panel containing level meter with label."""

    def __init__(self) -> None:
        """Initialize the level meter panel."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("wa-level-panel")

        # Label
        label = Gtk.Label(label="Input Level")
        label.set_halign(Gtk.Align.START)
        label.add_css_class("caption")
        label.add_css_class("wa-level-caption")
        self.append(label)

        # Level meter widget
        self.meter = LevelMeterWidget()
        self.append(self.meter)

        # dB label
        self.db_label = Gtk.Label(label="-∞ dB")
        self.db_label.set_halign(Gtk.Align.END)
        self.db_label.add_css_class("caption")
        self.db_label.add_css_class("wa-level-caption")
        self.append(self.db_label)

        logger.debug("Level meter panel initialized")

    def update_level(self, rms: float, peak: float, db: float) -> None:
        """
        Update the level meter and dB label.

        Args:
            rms: RMS level (0.0 to 1.0)
            peak: Peak level (0.0 to 1.0)
            db: Decibel level
        """
        self.meter.update_level(rms, peak, db)

        # Update dB label
        self.db_label.set_text(format_db_label(db))

    def reset(self) -> None:
        """Reset the level meter."""
        self.meter.reset()
        self.db_label.set_text("-∞ dB")
