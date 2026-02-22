"""Sidebar panel for transcription history."""

import logging
import threading
from datetime import datetime
from typing import List, Protocol

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, GObject, Gtk

from ..persistence.models import HistoryEntry
from .history_item import HistoryItem
from .history_panel_logic import (
    filter_entries_by_query,
    group_entries_by_date,
    resolve_history_query_mode,
)

logger = logging.getLogger(__name__)


class HistoryBackend(Protocol):
    """Protocol consumed by HistoryPanel for data and exports."""

    def get_recent(self, limit: int | None = 50) -> List[HistoryEntry]:
        """Return recent entries."""

    def search(self, query: str, limit: int = 50) -> List[HistoryEntry]:
        """Search entries."""

    def get_favorites(self, limit: int = 50) -> List[HistoryEntry]:
        """Return favorite entries."""

    def toggle_favorite(self, entry_id: int) -> bool:
        """Toggle favorite flag."""

    def delete(self, entry_id: int) -> bool:
        """Delete one entry."""

    def export_json(self, entries: List[HistoryEntry], path) -> None:
        """Export JSON."""

    def export_markdown(self, entries: List[HistoryEntry], path) -> None:
        """Export Markdown."""

    def export_csv(self, entries: List[HistoryEntry], path) -> None:
        """Export CSV."""

    def export_text(self, entries: List[HistoryEntry], path) -> None:
        """Export plain text."""


