"""Status bar widget for displaying system resource usage."""

import logging
import threading
import time

import gi
import psutil

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

from .utils import format_file_size

logger = logging.getLogger(__name__)


class StatusBar(Gtk.Box):
    """Status bar for displaying application and system status."""

    def __init__(self):
        """Initialize the status bar."""
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("wa-status-bar")
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        # Model info
        self.model_label = Gtk.Label(label="Model: --")
        self.model_label.add_css_class("dim-label")
        self.model_label.add_css_class("wa-status-text")
        self.append(self.model_label)

        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Language info
        self.language_label = Gtk.Label(label="Lang: --")
        self.language_label.add_css_class("dim-label")
        self.language_label.add_css_class("wa-status-text")
        self.append(self.language_label)

        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Memory usage
        self.memory_label = Gtk.Label(label="Mem: --")
        self.memory_label.add_css_class("dim-label")
        self.memory_label.add_css_class("wa-status-text")
        self.append(self.memory_label)

        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # CPU usage
        self.cpu_label = Gtk.Label(label="CPU: --")
        self.cpu_label.add_css_class("dim-label")
        self.cpu_label.add_css_class("wa-status-text")
        self.append(self.cpu_label)

        # Spacer — pushes status message to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.append(spacer)

        # Transient status message
        self.status_msg_label = Gtk.Label(label="")
        self.status_msg_label.add_css_class("dim-label")
        self.status_msg_label.add_css_class("wa-status-text")
        self.status_msg_label.set_halign(Gtk.Align.END)
        self.append(self.status_msg_label)

        self._status_clear_id: int = 0

        # Start monitoring thread
        self._monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        self._monitor_thread.start()

    def set_model_info(self, name: str, device: str, language: str = None):
        """
        Update model information.

        Args:
            name: Model name (e.g., "base")
            device: Compute device (e.g., "cpu", "cuda")
            language: Language code (e.g., "en", "es")
        """
        self.model_label.set_text(f"Model: {name} ({device})")
        if language:
            self.language_label.set_text(f"Lang: {language}")

    def set_status(self, text: str, timeout_ms: int = 4000) -> None:
        """Display a transient status message that auto-clears.

        Args:
            text: Message to display. Empty string clears immediately.
            timeout_ms: Milliseconds before auto-clear (default 4000).
        """
        if self._status_clear_id:
            GLib.source_remove(self._status_clear_id)
            self._status_clear_id = 0

        self.status_msg_label.set_text(text)

        if text:
            self._status_clear_id = GLib.timeout_add(
                timeout_ms, self._clear_status
            )

    def _clear_status(self) -> bool:
        """Clear the transient status label (GLib timeout callback)."""
        self.status_msg_label.set_text("")
        self._status_clear_id = 0
        return False

    def _monitor_resources(self):
        """Monitor system resources in background."""
        process = psutil.Process()

        while self._monitoring:
            try:
                # Memory usage (RSS)
                mem_info = process.memory_info()
                mem_str = format_file_size(mem_info.rss)

                # CPU usage
                cpu_percent = process.cpu_percent(interval=None)

                # Update UI
                GLib.idle_add(self._update_labels, mem_str, cpu_percent)

                time.sleep(5.0)  # Update every 5 seconds

            except Exception as e:
                logger.error(f"Resource monitoring error: {e}")
                time.sleep(5.0)

    def _update_labels(self, mem_str: str, cpu_percent: float) -> bool:
        """
        Update labels on main thread.

        Args:
            mem_str: Formatted memory string
            cpu_percent: CPU usage percentage

        Returns:
            False to remove idle callback
        """
        self.memory_label.set_text(f"Mem: {mem_str}")
        self.cpu_label.set_text(f"CPU: {cpu_percent:.1f}%")
        return False

    def start_monitoring(self):
        """Start monitoring."""
        if not self._monitoring:
            self._monitoring = True
            self._monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
            self._monitor_thread.start()

    def cleanup(self):
        """Stop monitoring."""
        self._monitoring = False
        if hasattr(self, '_monitor_thread') and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)
