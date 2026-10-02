"""Headless GTK smoke test for ErrorDialog.show_error (WHISP-80).

ErrorDialog.show_error used to build a raw, deprecated Gtk.MessageDialog with
a variable button set (an optional recovery action plus an always-present
Close) and an optional details expander appended to its content area. This
migrates it onto dialogs.show_dialog (Adw.AlertDialog / Adw.MessageDialog,
see tests/gui/test_settings_dialog_smoke.py for why that needs a real
display, and why that is safe -- same WHISPER_ALOUD_GUI_TEST opt-in gate,
same scripts/test_gui.sh wrapper) and confirms: the dialog actually appears,
the details expander is still present when details are given, and each
response (recovery action / Close) calls the right callback.

Opt-in only, via scripts/test_gui.sh -- never run directly with a plain
`pytest`.
"""

from __future__ import annotations

import os

import pytest

_GUI_DISPLAY = os.environ.get("WHISPER_ALOUD_GUI_DISPLAY", "")
_GUI_XAUTHORITY = os.environ.get("WHISPER_ALOUD_GUI_XAUTHORITY", "")
_GUI_ENABLED = (
    os.environ.get("WHISPER_ALOUD_GUI_TEST") == "1"
    and "WAYLAND_DISPLAY" not in os.environ
    and _GUI_DISPLAY.startswith(":")
    # Never the user's own X/Xwayland displays, and only inside the wrapper's
    # disposable HOME: a hand-set WHISPER_ALOUD_GUI_TEST must not put windows
    # on the real screen.
    and _GUI_DISPLAY.split(".")[0] not in (":0", ":1")
    and not os.path.expanduser("~").startswith("/home/")
)

if _GUI_ENABLED:
    os.environ["DISPLAY"] = _GUI_DISPLAY
    if _GUI_XAUTHORITY:
        os.environ["XAUTHORITY"] = _GUI_XAUTHORITY

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(
        not _GUI_ENABLED,
        reason=(
            "GUI smoke tests require WHISPER_ALOUD_GUI_TEST=1, no WAYLAND_DISPLAY, and "
            "an Xvfb DISPLAY preserved as WHISPER_ALOUD_GUI_DISPLAY. Run via "
            "scripts/test_gui.sh -- never directly."
        ),
    ),
]


def _init_gtk():
    """Initialize GTK/Adw against the Xvfb display and return the Gtk module."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    Adw.init()
    return Gtk


def test_show_error_without_details_or_recovery_shows_close_only(monkeypatch):
    gtk = _init_gtk()
    from whisper_aloud.ui.error_handler import ErrorDialog

    parent = gtk.Window()
    dialog = ErrorDialog.show_error(parent, "Transcription Error", "Something went wrong")

    assert dialog.get_heading() == "Transcription Error"
    assert dialog.has_response("close")
    assert not dialog.has_response("recovery")
    assert dialog.get_extra_child() is None


def test_show_error_with_details_shows_expander_with_details_text():
    gtk = _init_gtk()
    from whisper_aloud.ui.error_handler import ErrorDialog

    parent = gtk.Window()
    dialog = ErrorDialog.show_error(
        parent, "Model Loading Error", "Failed to load model", details="Traceback: boom"
    )

    expander = dialog.get_extra_child()
    assert isinstance(expander, gtk.Expander)
    assert expander.get_label() == "Show Details"

    scrolled = expander.get_child()
    details_view = scrolled.get_child()
    buf = details_view.get_buffer()
    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert text == "Traceback: boom"


def test_show_error_close_response_without_recovery_action_does_not_raise():
    gtk = _init_gtk()
    from whisper_aloud.ui.error_handler import ErrorDialog

    parent = gtk.Window()
    dialog = ErrorDialog.show_error(parent, "Transcription Error", "Something went wrong")

    dialog.emit("response", "close")  # must not raise, no recovery_action to call


def test_show_error_recovery_response_calls_recovery_callback_only():
    gtk = _init_gtk()
    from whisper_aloud.ui.error_handler import ErrorDialog

    parent = gtk.Window()
    calls = []
    dialog = ErrorDialog.show_error(
        parent,
        "Audio Device Error",
        "Failed to access audio device",
        recovery_action=("Open Settings", lambda: calls.append("recovery")),
    )

    assert dialog.has_response("recovery")
    assert dialog.get_response_label("recovery") == "Open Settings"

    dialog.emit("response", "close")
    assert calls == []  # Close must never invoke the recovery callback

    dialog.emit("response", "recovery")
    assert calls == ["recovery"]


def test_show_error_recovery_callback_exception_is_caught_and_logged(caplog):
    gtk = _init_gtk()
    from whisper_aloud.ui.error_handler import ErrorDialog

    parent = gtk.Window()

    def _boom():
        raise RuntimeError("recovery blew up")

    dialog = ErrorDialog.show_error(
        parent,
        "Audio Device Error",
        "Failed to access audio device",
        recovery_action=("Open Settings", _boom),
    )

    dialog.emit("response", "recovery")  # must not raise -- caught and logged

    assert any("recovery blew up" in r.message for r in caplog.records)
