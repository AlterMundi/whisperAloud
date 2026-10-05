"""Headless GTK smoke test for the libadwaita foundation (HIG-01).

Builds the real WhisperAloudApp + MainWindow under Xvfb and checks:
- the window is an Adw.ApplicationWindow whose content is an Adw.ToolbarView with
  an Adw.HeaderBar on top (holding the same controls as before) and the status bar
  at the bottom;
- dark style follows the system (Adw.StyleManager is not forced either way);
- behaviours the maintainer explicitly keeps: Esc clears the transcript, and
  selecting a history row copies its text to the clipboard.

Same Xvfb opt-in gate as the other tests/gui/ smoke tests -- run via
scripts/test_gui.sh, never directly with a plain `pytest`.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

_GUI_DISPLAY = os.environ.get("WHISPER_ALOUD_GUI_DISPLAY", "")
_GUI_XAUTHORITY = os.environ.get("WHISPER_ALOUD_GUI_XAUTHORITY", "")
_GUI_ENABLED = (
    os.environ.get("WHISPER_ALOUD_GUI_TEST") == "1"
    and "WAYLAND_DISPLAY" not in os.environ
    and _GUI_DISPLAY.startswith(":")
    and _GUI_DISPLAY.split(".")[0] not in (":0", ":1")
    and not os.environ.get("WHISPER_ALOUD_TEST_REAL_HOME", os.path.expanduser("~")).startswith("/home/")
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


def _pump(seconds: float = 0.0) -> None:
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while True:
        while ctx.iteration(False):
            pass
        if time.monotonic() >= end:
            return
        time.sleep(0.01)


@pytest.fixture(scope="module")
def app_window():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gio, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")

    from whisper_aloud.ui.app import WhisperAloudApp

    app = WhisperAloudApp()
    # NON_UNIQUE: other smoke tests in this process register the same
    # application id; a second unique registration would turn this instance
    # into a remote proxy that never runs startup.
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)  # emits "startup" (Adw init + app CSS + actions)
    app.activate()  # creates and presents MainWindow
    _pump(0.1)
    yield app, app.window
    app.window.cleanup()


def _is_inside(widget, ancestor) -> bool:
    w = widget
    while w is not None:
        if w is ancestor:
            return True
        w = w.get_parent()
    return False


def test_window_uses_toolbar_view_with_adw_header_bar(app_window):
    from gi.repository import Adw

    _app, win = app_window
    assert isinstance(win, Adw.ApplicationWindow)

    content = win.get_content()
    assert isinstance(content, Adw.ToolbarView)
    assert isinstance(win.header_bar, Adw.HeaderBar)
    assert _is_inside(win.header_bar, content)

    title = win.header_bar.get_title_widget()
    assert isinstance(title, Adw.WindowTitle)
    assert title.get_title() == "WhisperAloud"

    for control in (
        win.history_toggle,
        win.transcribe_file_button,
        win.settings_button,
        win.lang_dropdown,
    ):
        assert _is_inside(control, win.header_bar), control

    # The status bar stays at the bottom, as a toolbar-view bottom bar, outside
    # the scrolling content.
    assert _is_inside(win.status_bar, content)
    assert not _is_inside(win.status_bar, content.get_content())
    assert _is_inside(win.paned, content.get_content())


def test_dark_style_follows_the_system(app_window):
    from gi.repository import Adw

    manager = Adw.StyleManager.get_default()
    assert manager.get_color_scheme() == Adw.ColorScheme.DEFAULT


def test_escape_still_clears_the_transcript(app_window):
    from gi.repository import Gdk

    _app, win = app_window
    buffer = win.text_view.get_buffer()
    buffer.set_text("some transcript")
    win.clear_button.set_sensitive(True)

    assert win._on_key_pressed(None, Gdk.KEY_Escape, 0, 0) is True
    assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) == ""


def test_selecting_a_history_row_still_copies_it_to_the_clipboard(app_window):
    _app, win = app_window
    entry = SimpleNamespace(id=42, text="copied from history", confidence=0.9, duration=1.5)

    # Avoid the edit-save path talking to a daemon: the entry id is only
    # bookkeeping here.
    win._on_history_entry_selected(None, entry)

    result = {}
    clipboard = win.get_clipboard()
    clipboard.read_text_async(
        None, lambda cb, res: result.setdefault("text", cb.read_text_finish(res))
    )
    deadline = time.monotonic() + 3
    while "text" not in result and time.monotonic() < deadline:
        _pump(0.02)
    assert result.get("text") == "copied from history"
