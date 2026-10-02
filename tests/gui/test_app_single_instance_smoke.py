"""Headless GTK smoke test for WhisperAloudApp's single-instance behavior
(WHISP-60).

Flathub rejects tray-only apps (flathub#9368); the fix is a GUI window as the
launch entry point, re-launched from the app grid presenting the *existing*
window rather than opening a second one. GLib/GIO already enforce the
single-process part of that (GApplication registers application_id on the
session bus; a second `run()` in another process hands off to the first
instance's `activate` signal instead of starting a new process) -- that
framework behavior isn't re-tested here. What WhisperAloudApp's own code is
responsible for is do_activate() not creating a second MainWindow when
activation fires again (e.g. a second launch, or a notification click): see
src/whisper_aloud/ui/app.py.

Uses the same Xvfb opt-in gate as the other tests/gui/ smoke tests -- run via
scripts/test_gui.sh, never directly with a plain `pytest`.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

_GUI_DISPLAY = os.environ.get("WHISPER_ALOUD_GUI_DISPLAY", "")
_GUI_XAUTHORITY = os.environ.get("WHISPER_ALOUD_GUI_XAUTHORITY", "")
_GUI_ENABLED = (
    os.environ.get("WHISPER_ALOUD_GUI_TEST") == "1"
    and "WAYLAND_DISPLAY" not in os.environ
    and _GUI_DISPLAY.startswith(":")
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


def _make_app():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")

    from whisper_aloud.ui.app import WhisperAloudApp

    # The real application_id -- do_startup()/do_activate() are this test's
    # subject, not GApplication's own bus registration, so register() isn't
    # required for them to run correctly. MainWindow's own daemon-connect
    # background thread is irrelevant to this test and left unsuppressed
    # (same as other smoke tests that don't touch it); it fails harmlessly
    # against the private test bus.
    return WhisperAloudApp()


def test_do_activate_reuses_the_existing_window_instead_of_opening_a_second_one():
    app = _make_app()
    # register() (not calling do_startup()/do_activate() directly -- GIO
    # segfaults if the "startup" lifecycle hook runs on an unregistered
    # app) emits "startup" the same way a real launch does, on this private
    # test bus (scripts/test_gui.sh). activate() then emits "activate",
    # driving do_activate() exactly as a second real launch's hand-off
    # would.
    assert app.register(), "failed to register on the private test bus"

    app.activate()
    first_window = app.window
    assert first_window is not None

    with patch.object(first_window, "present") as mock_present:
        app.activate()

    assert app.window is first_window, "a second activate must not replace the window"
    mock_present.assert_called_once()
