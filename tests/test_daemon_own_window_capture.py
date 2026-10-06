"""Field bug: never capture/paste into WhisperAloud's own GUI window.

Stopping recording from the GUI window's Stop button (as opposed to the
global hotkey) made the GUI's OWN window the "captured" window -- it was
the one focused at the moment Stop was clicked. If the user then switched
to a different app while transcription ran, completion later re-activated
(refocused) the captured WhisperAloud window instead of leaving the user
in the app they had switched to. With the hotkey (GUI window not focused),
this never happened -- only the GUI stop path was affected.

Fix, in two parts:
  * capture time (StopRecording / _on_recorder_auto_stop in daemon.py):
    never store a WhisperAloud window as `_captured_by_job[job_id]`.
  * completion time, only reached when there was no captured window at
    all (disabled, unavailable, or filtered out above): never auto-type
    into our own window just because it happens to be focused again --
    quietly skip, no notification spam.

Real captured EXTERNAL windows (WHISP-88 semantics) must be unaffected.
"""
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests._daemon_harness import make_daemon
from whisper_aloud.clipboard import text_insertion as ti
from whisper_aloud.clipboard.window_manager import WindowInfo


def _own_window(window_id=1, backend="gnome-extension"):
    return WindowInfo(
        window_id=window_id, title="WhisperAloud", backend=backend,
        wm_class="org.fede.whisperaloud.Gui",
    )


def _external_window(window_id=2, backend="gnome-extension"):
    return WindowInfo(
        window_id=window_id, title="Terminal", backend=backend,
        wm_class="org.gnome.Terminal",
    )


# ── Capture-time filtering ─────────────────────────────────────────────────


class _NoOpExecutor:
    """Records submissions but never runs them -- isolates the synchronous
    capture decision in StopRecording/_on_recorder_auto_stop from the rest
    of the (threaded, in real life) transcription pipeline, which would
    otherwise race the test's assertion on `_captured_by_job` (the real
    pipeline pops that entry on completion -- see
    `_emit_transcription_success`)."""

    def submit(self, fn, *args, **kwargs):
        return MagicMock()

    def shutdown(self, wait: bool = True) -> None:
        return None


@pytest.fixture
def daemon():
    service = make_daemon()
    service.executor = _NoOpExecutor()
    yield service
    service._patches.close()


class TestStopRecordingNeverCapturesOwnWindow:
    def test_own_window_is_not_captured(self, daemon):
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.window_manager.get_active_window.return_value = _own_window()
        daemon.recorder.stop.return_value = MagicMock()

        daemon.StopRecording()

        assert daemon._captured_by_job == {}

    def test_external_window_is_still_captured(self, daemon):
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.window_manager.get_active_window.return_value = _external_window()
        daemon.recorder.stop.return_value = MagicMock()

        daemon.StopRecording()

        assert len(daemon._captured_by_job) == 1
        captured = next(iter(daemon._captured_by_job.values()))
        assert captured.wm_class == "org.gnome.Terminal"

    def test_no_active_window_captures_nothing(self, daemon):
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.window_manager.get_active_window.return_value = None
        daemon.recorder.stop.return_value = MagicMock()

        daemon.StopRecording()

        assert daemon._captured_by_job == {}


class TestAutoStopNeverCapturesOwnWindow:
    def test_own_window_is_not_captured(self, daemon):
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.window_manager.get_active_window.return_value = _own_window()

        daemon._on_recorder_auto_stop(np.zeros(1600, dtype=np.float32))

        assert daemon._captured_by_job == {}

    def test_external_window_is_still_captured(self, daemon):
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.window_manager.get_active_window.return_value = _external_window()

        daemon._on_recorder_auto_stop(np.zeros(1600, dtype=np.float32))

        assert len(daemon._captured_by_job) == 1


# ── Completion-time: no captured window, own window (or not) focused ──────


def _result(text="hello world"):
    result = MagicMock()
    result.text = text
    result.confidence = 0.9
    result.duration = 1.0
    result.language = "en"
    return result


