"""Headless GTK smoke test for MainWindow's "daemon unavailable" state
(WHISP-60).

Builds a real MainWindow under a live Xvfb display (see tests/gui/
test_settings_dialog_smoke.py for why this needs a display, and why that is
safe: same WHISPER_ALOUD_GUI_TEST opt-in gate, same scripts/test_gui.sh
wrapper) and drives it through a failed D-Bus activation exactly the way
_init_components_async's background thread would report one, without
spawning a real connection attempt for the assertions themselves. Confirms:
the daemon-connect_logic decision is wired up (exhausting the auto-retry
budget shows the "daemon not running" dialog with a working Retry Connection
button), and that clicking Retry resets the attempt counter and starts a
fresh attempt (WhisperAloudClient() itself still runs for that one real
attempt -- against the private test bus scripts/test_gui.sh provides, where
it deterministically fails to find the service and does not touch the real
session).

Opt-in only, via scripts/test_gui.sh -- never run directly with a plain
`pytest`.
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
    # Never the user's own X/Xwayland displays, and only inside the wrapper's
    # disposable HOME: a hand-set WHISPER_ALOUD_GUI_TEST must not put windows
    # on the real screen.
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


def _pump_main_loop() -> None:
    """Drain pending GLib idle callbacks without a running Gtk.main()."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def _make_window():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    Adw.init()

    from whisper_aloud.ui.main_window import MainWindow

    # NON_UNIQUE avoids needing a working D-Bus session-bus registration for this
    # throwaway test application id -- MainWindow just needs *an* application.
    app = Gtk.Application(
        application_id="org.fede.whisperaloud.GuiTest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()  # emits GApplication::startup, required before adding windows

    # __init__ schedules the real _init_components_async via GLib.idle_add,
    # which would spawn a background thread making a real (if harmless --
    # private test bus, fails fast) D-Bus connection attempt whose result
    # lands back on the main loop asynchronously, racing with this test's own
    # explicit _on_connect_attempt_failed() calls and attempt-counter
    # assertions. Suppress that one real attempt (never started, so it can
    # never fire later) and drain its idle_add before the test takes over.
    with patch("whisper_aloud.ui.main_window.threading.Thread"):
        window = MainWindow(application=app)
        _pump_main_loop()
    window._connect_attempt = 0
    # That suppressed attempt still opened a "Connecting..." dialog (the
    # thread it would have hidden it from never ran) -- close it so it
    # doesn't get mistaken for the daemon-unavailable dialog below.
    window._hide_loading_dialog()
    _pump_main_loop()
    return window


def _find_button_by_label(widget, label: str):
    """Depth-first search of a GTK4 widget tree (get_first_child/
    get_next_sibling, since GTK4 containers have no get_children()) for a
    Gtk.Button whose label matches exactly."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        return widget

    child = widget.get_first_child()
    while child is not None:
        found = _find_button_by_label(child, label)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _toplevels_snapshot():
    """Identities of every current top-level window. The test process runs
    many GUI smoke tests in one pytest session, so previous tests' windows
    (and any dialogs they left open) are still alive -- comparisons must use
    a before/after diff, never an absolute "no extra windows" assertion."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    return {id(w) for w in Gtk.Window.get_toplevels()}


def _new_toplevels_since(snapshot):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    return [w for w in Gtk.Window.get_toplevels() if id(w) not in snapshot]


def test_exhausting_auto_retries_shows_daemon_unavailable_dialog():
    from whisper_aloud.ui.daemon_connect_logic import DEFAULT_MAX_AUTO_RETRIES

    window = _make_window()
    before = _toplevels_snapshot()

    window._on_connect_attempt_failed(DEFAULT_MAX_AUTO_RETRIES)
    _pump_main_loop()

    assert window._daemon_available is False
    new_windows = _new_toplevels_since(before)
    assert len(new_windows) == 1, "expected exactly one new window (the unavailable dialog)"
    dialog = new_windows[0]
    retry_button = _find_button_by_label(dialog, "Retry Connection")
    assert retry_button is not None, "daemon-unavailable dialog must offer a retry button"


def test_retrying_resets_the_attempt_counter_and_reconnects():
    from whisper_aloud.ui.daemon_connect_logic import DEFAULT_MAX_AUTO_RETRIES

    window = _make_window()
    before = _toplevels_snapshot()
    window._on_connect_attempt_failed(DEFAULT_MAX_AUTO_RETRIES)
    _pump_main_loop()

    dialog = _new_toplevels_since(before)[0]
    retry_button = _find_button_by_label(dialog, "Retry Connection")

    window._connect_attempt = 99  # simulate having exhausted a previous burst
    # The retry-click thread itself is real (see _make_window's docstring on
    # why that's safe); suppress only its background thread the same way, so
    # this assertion isn't racing a real connect attempt's own idle_add.
    with patch("whisper_aloud.ui.main_window.threading.Thread"):
        retry_button.emit("clicked")
        _pump_main_loop()
    window._hide_loading_dialog()

    # _init_components_async increments on entry, so a reset-then-restart
    # lands back on attempt 1, not a continuation of the old count.
    assert window._connect_attempt == 1


def test_intermediate_failure_schedules_auto_retry_without_showing_dialog():
    window = _make_window()
    before = _toplevels_snapshot()

    window._on_connect_attempt_failed(1)  # first failure, well under the budget
    _pump_main_loop()

    assert window._daemon_available is False
    assert window._retry_timeout_id != 0, "an automatic retry should have been scheduled"
    # No extra dialog yet -- only the manual-retry dialog from exhausting the
    # auto-retry budget creates one.
    assert _new_toplevels_since(before) == []

    # Cancel the scheduled retry: left running, it would fire mid-way through
    # a *later* test (this window outlives the test via the GSource closure)
    # and spawn a real, if harmless, background connect attempt.
    from gi.repository import GLib

    GLib.source_remove(window._retry_timeout_id)


def test_daemon_vanishing_offers_actionable_retry():
    """Field bug 4: a daemon that vanishes after being connected (e.g.
    `whisper-aloud quit`) must offer the same clickable "Retry Connection"
    dialog as a daemon that was never reachable -- not just a non-clickable
    status message that only recovers if something else restarts it."""
    window = _make_window()
    before = _toplevels_snapshot()

    window._handle_disconnection()
    _pump_main_loop()

    assert window._daemon_available is False
    new_windows = _new_toplevels_since(before)
    assert len(new_windows) == 1, "expected exactly one new window (the unavailable dialog)"
    dialog = new_windows[0]
    retry_button = _find_button_by_label(dialog, "Retry Connection")
    assert retry_button is not None, "disconnection dialog must offer a retry button"


def test_disconnection_dialog_is_not_duplicated_on_repeated_loss():
    window = _make_window()
    before = _toplevels_snapshot()

    window._handle_disconnection()
    _pump_main_loop()
    window._handle_disconnection()
    _pump_main_loop()

    assert len(_new_toplevels_since(before)) == 1, "a second loss must not stack a new dialog"


def test_reconnecting_closes_the_unavailable_dialog():
    window = _make_window()
    before = _toplevels_snapshot()

    window._handle_disconnection()
    _pump_main_loop()
    assert len(_new_toplevels_since(before)) == 1

    window._handle_reconnection()
    _pump_main_loop()

    assert window._unavailable_dialog is None
    assert _new_toplevels_since(before) == []
