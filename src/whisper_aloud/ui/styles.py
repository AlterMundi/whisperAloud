"""Application-wide GTK style provider.

Since HIG-01 the look comes from libadwaita (loaded by Adw.Application): header
bars, buttons, lists, entries, window backgrounds and light/dark colours are
Adwaita's, selected with its style classes (``suggested-action``, ``pill``,
``flat``, ``card``, ``heading``, ``caption``, ``dim-label``, ``numeric``,
``monospace``...). This stylesheet only keeps rules for app-specific widgets with
no libadwaita equivalent on the supported floor (libadwaita 1.5). Every rule is
scoped to a ``wa-*`` class and uses ``currentColor`` instead of literal or
GTK3-era ``@theme_*`` colours, so it follows Adw.StyleManager
(tests/test_app_styles.py enforces this).
"""

import logging

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

logger = logging.getLogger(__name__)


APP_CSS = b"""
/* Key caps in the keyboard shortcuts window (ui/shortcuts_window.py). Mirrors
   libadwaita's "shortcut > .keycap", which only applies inside GtkShortcutLabel;
   Adw.ShortcutLabel needs libadwaita 1.8 (HIG-11 replaces this window). */
label.wa-keycap {
  min-width: 20px;
  padding: 2px 6px;
  border-radius: 6px;
  background-color: alpha(currentColor, 0.08);
  box-shadow: inset 0 -1px alpha(currentColor, 0.15);
  font-size: smaller;
}
"""


def install_app_css() -> None:
    """Install application CSS provider for all displays."""
    try:
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):  # GTK >= 4.12
            provider.load_from_string(APP_CSS.decode())
        else:
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
