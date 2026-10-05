"""Headless GTK smoke test for MainWindow's "Transcribe file..." flow
(WHISP-96): the header button/Ctrl+O action, drag-and-drop, and the
FileTranscriptionProgress/Ready/Error signal wiring.

Builds a real MainWindow under a live Xvfb display -- see tests/gui/
test_main_window_partial_smoke.py for the pattern this mirrors (a fake
WhisperAloudClient-shaped object wired up exactly the way
`_on_daemon_connected` wires a real one).

Gtk.FileDialog itself is not exercised here (it needs a real portal/desktop
to answer it) -- these tests call `_start_file_transcription()` /
`_on_file_drop()` directly, i.e. "mock FileDialog" by skipping it and
driving the code path it would otherwise feed, same as the ticket brief
asks for.

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


class _FakeClient:
    """Stands in for service.client.WhisperAloudClient: records subscribed
    callbacks and lets the test fire signals exactly as the daemon would."""

    def __init__(self):
        self.is_connected = True
        self._file_progress_cb = None
        self._file_ready_cb = None
        self._file_error_cb = None
        self.transcribe_file_calls = []
        self._next_job_id = "job-1"

    # --- subscription surface used by MainWindow._on_daemon_connected ---
    def on_model_state_changed(self, callback):
        pass

    def get_status(self):
        return {"state": "idle", "model_state": "ready", "model_error": ""}

    def on_status_changed(self, callback):
        pass

    def on_transcription_ready(self, callback):
        pass

    def on_level_update(self, callback):
        pass

    def on_config_changed(self, callback):
        """HIG-07: MainWindow keeps its config copy live through ConfigChanged."""
        self._config_changed_cb = callback

    def get_config(self):
        return {}

    def on_error(self, callback):
        pass

    def on_partial_transcript(self, callback):
        pass

    def on_file_transcription_progress(self, callback):
        self._file_progress_cb = callback

    def on_file_transcription_ready(self, callback):
        self._file_ready_cb = callback

    def on_file_transcription_error(self, callback):
        self._file_error_cb = callback

    def watch_name(self, on_connected=None, on_disconnected=None):
        pass

    def get_history(self, limit=50):
        return []

    def transcribe_file(self, path, options):
        self.transcribe_file_calls.append((path, options))
        return self._next_job_id

    # --- test helpers: fire signals as the daemon would ---
    def emit_file_progress(self, job_id, stage):
        assert self._file_progress_cb is not None, "GUI never subscribed on_file_transcription_progress"
        self._file_progress_cb(job_id, stage)
        _pump_main_loop()

    def emit_file_ready(self, job_id, text, meta):
        assert self._file_ready_cb is not None, "GUI never subscribed on_file_transcription_ready"
        self._file_ready_cb(job_id, text, meta)
        _pump_main_loop()

    def emit_file_error(self, job_id, code, message):
        assert self._file_error_cb is not None, "GUI never subscribed on_file_transcription_error"
        self._file_error_cb(job_id, code, message)
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

    app = Gtk.Application(
        application_id="org.fede.whisperaloud.GuiTest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()
    window = MainWindow(application=app)

    fake_client = _FakeClient()
    window._on_daemon_connected(fake_client)
    return window, fake_client


def _make_disconnected_window():
    """Like _make_window(), but never calls _on_daemon_connected() -- for
    exercising open_file_for_transcription()'s event-driven queueing while
    no client is attached yet, as happens when a file is opened right at
    app startup, before the daemon connection lands."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    Adw.init()

    from whisper_aloud.ui.main_window import MainWindow

    app = Gtk.Application(
        application_id="org.fede.whisperaloud.GuiTest",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register()
    window = MainWindow(application=app)
    return window


def _pump_main_loop() -> None:
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def _buffer_text(window) -> str:
    buf = window.text_view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)


def _capture_status(window) -> list:
    """Record every window.status_bar.set_status(...) call's text, in
    order, without disturbing the real widget update. Reading the label's
    *final* text after pumping the main loop is racy here: MainWindow's own
    __init__ already queued a real (and, on this test bus, doomed-to-fail)
    daemon connect attempt via GLib.idle_add, whose failure handler also
    calls set_status() and can run interleaved with the signal emissions
    this test fires -- so only the call list, not the end state, is a
    reliable assertion target.
    """
    calls: list = []
    original = window.status_bar.set_status

    def _recording_set_status(text, *args, **kwargs):
        calls.append(text)
        return original(text, *args, **kwargs)

    window.status_bar.set_status = _recording_set_status
    return calls


def test_start_file_transcription_calls_client_and_shows_status():
    window, fake_client = _make_window()
    status_calls = _capture_status(window)

    window._start_file_transcription("/tmp/meeting.wav")

    assert fake_client.transcribe_file_calls == [("/tmp/meeting.wav", {})]
    assert window._file_job_id == "job-1"
    assert any("meeting.wav" in text for text in status_calls)


