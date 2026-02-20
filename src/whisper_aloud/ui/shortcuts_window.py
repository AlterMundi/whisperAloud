"""Keyboard shortcuts help window for WhisperAloud."""

import logging
from typing import List, Tuple

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

logger = logging.getLogger(__name__)


class ShortcutsWindow(Gtk.Window):
    """Window displaying all keyboard shortcuts."""

    # List of (group_name, [(shortcut, description), ...])
    SHORTCUTS: List[Tuple[str, List[Tuple[str, str]]]] = [
        ("Recording", [
            ("Space", "Start/Stop recording"),
            ("Ctrl+X", "Cancel transcription"),
        ]),
        ("Text", [
            ("Ctrl+C", "Copy transcription to clipboard"),
            ("Escape", "Clear transcription text"),
        ]),
        ("Application", [
            ("Ctrl+Q", "Quit application"),
            ("F1", "Show keyboard shortcuts"),
            ("Ctrl+,", "Open settings"),
        ]),
    ]

    def __init__(self, parent: Gtk.Window) -> None:
        """
        Initialize shortcuts window.

        Args:
            parent: Parent window
        """
        super().__init__()

        self.set_title("Keyboard Shortcuts")
        self.set_transient_for(parent)
        self.set_modal(False)
        self.set_default_size(420, 580)
        self.set_resizable(False)
        self.add_css_class("wa-dialog-window")

        self._build_ui()
        self.connect("notify::is-active", self._on_window_active_changed)
        logger.debug("Shortcuts window initialized")

    def _on_window_active_changed(self, window: Gtk.Window, _param: object) -> None:
        """Auto-close shortcuts window when it loses focus."""
        if not window.get_property("is-active") and window.is_visible():
            GLib.idle_add(window.close)

    def _build_ui(self) -> None:
        """Build the shortcuts window UI."""
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # Header bar
        header = Gtk.HeaderBar()
        header.add_css_class("wa-headerbar")
        header.set_title_widget(Gtk.Label(label="Keyboard Shortcuts"))
        self.set_titlebar(header)

        # Scrolled window for content
        scrolled = Gtk.ScrolledWindow()
        scrolled.add_css_class("wa-output-wrap")
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        main_box.append(scrolled)

        # Content box
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        scrolled.set_child(content)

        # Add shortcut groups
        for group_name, shortcuts in self.SHORTCUTS:
            group_box = self._create_shortcut_group(group_name, shortcuts)
            content.append(group_box)

    def _create_shortcut_group(
        self,
        name: str,
        shortcuts: List[Tuple[str, str]]
    ) -> Gtk.Box:
        """
        Create a shortcut group widget.

        Args:
            name: Group name
            shortcuts: List of (key, description) tuples

        Returns:
            Box containing the group
        """
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Group header
        header = Gtk.Label(label=name)
        header.set_halign(Gtk.Align.START)
        header.add_css_class("heading")
        group.append(header)

        # Shortcuts list
        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        list_box.add_css_class("boxed-list")
        list_box.add_css_class("wa-shortcuts-list")
        group.append(list_box)

        for key, description in shortcuts:
            row = self._create_shortcut_row(key, description)
            list_box.append(row)

        return group

    def _create_shortcut_row(self, key: str, description: str) -> Gtk.ListBoxRow:
        """
        Create a single shortcut row.

        Args:
            key: Keyboard shortcut string
            description: Description of what the shortcut does

        Returns:
            ListBoxRow widget
        """
        row = Gtk.ListBoxRow()
        row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Description label
        desc_label = Gtk.Label(label=description)
        desc_label.set_halign(Gtk.Align.START)
        desc_label.set_hexpand(True)
        box.append(desc_label)

        # Shortcut label (styled like a key cap)
        key_label = Gtk.Label(label=key)
        key_label.add_css_class("dim-label")
        key_label.add_css_class("monospace")
        key_label.add_css_class("wa-keycap")
        box.append(key_label)

        row.set_child(box)
        return row
