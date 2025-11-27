"""Sidebar panel for transcription history."""

import logging
import threading
from typing import List, Optional
from datetime import datetime

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, GObject

from ..persistence.history_manager import HistoryManager
from ..persistence.models import HistoryEntry
from .history_item import HistoryItem

logger = logging.getLogger(__name__)


class HistoryPanel(Gtk.Box):
    """Sidebar panel for transcription history."""

    __gsignals__ = {
        'entry-selected': (GObject.SignalFlags.RUN_FIRST, None, (object,)),  # Emits HistoryEntry
    }

    def __init__(self, history_manager: HistoryManager):
        """
        Initialize history panel.

        Args:
            history_manager: HistoryManager instance
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.history_manager = history_manager

        # Search debouncing
        self._search_timeout_id = None
        self._search_debounce_ms = 500
        self._current_query = ""
        self._show_favorites_only = False

        self._build_ui()
        
        # Load initial data
        self.refresh_recent()

    def _build_ui(self):
        """Build panel UI."""
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label="History")
        label.add_css_class("heading")
        header_box.append(label)
        
        # Refresh button
        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh history")
        refresh_btn.add_css_class("flat")
        refresh_btn.connect("clicked", lambda b: self.refresh_recent())
        header_box.append(refresh_btn)
        
        self.append(header_box)

        # Search box
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search transcriptions...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_box.append(self.search_entry)

        # Filter buttons
        self.favorites_button = Gtk.ToggleButton()
        self.favorites_button.set_icon_name("starred-symbolic")
        self.favorites_button.set_tooltip_text("Show favorites only")
        self.favorites_button.connect("toggled", self._on_filter_toggled)
        search_box.append(self.favorites_button)

        self.append(search_box)

        # Scrolled list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.connect("row-activated", self._on_row_activated)
        self.list_box.add_css_class("rich-list")
        scrolled.set_child(self.list_box)

        self.append(scrolled)

        # Export button
        export_button = Gtk.Button(label="Export History...")
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
                if self._show_favorites_only:
                    results = self.history_manager.get_favorites(limit=50)
                    # Apply text filter in memory if needed (since get_favorites doesn't take query)
                    if query:
                        query_lower = query.lower()
                        results = [r for r in results if query_lower in r.text.lower()]
                elif query:
                    results = self.history_manager.search(query, limit=50)
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
                self.list_box.append(item)
        
        return False

    def _group_by_date(self, entries: List[HistoryEntry]) -> dict:
        """Group entries by date string."""
        grouped = {}
        today = datetime.now().date()
        
        for entry in entries:
            if not entry.timestamp:
                key = "Unknown Date"
            else:
                date = entry.timestamp.date()
                if date == today:
                    key = "Today"
                elif (today - date).days == 1:
                    key = "Yesterday"
                else:
                    key = date.strftime("%B %d, %Y")
            
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(entry)
            
        return grouped

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
        """Handle delete request from item."""
        # Confirm dialog could go here, but for now just delete
        success = self.history_manager.delete(entry_id)
        if success:
            self.refresh_recent()

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