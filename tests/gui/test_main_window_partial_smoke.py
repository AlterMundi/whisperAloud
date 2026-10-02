"""Headless GTK smoke test for MainWindow's live partial-transcript rendering
(WHISP-58).

Builds a real MainWindow under a live Xvfb display (see tests/gui/
test_settings_dialog_smoke.py for why this needs a display, and why that is safe:
same WHISPER_ALOUD_GUI_TEST opt-in gate, same scripts/test_gui.sh wrapper). A fake
WhisperAloudClient-shaped object stands in for the real D-Bus client and is wired up
exactly the way `_on_daemon_connected` wires a real one, so we exercise the real
`client.on_partial_transcript(...)` subscription path, not just the internal
`_handle_partial_transcript` helper directly.

Opt-in only, via scripts/test_gui.sh — never run directly with a plain `pytest`.
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
            "scripts/test_gui.sh — never directly."
        ),
    ),
]


class _FakeClient:
    """Stands in for service.client.WhisperAloudClient: records subscribed
    callbacks and lets the test fire signals exactly as the daemon would."""

    def __init__(self):
        self.is_connected = True
        self._partial_cb = None
        self._ready_cb = None

    # --- subscription surface used by MainWindow._on_daemon_connected ---
    def on_status_changed(self, callback):
        pass

    def on_transcription_ready(self, callback):
        self._ready_cb = callback

    def on_level_update(self, callback):
        pass

    def on_error(self, callback):
        pass

    def on_partial_transcript(self, callback):
        self._partial_cb = callback

    def watch_name(self, on_connected=None, on_disconnected=None):
        pass

    def get_history(self, limit=50):
        return []

    # --- test helpers: fire signals as the daemon would ---
    def emit_partial_transcript(self, session_id, committed, tentative, seq):
        assert self._partial_cb is not None, "GUI never subscribed on_partial_transcript"
        self._partial_cb(session_id, committed, tentative, seq)
        _pump_main_loop()  # the real handler marshals via GLib.idle_add

    def emit_transcription_ready(self, text, meta):
        assert self._ready_cb is not None, "GUI never subscribed on_transcription_ready"
        self._ready_cb(text, meta)
        _pump_main_loop()


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
    # throwaway test application id — MainWindow just needs *an* application.
    app = Gtk.Application(
        application_id="org.fede.whisperaloud.GuiTest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()  # emits GApplication::startup, required before adding windows
    window = MainWindow(application=app)

    fake_client = _FakeClient()
    # Wire up exactly as a real daemon connection would (signal subscriptions,
    # config load, state transitions) — not a shortcut around MainWindow's logic.
    window._on_daemon_connected(fake_client)
    return window, fake_client


def _pump_main_loop() -> None:
    """Drain pending GLib idle callbacks (GLib.idle_add) without a running
    Gtk.main() — MainWindow's daemon-signal handlers marshal onto the main thread
    via GLib.idle_add since the real daemon emits from a background thread."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def _buffer_text(window) -> str:
    buf = window.text_view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)


def _tentative_is_dimmed(window, committed: str, tentative: str) -> bool:
    """True if the characters after `committed` carry a distinct (dimmed) tag from
    those before it, i.e. the tentative span got its own Pango-markup-derived tag."""
    buf = window.text_view.get_buffer()
    if not tentative:
        return True  # nothing to dim
    committed_iter = buf.get_iter_at_offset(max(0, len(committed) - 1)) if committed else None
    tentative_iter = buf.get_iter_at_offset(len(committed))
    tentative_tags = set(tentative_iter.get_tags())
    if committed_iter is not None:
        committed_tags = set(committed_iter.get_tags())
    else:
        committed_tags = set()
    return bool(tentative_tags) and tentative_tags != committed_tags


def test_partial_transcript_then_final_renders_committed_and_tentative():
    window, fake_client = _make_window()

    fake_client.emit_partial_transcript("sess-1", "Hello ", "wor", 1)
    assert _buffer_text(window) == "Hello wor"
    assert _tentative_is_dimmed(window, "Hello ", "wor")

    fake_client.emit_partial_transcript("sess-1", "Hello world", "", 2)
    assert _buffer_text(window) == "Hello world"

    fake_client.emit_transcription_ready(
        "Hello world, the final transcript.", {"session_id": "sess-1"}
    )
    assert _buffer_text(window) == "Hello world, the final transcript."


def test_transcription_ready_from_mismatching_session_ignored():
    window, fake_client = _make_window()

    fake_client.emit_partial_transcript("sess-1", "Hello ", "wor", 1)
    fake_client.emit_transcription_ready(
        "Stale result from a superseded session", {"session_id": "sess-other"}
    )
    # Must not clobber the current session's partial view.
    assert _buffer_text(window) == "Hello wor"


def test_partial_from_other_session_ignored():
    window, fake_client = _make_window()

    fake_client.emit_partial_transcript("sess-1", "Hello ", "wor", 1)
    fake_client.emit_partial_transcript("sess-other", "Should not appear", "", 99)
    assert _buffer_text(window) == "Hello wor"


def test_out_of_order_seq_ignored():
    window, fake_client = _make_window()

    fake_client.emit_partial_transcript("sess-1", "Hello world", "", 5)
    fake_client.emit_partial_transcript("sess-1", "Hel", "lo", 2)
    assert _buffer_text(window) == "Hello world"


def test_markup_injection_in_partial_is_rendered_as_literal_text():
    """A partial containing Pango markup syntax must show up verbatim, not be
    interpreted (e.g. bolded, or dropped as an unknown tag)."""
    window, fake_client = _make_window()

    fake_client.emit_partial_transcript(
        "sess-1", "committed ", "<b>injected</b> & <script>", 1
    )
    assert _buffer_text(window) == "committed <b>injected</b> & <script>"


def test_markup_injection_in_final_text_is_rendered_as_literal_text():
    window, fake_client = _make_window()

    fake_client.emit_transcription_ready(
        "<b>bold?</b> & <i>italic?</i>", {"session_id": "sess-1"}
    )
    assert _buffer_text(window) == "<b>bold?</b> & <i>italic?</i>"
