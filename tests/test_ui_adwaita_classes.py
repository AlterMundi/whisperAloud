"""HIG-01: the GUI is a libadwaita application.

Adw.Application initializes libadwaita (stylesheet, Adw.StyleManager dark-style
handling); without it Adw widgets render unstyled. Only class relationships are
checked here (no display needed); tests/gui/test_main_window_adwaita_smoke.py
checks the built widget tree.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402


def test_app_is_adw_application_with_same_id_and_flags():
    from whisper_aloud.ui.app import WhisperAloudApp

    assert issubclass(WhisperAloudApp, Adw.Application)
    app = WhisperAloudApp()
    assert app.get_application_id() == "org.fede.whisperaloud.Gui"
    assert app.get_flags() & Gio.ApplicationFlags.HANDLES_OPEN


def test_main_window_is_adw_application_window():
    from whisper_aloud.ui.main_window import MainWindow

    assert issubclass(MainWindow, Adw.ApplicationWindow)