def test_file_transcription_ready_renders_text_and_enables_buttons():
    window, fake_client = _make_window()
    window._start_file_transcription("/tmp/meeting.wav")

    fake_client.emit_file_ready(
        "job-1",
        "the transcribed text",
        {"history_id": 7, "source_name": "meeting.wav"},
    )

    assert _buffer_text(window) == "the transcribed text"
    assert window.copy_button.get_sensitive()
    assert window.clear_button.get_sensitive()
    assert window._file_job_id is None


def test_stale_file_transcription_ready_is_ignored():
    """A FileTranscriptionReady for a job that isn't the current one (e.g.
    the user started a second file, or the id just doesn't match) must not
    clobber the text view."""
    window, fake_client = _make_window()
    window._start_file_transcription("/tmp/meeting.wav")

    fake_client.emit_file_ready("some-other-job", "should not appear", {})

    assert _buffer_text(window) == ""
    assert window._file_job_id == "job-1"


def test_file_transcription_error_shown_in_status_bar():
    window, fake_client = _make_window()
    window._start_file_transcription("/tmp/meeting.wav")
    status_calls = _capture_status(window)

    fake_client.emit_file_error("job-1", "transcription_failed", "boom")

    assert any("boom" in text for text in status_calls)
    assert window._file_job_id is None


def test_file_progress_updates_status_bar():
    window, fake_client = _make_window()
    window._start_file_transcription("/tmp/meeting.wav")
    status_calls = _capture_status(window)

    fake_client.emit_file_progress("job-1", "transcribing")

    assert any("Transcribing" in text for text in status_calls)


class _FakeGioFile:
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path


class _FakeFileList:
    def __init__(self, paths):
        self._files = [_FakeGioFile(p) for p in paths]

    def get_files(self):
        return self._files


def test_drop_with_supported_file_starts_transcription():
    window, fake_client = _make_window()

    handled = window._on_file_drop(None, _FakeFileList(["/tmp/clip.wav"]), 0, 0)

    assert handled is True
    assert fake_client.transcribe_file_calls == [("/tmp/clip.wav", {})]


def test_drop_with_unsupported_file_is_rejected():
    window, fake_client = _make_window()

    handled = window._on_file_drop(None, _FakeFileList(["/tmp/notes.txt"]), 0, 0)

    assert handled is False
    assert fake_client.transcribe_file_calls == []


def test_drop_picks_first_supported_among_multiple():
    window, fake_client = _make_window()

    handled = window._on_file_drop(
        None, _FakeFileList(["/tmp/notes.txt", "/tmp/clip.mp3"]), 0, 0
    )

    assert handled is True
    assert fake_client.transcribe_file_calls == [("/tmp/clip.mp3", {})]


# ─── open_file_for_transcription(): event-driven queueing (WHISP-96) ──────
#
# No GLib.timeout_add retry loop here: a file opened before the daemon
# connection is up must be queued and consumed exactly when MainWindow
# transitions into a connected state (_on_daemon_connected /
# _handle_reconnection), not after any fixed delay.


def test_open_file_before_connection_is_queued_not_started():
    window = _make_disconnected_window()

    window.open_file_for_transcription("/tmp/meeting.wav")

    assert window._pending_open_path == "/tmp/meeting.wav"
    assert window._file_job_id is None


def test_queued_open_file_runs_once_daemon_connects():
    window = _make_disconnected_window()
    window.open_file_for_transcription("/tmp/meeting.wav")

    fake_client = _FakeClient()
    window._on_daemon_connected(fake_client)

    assert fake_client.transcribe_file_calls == [("/tmp/meeting.wav", {})]
    assert window._pending_open_path is None


def test_open_file_while_already_connected_starts_immediately():
    window, fake_client = _make_window()

    window.open_file_for_transcription("/tmp/meeting.wav")

    assert fake_client.transcribe_file_calls == [("/tmp/meeting.wav", {})]
    assert window._pending_open_path is None


def test_queued_open_file_survives_a_reconnection_cycle():
    """A file opened while the daemon was unavailable must still run once
    the user's "Retry Connection" (or an automatic reconnect) succeeds --
    _handle_reconnection() is the code path a real reconnect/manual retry
    both funnel through."""
    window = _make_disconnected_window()
    window.open_file_for_transcription("/tmp/meeting.wav")
    assert window._pending_open_path == "/tmp/meeting.wav"

    fake_client = _FakeClient()
    window.client = fake_client
    window._handle_reconnection()

    assert fake_client.transcribe_file_calls == [("/tmp/meeting.wav", {})]
    assert window._pending_open_path is None


def test_second_open_file_call_replaces_the_queued_one():
    window = _make_disconnected_window()

    window.open_file_for_transcription("/tmp/first.wav")
    window.open_file_for_transcription("/tmp/second.wav")

    assert window._pending_open_path == "/tmp/second.wav"

    fake_client = _FakeClient()
    window._on_daemon_connected(fake_client)

    assert fake_client.transcribe_file_calls == [("/tmp/second.wav", {})]