class HistoryPanel(Gtk.Box):
    """Sidebar panel for transcription history."""

    __gsignals__ = {
        'entry-selected': (GObject.SignalFlags.RUN_FIRST, None, (object,)),  # Emits HistoryEntry
    }

    def __init__(self, history_manager: HistoryBackend):
        """
        Initialize history panel.

        Args:
            history_manager: History backend implementation
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.history_manager = history_manager

        # Search debouncing
        self._search_timeout_id = None
        self._search_debounce_ms = 500
        self._current_query = ""
        self._show_favorites_only = False

        # Multi-select delete mode
        self._selection_mode = False

        self._build_ui()

        # Load initial data
        self.refresh_recent()

    def _build_ui(self):
        """Build panel UI."""
        self.add_css_class("wa-history-panel")
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.add_css_class("wa-history-header")
        label = Gtk.Label(label="History")
        label.add_css_class("heading")
        label.add_css_class("wa-section-title")
        header_box.append(label)

        # Refresh button
        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh history")
        refresh_btn.add_css_class("flat")
        refresh_btn.add_css_class("wa-ghost")
        refresh_btn.connect("clicked", lambda b: self.refresh_recent())
        header_box.append(refresh_btn)

        # Delete selected button (hidden until selection mode)
        self._delete_selected_btn = Gtk.Button(label="Delete")
        self._delete_selected_btn.add_css_class("destructive-action")
        self._delete_selected_btn.set_tooltip_text("Delete selected entries")
        self._delete_selected_btn.set_visible(False)
        self._delete_selected_btn.connect("clicked", self._on_delete_selected_clicked)
        header_box.append(self._delete_selected_btn)

        self.append(header_box)

        # Search box
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_box.add_css_class("wa-history-search")

        self.search_entry = Gtk.SearchEntry()
        # Use props for GTK4 version compatibility
        self.search_entry.props.placeholder_text = "Search transcriptions..."
        self.search_entry.set_hexpand(True)
        self.search_entry.add_css_class("wa-search-entry")
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_box.append(self.search_entry)

        # Filter buttons
        self.favorites_button = Gtk.ToggleButton()
        self.favorites_button.set_icon_name("starred-symbolic")
        self.favorites_button.set_tooltip_text("Show favorites only")
        self.favorites_button.add_css_class("wa-ghost")
        self.favorites_button.connect("toggled", self._on_filter_toggled)
        search_box.append(self.favorites_button)

        self.append(search_box)

        # Scrolled list
        scrolled = Gtk.ScrolledWindow()
        scrolled.add_css_class("wa-history-list-wrap")
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.list_box.add_css_class("rich-list")
        self.list_box.add_css_class("wa-history-list")
        scrolled.set_child(self.list_box)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.list_box.add_controller(key_controller)

        self.append(scrolled)

        # Export button
        export_button = Gtk.Button(label="Export History...")
        export_button.add_css_class("wa-ghost")
        export_button.connect("clicked", self._on_export_clicked)
        self.append(export_button)

    def refresh_recent(self):
        """Reload recent entries."""
        self._perform_search(self._current_query)

    def _on_search_changed(self, entry):
        """
        Handle search query changes with debouncing.

        Waits 300ms after last keystroke before triggering search
        to avoid excessive database queries on the UI thread.
        """
        # Cancel previous timeout
        if self._search_timeout_id:
            GLib.source_remove(self._search_timeout_id)

        # Schedule new search
        query = entry.get_text().strip()
        self._current_query = query

        self._search_timeout_id = GLib.timeout_add(
            self._search_debounce_ms,
            self._trigger_search,
            query
        )

    def _trigger_search(self, query: str) -> bool:
        """Trigger the search (called by timeout)."""
        self._perform_search(query)
        self._search_timeout_id = None
        return False  # Remove timeout source

    def _on_filter_toggled(self, button):
        """Handle filter toggle."""
        self._show_favorites_only = button.get_active()
        self._perform_search(self._current_query)

    def _perform_search(self, query: str):
        """
        Perform search in background thread.
        """
        def search_thread():
            """Background search thread."""
            try:
                mode, normalized_query = resolve_history_query_mode(
                    query,
                    self._show_favorites_only,
                )
                if mode == "favorites":
                    results = self.history_manager.get_favorites(limit=50)
                    results = filter_entries_by_query(results, normalized_query)
                elif mode == "search":
                    results = self.history_manager.search(normalized_query, limit=50)
                else:
                    results = self.history_manager.get_recent(limit=50)

                # Update UI on main thread
                GLib.idle_add(self._populate_list, results)
            except Exception as e:
                logger.error(f"Search failed: {e}", exc_info=True)

        # Run search in background
        thread = threading.Thread(target=search_thread, daemon=True)
        thread.start()

    def _populate_list(self, entries: List[HistoryEntry]) -> bool:
        """
        Populate list with entries (main thread).

        Returns:
            False to remove idle callback
        """
        # Exit selection mode (items are being replaced)
        if self._selection_mode:
            self._selection_mode = False
            self._delete_selected_btn.set_visible(False)

        # Clear existing
        while (child := self.list_box.get_first_child()):
            self.list_box.remove(child)

        if not entries:
            label = Gtk.Label(label="No transcriptions found")
            label.add_css_class("dim-label")
            label.set_margin_top(20)
            self.list_box.append(label)
            return False

        # Group by date
        grouped = self._group_by_date(entries)

        for date_label, date_entries in grouped.items():
            # Date header
            header = Gtk.Label(label=date_label)
            header.add_css_class("heading")
            header.add_css_class("dim-label")
            header.add_css_class("wa-history-date")
            header.set_halign(Gtk.Align.START)
            header.set_margin_top(12)
            header.set_margin_bottom(4)
            header.set_margin_start(4)

            # Wrap header in a non-selectable row
            header_row = Gtk.ListBoxRow()
            header_row.set_selectable(False)
            header_row.set_activatable(False)
            header_row.set_child(header)
            self.list_box.append(header_row)

            # Entries for this date
            for entry in date_entries:
                item = HistoryItem(entry)
                item.connect("favorite-toggled", self._on_favorite_toggled_item)
                item.connect("delete-requested", self._on_delete_requested)
                item.connect("selection-mode-requested", self._on_selection_mode_requested)
                self.list_box.append(item)

        return False

    def _group_by_date(self, entries: List[HistoryEntry]) -> dict:
        """Group entries by date string."""
        return group_entries_by_date(entries)

    def _on_row_activated(self, list_box, row):
        """Handle row click."""
        if isinstance(row, HistoryItem):
            # Emit signal for main window to display
            self.emit("entry-selected", row.entry)

    def _on_favorite_toggled_item(self, item, entry_id):
        """Handle favorite toggle from item."""
        # Update in database
        self.history_manager.toggle_favorite(entry_id)

        # If we are filtering by favorites and this was unchecked, refresh list
        if self._show_favorites_only and not item.entry.favorite:
            self.refresh_recent()

    def _on_delete_requested(self, item, entry_id):
        """Handle delete request from item with confirmation dialog."""
        dialog = Gtk.AlertDialog()
        dialog.set_message("Delete Transcription?")
        dialog.set_detail(
            "This action cannot be undone. The transcription will be permanently removed."
        )
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)

        def on_choose(d, result):
            try:
                idx = d.choose_finish(result)
                if idx == 1:  # "Delete"
                    success = self.history_manager.delete(entry_id)
                    if success:
                        self.refresh_recent()
            except Exception:
                pass

        dialog.choose(self.get_root(), None, on_choose)

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        """Exit selection mode on Escape."""
        if self._selection_mode and keyval == Gdk.KEY_Escape:
            self._exit_selection_mode()
            return True
        return False

    def _on_selection_mode_requested(self, item, entry_id: int) -> None:
        """Enter selection mode (first right-click) or exit it (second right-click)."""
        if not self._selection_mode:
            self._enter_selection_mode(entry_id)
        else:
            self._exit_selection_mode()

    def _enter_selection_mode(self, initial_id: int) -> None:
        """Switch all items to selection mode and pre-select the triggering item."""
        self._selection_mode = True
        self._delete_selected_btn.set_visible(True)
        child = self.list_box.get_first_child()
        while child:
            if isinstance(child, HistoryItem):
                child.set_selection_mode(True)
                child.set_selected(child.entry.id == initial_id)
            child = child.get_next_sibling()

    def _exit_selection_mode(self) -> None:
        """Return all items to normal mode and hide the Delete button."""
        self._selection_mode = False
        self._delete_selected_btn.set_visible(False)
        child = self.list_box.get_first_child()
        while child:
            if isinstance(child, HistoryItem):
                child.set_selection_mode(False)
            child = child.get_next_sibling()

    def _on_delete_selected_clicked(self, button) -> None:
        """Confirm and delete all checked entries."""
        selected_ids = []
        child = self.list_box.get_first_child()
        while child:
            if isinstance(child, HistoryItem) and child.get_selected():
                selected_ids.append(child.entry.id)
            child = child.get_next_sibling()

        if not selected_ids:
            return

        count = len(selected_ids)
        dialog = Gtk.AlertDialog()
        dialog.set_message(
            f"Delete {count} Transcription{'s' if count > 1 else ''}?"
        )
        dialog.set_detail(
            "This action cannot be undone. The selected transcriptions will be permanently removed."
        )
        dialog.set_buttons(["Cancel", "Delete"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)

        def on_choose(d, result):
            try:
                idx = d.choose_finish(result)
                if idx == 1:
                    for entry_id in selected_ids:
                        self.history_manager.delete(entry_id)
                    self._exit_selection_mode()
                    self.refresh_recent()
            except Exception:
                pass

        dialog.choose(self.get_root(), None, on_choose)

    def _on_export_clicked(self, button):
        """Handle export button click."""
        # Create file chooser dialog
        dialog = Gtk.FileChooserNative.new(
            title="Export History",
            parent=self.get_root(),
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Export",
            cancel_label="Cancel"
        )

        dialog.set_modal(True)
        dialog.set_current_name(f"whisper_history_{datetime.now().strftime('%Y%m%d')}.json")

        # Add filters
        filter_json = Gtk.FileFilter()
        filter_json.set_name("JSON (*.json)")
        filter_json.add_pattern("*.json")
        dialog.add_filter(filter_json)

        filter_md = Gtk.FileFilter()
        filter_md.set_name("Markdown (*.md)")
        filter_md.add_pattern("*.md")
        dialog.add_filter(filter_md)

        filter_csv = Gtk.FileFilter()
        filter_csv.set_name("CSV (*.csv)")
        filter_csv.add_pattern("*.csv")
        dialog.add_filter(filter_csv)

        filter_txt = Gtk.FileFilter()
        filter_txt.set_name("Text (*.txt)")
        filter_txt.add_pattern("*.txt")
        dialog.add_filter(filter_txt)

        dialog.connect("response", self._on_export_response)
        dialog.show()

    def _on_export_response(self, dialog, response):
        """Handle export dialog response."""
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            path = file.get_path()

            # Determine format from extension
            if path.endswith(".md"):
                format_type = "markdown"
            elif path.endswith(".csv"):
                format_type = "csv"
            elif path.endswith(".txt"):
                format_type = "text"
            else:
                format_type = "json"

            self._perform_export(path, format_type)

        dialog.destroy()

    def _perform_export(self, path, format_type):
        """Perform export in background."""
        def export_thread():
            try:
                # Get all entries for export (or filtered ones)
                # For now, let's export everything if no filter, or filtered set
                if self._current_query or self._show_favorites_only:
                    # Re-run search to get all matching items (increase limit)
                    if self._show_favorites_only:
                        entries = self.history_manager.get_favorites(limit=1000)
                    else:
                        entries = self.history_manager.search(self._current_query, limit=1000)
                else:
                    entries = self.history_manager.get_recent(limit=None)

                from pathlib import Path
                file_path = Path(path)

                if format_type == "markdown":
                    self.history_manager.export_markdown(entries, file_path)
                elif format_type == "csv":
                    self.history_manager.export_csv(entries, file_path)
                elif format_type == "text":
                    self.history_manager.export_text(entries, file_path)
                else:
                    self.history_manager.export_json(entries, file_path)

                logger.info(f"Exported history to {path}")

            except Exception as e:
                logger.error(f"Export failed: {e}", exc_info=True)

        thread = threading.Thread(target=export_thread, daemon=True)
        thread.start()
