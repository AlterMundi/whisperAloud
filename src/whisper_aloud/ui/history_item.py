"""History item widget for the history panel."""

import logging

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from ..persistence.models import HistoryEntry
from .history_logic import (
    format_transcription_preview,
    should_emit_favorite_toggle,
)

logger = logging.getLogger(__name__)


class HistoryItem(Gtk.ListBoxRow):
    """Individual history entry widget."""

    _PREVIEW_DELAY_MS = 180

    __gsignals__ = {
        'favorite-toggled': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'delete-requested': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, entry: HistoryEntry):
        """
        Initialize history item.

        Args:
            entry: HistoryEntry to display
        """
        super().__init__()
        self.entry = entry
        self.add_css_class("wa-history-item")

        # Build layout
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Time label
        time_str = entry.timestamp.strftime("%H:%M") if entry.timestamp else "--:--"
        time_label = Gtk.Label(label=time_str)
        time_label.set_width_chars(5)
        time_label.add_css_class("dim-label")
        time_label.add_css_class("wa-history-time")
        box.append(time_label)

        # Content box (Text + Metadata)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content_box.set_hexpand(True)

        # Text preview (first 100 chars)
        text_preview = entry.text[:100] + ("..." if len(entry.text) > 100 else "")
        text_label = Gtk.Label(label=text_preview)
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_label.set_xalign(0)
        text_label.add_css_class("wa-history-title")
        content_box.append(text_label)

        # Metadata: language • confidence% • duration
        confidence_pct = int(entry.confidence * 100)
        meta_text = f"{entry.language} • {confidence_pct}% • {entry.duration:.1f}s"
        meta_label = Gtk.Label(label=meta_text)
        meta_label.add_css_class("dim-label")
        meta_label.add_css_class("caption")
        meta_label.add_css_class("wa-history-meta")
        meta_label.set_xalign(0)
        content_box.append(meta_label)

        box.append(content_box)

        # Favorite button
        fav_button = Gtk.ToggleButton()
        fav_button.set_icon_name("starred-symbolic" if entry.favorite else "non-starred-symbolic")
        fav_button.set_active(entry.favorite)
        fav_button.set_valign(Gtk.Align.CENTER)
        fav_button.add_css_class("flat")
        fav_button.add_css_class("wa-ghost")
        fav_button.set_tooltip_text("Toggle favorite")
        fav_button.connect("toggled", self._on_favorite_toggled)
        box.append(fav_button)

        self.set_child(box)
        self._preview_text = format_transcription_preview(entry.text)
        self._preview_popover: Gtk.Popover | None = None
        self._preview_show_timeout_id: int | None = None
        self._context_popover: Gtk.Popover | None = None
        self._setup_hover_preview()

        # Context menu
        self._setup_context_menu()

    def _setup_hover_preview(self) -> None:
        """Set up an upwards popover preview for transcription text."""
        if not self._preview_text:
            return

        self._preview_popover = Gtk.Popover()
        self._preview_popover.set_has_arrow(True)
        self._preview_popover.set_autohide(True)
        self._preview_popover.set_parent(self)
        self._preview_popover.set_position(Gtk.PositionType.TOP)
        self._preview_popover.set_size_request(360, -1)
        self._preview_popover.add_css_class("wa-preview-popover")

        preview_label = Gtk.Label(label=self._preview_text)
        preview_label.set_xalign(0.0)
        preview_label.set_yalign(0.0)
        preview_label.set_wrap(True)
        preview_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        preview_label.set_max_width_chars(65)
        preview_label.add_css_class("monospace")
        preview_label.add_css_class("wa-preview-label")
        self._preview_popover.set_child(preview_label)

        hover_controller = Gtk.EventControllerMotion()
        hover_controller.connect("enter", self._on_hover_enter)
        hover_controller.connect("leave", self._on_hover_leave)
        self.add_controller(hover_controller)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("leave", self._on_focus_leave)
        self.add_controller(focus_controller)

    def _on_hover_enter(self, _controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
        """Schedule preview popover for delayed show."""
        if not self._preview_popover:
            return
        self._cancel_preview_timer()
        self._preview_show_timeout_id = GLib.timeout_add(
            self._PREVIEW_DELAY_MS,
            self._show_preview_popover,
        )

    def _on_hover_leave(self, _controller: Gtk.EventControllerMotion) -> None:
        """Hide preview popover when pointer leaves the row."""
        self._cancel_preview_timer()
        if self._preview_popover:
            self._preview_popover.popdown()

    def _on_focus_leave(self, _controller: Gtk.EventControllerFocus) -> None:
        """Hide preview popover when row focus is lost."""
        self._cancel_preview_timer()
        if self._preview_popover:
            self._preview_popover.popdown()

    def _cancel_preview_timer(self) -> None:
        """Cancel pending preview timer if one is active."""
        if self._preview_show_timeout_id is not None:
            GLib.source_remove(self._preview_show_timeout_id)
            self._preview_show_timeout_id = None

    def _show_preview_popover(self) -> bool:
        """Show preview popover anchored to the row top center."""
        self._preview_show_timeout_id = None
        if not self._preview_popover:
            return GLib.SOURCE_REMOVE
        width = max(1, self.get_allocated_width())
        self._preview_popover.set_pointing_to(Gdk.Rectangle(x=width // 2, y=0, width=1, height=1))
        self._preview_popover.popup()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _format_transcription_tooltip(text: str) -> str:
        """Backwards-compatible wrapper for preview formatting."""
        return format_transcription_preview(text)

    def _on_favorite_toggled(self, button: Gtk.ToggleButton) -> None:
        """
        Handle favorite toggle.

        Args:
            button: The toggle button
        """
        is_active = button.get_active()
        button.set_icon_name("starred-symbolic" if is_active else "non-starred-symbolic")

        # Only emit if state actually changed (avoid loops if updated externally)
        if should_emit_favorite_toggle(self.entry.favorite, is_active):
            self.entry.favorite = is_active
            self.emit("favorite-toggled", self.entry.id)

    def _setup_context_menu(self) -> None:
        """Set up right-click context menu."""
        # Create popover menu
        Gtk.PopoverMenu()

        # Create menu model
        # Note: In GTK4, we typically use Gio.Menu, but for simplicity in this custom widget
        # we might use a Gtk.Popover with buttons if Gio.Menu is too complex to set up here.
        # However, Gtk.ListBoxRow doesn't easily support right-click without an EventController.

        controller = Gtk.GestureClick()
        controller.set_button(0)  # Listen to all buttons
        controller.connect("pressed", self._on_mouse_pressed)
        self.add_controller(controller)

    def _on_mouse_pressed(self, gesture, n_press, x, y):
        """Handle mouse press for context menu."""
        from gi.repository import Gdk

        # Check for right click (button 3)
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_SECONDARY:
            self._show_context_menu(x, y)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _show_context_menu(self, x, y):
        """Show context menu popover."""
        if self._preview_popover:
            self._preview_popover.popdown()

        if self._context_popover:
            self._context_popover.popdown()

        popover = Gtk.Popover()
        popover.set_parent(self)
        popover.set_autohide(True)
        popover.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Delete button
        delete_btn = Gtk.Button(label="Delete")
        delete_btn.add_css_class("flat")
        delete_btn.add_css_class("wa-ghost")
        delete_btn.set_tooltip_text("Delete this history entry")
        delete_btn.connect("clicked", lambda b: self._on_delete_clicked(popover))
        box.append(delete_btn)

        popover.set_child(box)
        self._context_popover = popover
        popover.popup()

    def _on_delete_clicked(self, popover):
        """Handle delete action."""
        popover.popdown()
        self._context_popover = None
        self.emit("delete-requested", self.entry.id)