def _install_clipboard(daemon_obj):
    captured = {}
    clipboard = MagicMock()
    clipboard._session_type = "wayland"

    def _copy(text, on_complete=None):
        captured["on_complete"] = on_complete
        return True

    clipboard.copy.side_effect = _copy
    daemon_obj.clipboard_manager = clipboard
    return captured


@pytest.fixture
def paste_daemon(daemon):
    daemon.config.clipboard.auto_copy = True
    daemon.config.clipboard.auto_paste = True
    daemon.config.clipboard.paste_to_captured_window = True
    daemon.config.clipboard.paste_delay_ms = 0
    daemon.notifications = MagicMock()
    daemon._text_inserter = MagicMock()
    daemon._text_inserter.can_insert = True
    daemon.window_manager = MagicMock()
    return daemon


def _emit(daemon_obj, text="hello world", job="job-1"):
    daemon_obj._emit_transcription_success(
        _result(text), np.zeros(16000, dtype=np.float32), job, "sess-1", 0, token=None
    )


def _transcribe_no_capture(daemon_obj, focused_window):
    """No `_captured_by_job` entry for this job -- exercises the "no
    captured window" auto-paste branch. `focused_window` is whatever
    `window_manager.get_active_window()` reports as currently focused at
    completion time (None, an own window, or an external window)."""
    captured = _install_clipboard(daemon_obj)
    daemon_obj.window_manager.get_active_window.return_value = focused_window
    _emit(daemon_obj)
    captured["on_complete"](True)


class TestNoCapturedWindowCompletionCheck:
    def test_own_window_focused_at_completion_skips_insert(self, paste_daemon, caplog):
        import logging

        with caplog.at_level(logging.INFO):
            _transcribe_no_capture(paste_daemon, _own_window())

        paste_daemon._text_inserter.insert.assert_not_called()
        # Quiet: no notification spam, just an info log.
        paste_daemon.notifications.show_paste_skipped_focus_changed.assert_not_called()
        paste_daemon.notifications.show_clipboard_notice.assert_not_called()
        assert any("skipping auto-paste" in r.message for r in caplog.records)

    def test_external_window_focused_at_completion_still_inserts(self, paste_daemon):
        _transcribe_no_capture(paste_daemon, _external_window())

        paste_daemon._text_inserter.insert.assert_called_once()
        assert paste_daemon._text_inserter.insert.call_args.kwargs["window_id"] is None

    def test_no_focused_window_at_completion_still_inserts(self, paste_daemon):
        """get_active_window() returning None (nothing focused, or the
        window manager couldn't tell) must not block the existing
        "insert into whatever has focus" behavior."""
        _transcribe_no_capture(paste_daemon, None)

        paste_daemon._text_inserter.insert.assert_called_once()

    def test_window_manager_failure_degrades_to_inserting(self, paste_daemon):
        """A broken window manager must fail open (paste as before), never
        silently swallow every completion."""
        captured = _install_clipboard(paste_daemon)
        paste_daemon.window_manager.get_active_window.side_effect = RuntimeError("gone")
        _emit(paste_daemon)
        captured["on_complete"](True)

        paste_daemon._text_inserter.insert.assert_called_once()

    def test_missing_window_manager_still_inserts(self, paste_daemon):
        paste_daemon.window_manager = None
        captured = _install_clipboard(paste_daemon)
        _emit(paste_daemon)
        captured["on_complete"](True)

        paste_daemon._text_inserter.insert.assert_called_once()


class TestCapturedExternalWindowUnaffected:
    """WHISP-88 semantics for a real external captured window must be
    unchanged by this fix."""

    def test_captured_external_window_still_goes_through_confirm_focus(self, paste_daemon):
        info = _external_window()
        paste_daemon._captured_by_job["job-1"] = info
        captured = _install_clipboard(paste_daemon)
        _emit(paste_daemon)
        captured["on_complete"](True)

        paste_daemon.window_manager.activate_window.assert_called_once_with(info)
        paste_daemon._text_inserter.insert.assert_not_called()
        confirm_cb = paste_daemon.window_manager.confirm_focus.call_args[0][1]
        confirm_cb(True)
        assert paste_daemon._text_inserter.insert.call_args.kwargs["window_id"] == info.window_id
