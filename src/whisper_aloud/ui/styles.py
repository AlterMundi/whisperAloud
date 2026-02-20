"""Application-wide GTK style provider."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk

logger = logging.getLogger(__name__)


APP_CSS = b"""
headerbar.wa-headerbar {
  padding: 6px 10px;
  border-bottom: 1px solid alpha(@theme_fg_color, 0.08);
  background: linear-gradient(
    to bottom,
    alpha(@theme_base_color, 0.94),
    alpha(@theme_base_color, 0.80)
  );
}

window.wa-app-window,
window.wa-dialog-window {
  background: @theme_bg_color;
}

box.wa-surface {
  background: alpha(@theme_base_color, 0.72);
  border: 1px solid alpha(@theme_fg_color, 0.08);
  border-radius: 16px;
  padding: 14px;
  box-shadow: 0 10px 20px alpha(black, 0.04);
}

label.wa-status-chip {
  font-weight: 600;
  letter-spacing: 0.2px;
  margin-left: 8px;
  margin-right: 8px;
}

stackswitcher.wa-tabs button {
  border-radius: 999px;
  padding: 6px 14px;
  font-weight: 600;
  min-height: 32px;
}

label.wa-section-title {
  font-weight: 700;
  letter-spacing: 0.2px;
}

box.wa-setting-row {
  min-height: 38px;
  padding-top: 3px;
  padding-bottom: 3px;
}

listbox.wa-shortcuts-list row {
  padding-top: 2px;
  padding-bottom: 2px;
}

label.wa-help {
  color: alpha(@theme_fg_color, 0.72);
}

scrolledwindow.wa-output-wrap {
  border-radius: 16px;
  border: 1px solid alpha(@theme_fg_color, 0.08);
  background: alpha(@theme_base_color, 0.7);
}

textview.wa-output {
  font-size: 1.02em;
  line-height: 1.45;
}

button.wa-ghost {
  border-radius: 10px;
}

box.wa-history-panel {
  background: alpha(@theme_base_color, 0.6);
  border: 1px solid alpha(@theme_fg_color, 0.08);
  border-radius: 14px;
  padding: 8px;
}

box.wa-history-header {
  margin-bottom: 2px;
}

box.wa-history-search {
  margin-bottom: 4px;
}

entry.wa-search-entry {
  border-radius: 10px;
  padding-top: 4px;
  padding-bottom: 4px;
}

scrolledwindow.wa-history-list-wrap {
  border-radius: 12px;
  border: 1px solid alpha(@theme_fg_color, 0.08);
  background: alpha(@theme_base_color, 0.82);
}

listbox.wa-history-list row {
  border-radius: 10px;
  margin: 1px 3px;
}

listboxrow.wa-history-item {
  transition: all 120ms ease-out;
  min-height: 52px;
}

label.wa-history-date {
  font-weight: 700;
}

label.wa-history-time {
  font-variant-numeric: tabular-nums;
}

label.wa-history-title {
  font-weight: 600;
  letter-spacing: 0.1px;
}

label.wa-history-meta {
  opacity: 0.86;
}

box.wa-status-bar {
  border-top: 1px solid alpha(@theme_fg_color, 0.08);
  background: alpha(@theme_base_color, 0.75);
  min-height: 30px;
}

label.wa-status-text {
  font-variant-numeric: tabular-nums;
}

box.wa-level-panel {
  margin-top: 4px;
}

label.wa-level-caption {
  color: alpha(@theme_fg_color, 0.82);
}

label.wa-keycap {
  background: alpha(@accent_bg_color, 0.18);
  border: 1px solid alpha(@accent_bg_color, 0.44);
  border-radius: 9px;
  padding: 3px 8px;
  font-weight: 700;
}

button.wa-toolbar-btn,
dropdown.wa-language {
  border-radius: 10px;
  min-height: 34px;
}

button.wa-primary-action {
  min-height: 38px;
  padding-left: 14px;
  padding-right: 14px;
  font-weight: 650;
  letter-spacing: 0.15px;
}

popover.wa-preview-popover {
  border-radius: 10px;
}

label.wa-preview-label {
  padding: 8px 10px;
  font-size: 0.92em;
}
"""


def install_app_css() -> None:
    """Install application CSS provider for all displays."""
    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS)
        display = Gdk.Display.get_default()
        if display is None:
            logger.debug("No GDK display available, skipping CSS installation")
            return
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    except Exception as exc:
        logger.warning(f"Failed to install application CSS: {exc}")
