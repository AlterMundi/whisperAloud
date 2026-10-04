"""Regression tests for the daemon's clipboard-copy -> auto-paste ordering.

Field bug (0.3.0 Flatpak, GNOME Wayland, real desktop): auto-paste pasted
the PREVIOUS clipboard content, not the new transcription; a manual
Ctrl+V right after pasted the new transcription correctly. Root cause:
`ClipboardManager._copy_wayland` used to report success (and the daemon
then triggered a paste) the instant wl-copy's stdin was closed, racing
wl-copy's fork into the background -- the real event that means the
Wayland selection is owned.

The fix threads a real completion signal through
`ClipboardManager.copy(text, on_complete=...)` (see
tests/test_clipboard.py::TestWaylandCopyOrdering) and the daemon's
`_emit_transcription_success` now drives the "Transcription copied to
clipboard" log and any auto-paste entirely from that callback instead of
from the synchronous `copy()` call. These tests pin that ordering down at
the daemon level with a fully mocked `clipboard_manager` standing in for
the real (threaded) wl-copy wait, asserting:

(a) paste is NOT invoked before the copy's on_complete fires
(b) paste IS invoked after a successful on_complete
(c) a failed on_complete (non-zero exit / timeout) skips the paste,
    still writes/keeps the fallback-backed copy, and logs a warning
(e) the paste itself is triggered via GLib.idle_add/timeout_add (the
    main-loop path), never from a bare threading.Thread
"""
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests._daemon_harness import make_daemon


def _result(text="hello world"):
    result = MagicMock()
    result.text = text
    result.confidence = 0.9
    result.duration = 1.0
    result.language = "en"
    return result


@pytest.fixture
def daemon():
    service = make_daemon()
    yield service
    service._patches.close()


def _install_controllable_clipboard(daemon_obj, session_type="wayland"):
    """Replace `daemon.clipboard_manager` with a mock whose `.copy()`
    captures the `on_complete` callback instead of invoking it, so the
    test can fire it at a chosen moment -- standing in for the real
    background-thread wait for wl-copy's exit."""
    captured = {}
    clipboard = MagicMock()
    clipboard._session_type = session_type

    def _copy(text, on_complete=None):
        captured["text"] = text
        captured["on_complete"] = on_complete
        return True

    clipboard.copy.side_effect = _copy
    daemon_obj.clipboard_manager = clipboard
    return captured


class TestPasteWaitsForCopyCompletion:
    def test_paste_not_invoked_before_on_complete_fires(self, daemon, caplog):
        """(a) The paste must not fire merely because copy() was called --
        only once its on_complete callback reports success."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon._test_mode = False
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )

            daemon.clipboard_manager.copy.assert_called_once()
            assert callable(captured.get("on_complete"))
            sim.simulate_paste.assert_not_called()

    def test_paste_invoked_after_successful_completion(self, daemon):
        """(b) Once on_complete(True) fires, the paste must be triggered."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon._test_mode = False
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )
            sim.simulate_paste.assert_not_called()

            captured["on_complete"](True)

            sim.simulate_paste.assert_called_once()

    def test_on_complete_may_fire_from_a_background_thread(self, daemon):
        """The real wl-copy wait happens off the main loop/caller thread;
        the daemon must handle on_complete arriving from any thread and
        still trigger the paste correctly."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon._test_mode = False
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )

            done = threading.Event()

            def _fire():
                captured["on_complete"](True)
                done.set()

            t = threading.Thread(target=_fire, daemon=True)
            t.start()
            assert done.wait(timeout=5)
            t.join(timeout=5)

            sim.simulate_paste.assert_called_once()

    def test_failed_completion_skips_paste_and_warns(self, daemon, caplog):
        """(c) A failed copy (non-zero exit / timeout, reported as
        on_complete(False)) must never trigger a paste, and must log a
        warning instead of the success message."""
        import logging

        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon._test_mode = False
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )

            with caplog.at_level(logging.WARNING):
                captured["on_complete"](False)

            sim.simulate_paste.assert_not_called()
            assert any(
                "skipping auto-paste" in rec.message.lower()
                or "clipboard copy did not complete" in rec.message.lower()
                for rec in caplog.records
            )

    def test_successful_completion_logs_copied_message(self, daemon, caplog):
        """(item 5) The 'Transcription copied to clipboard' log must only
        reflect the REAL completion, not the dispatch."""
        import logging

        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = False
        daemon._test_mode = False
        captured = _install_controllable_clipboard(daemon)

        daemon._emit_transcription_success(
            _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
        )

        with caplog.at_level(logging.INFO):
            assert not any(
                "transcription copied to clipboard" in rec.message.lower()
                for rec in caplog.records
            )
            captured["on_complete"](True)

        assert any(
            "transcription copied to clipboard" in rec.message.lower()
            for rec in caplog.records
        )


class TestPasteRunsOnMainLoopNotAThread:
    def test_simulate_paste_scheduled_via_idle_add_not_a_thread(self, daemon):
        """(e) simulate_paste() must be invoked from the GLib main-loop
        path (idle_add/timeout_add), never from a bare threading.Thread --
        it mutates portal/session state that is otherwise only ever
        touched from main-loop callbacks."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon._test_mode = False
        captured = _install_controllable_clipboard(daemon)

        idle_add_calls = []
        real_idle_add = daemon._mock_glib.idle_add

        def _spy_idle_add(fn, *args):
            idle_add_calls.append(fn)
            return real_idle_add(fn, *args)

        daemon._mock_glib.idle_add = _spy_idle_add

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim, \
                patch("threading.Thread") as mock_thread_cls:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )
            captured["on_complete"](True)

            sim.simulate_paste.assert_called_once()
            # No new background thread was spun up to perform the paste.
            mock_thread_cls.assert_not_called()
            # And it really did go through GLib.idle_add at least once
            # (the _finish() + _trigger_paste() hops).
            assert len(idle_add_calls) >= 1

    def test_captured_window_paste_uses_timeout_add_settle_delay(self, daemon):
        """When a captured window must be refocused first, focus must be
        CONFIRMED (WHISP-88) before the paste is scheduled via
        GLib.timeout_add(paste_delay_ms, ...) as an optional settle delay --
        never a time.sleep in a thread, and never a substitute for either
        the on_complete success signal or the focus confirmation itself."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.config.clipboard.paste_delay_ms = 123
        daemon._test_mode = False
        daemon.window_manager = MagicMock()
        captured_window = MagicMock()
        daemon._captured_by_job["job-1"] = captured_window
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim, \
                patch("time.sleep") as mock_sleep:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )
            captured["on_complete"](True)

            daemon.window_manager.activate_window.assert_called_once_with(captured_window)
            daemon.window_manager.confirm_focus.assert_called_once()
            confirm_args = daemon.window_manager.confirm_focus.call_args[0]
            assert confirm_args[0] is captured_window
            confirm_callback = confirm_args[1]
            mock_sleep.assert_not_called()
            # The paste must not be scheduled until confirm_focus's own
            # callback reports success -- not merely because confirm_focus
            # was called.
            daemon._mock_glib.timeout_add.assert_not_called()
            sim.simulate_paste.assert_not_called()

            confirm_callback(True)

            daemon._mock_glib.timeout_add.assert_called_once()
            delay_arg = daemon._mock_glib.timeout_add.call_args[0][0]
            assert delay_arg == 123
            sim.simulate_paste.assert_not_called()  # timeout_add is mocked; fn not fired yet

            # Manually fire the queued single-shot timeout callback, as
            # the real GLib main loop would after the delay elapses.
            fire = daemon._mock_glib.timeout_add.call_args[0][1]
            fire()

            sim.simulate_paste.assert_called_once()

    def test_captured_window_zero_settle_delay_uses_idle_add(self, daemon):
        """paste_delay_ms == 0 after a confirmed focus must still schedule
        the paste on the main loop (idle_add), not fire it inline."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon.config.clipboard.paste_to_captured_window = True
        daemon.config.clipboard.paste_delay_ms = 0
        daemon._test_mode = False
        daemon.window_manager = MagicMock()
        captured_window = MagicMock()
        daemon._captured_by_job["job-1"] = captured_window
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )
            captured["on_complete"](True)

            confirm_callback = daemon.window_manager.confirm_focus.call_args[0][1]
            daemon._mock_glib.timeout_add.reset_mock()
            confirm_callback(True)

            daemon._mock_glib.timeout_add.assert_not_called()
            sim.simulate_paste.assert_called_once()


class TestFocusNotConfirmedSkipsPaste:
    """WHISP-88: when confirm_focus reports False -- mismatch or no signal
    -- the daemon must NOT paste, and must notify the user that the text
    is still on the clipboard but the paste was skipped."""

    def test_confirm_focus_false_skips_paste_and_notifies(self, daemon, caplog):
        import logging

        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon.config.clipboard.paste_to_captured_window = True
        daemon._test_mode = False
        daemon.window_manager = MagicMock()
        daemon.notifications = MagicMock()
        captured_window = MagicMock()
        daemon._captured_by_job["job-1"] = captured_window
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )
            captured["on_complete"](True)

            confirm_callback = daemon.window_manager.confirm_focus.call_args[0][1]
            with caplog.at_level(logging.WARNING):
                confirm_callback(False)

            sim.simulate_paste.assert_not_called()
            daemon._mock_glib.timeout_add.assert_not_called()
            daemon.notifications.show_paste_skipped_focus_changed.assert_called_once()
            assert any("WHISP-88" in r.message for r in caplog.records)

    def test_confirm_focus_raising_skips_paste_and_notifies(self, daemon):
        """A window_manager that errors out confirming focus degrades the
        same way as an explicit False -- never falls back to pasting."""
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon.config.clipboard.paste_to_captured_window = True
        daemon._test_mode = False
        daemon.window_manager = MagicMock()
        daemon.window_manager.confirm_focus.side_effect = RuntimeError("boom")
        daemon.notifications = MagicMock()
        captured_window = MagicMock()
        daemon._captured_by_job["job-1"] = captured_window
        captured = _install_controllable_clipboard(daemon)

        with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim:
            sim = MagicMock()
            build_sim.return_value = sim

            daemon._emit_transcription_success(
                _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
            )
            captured["on_complete"](True)

            sim.simulate_paste.assert_not_called()
            daemon._mock_glib.timeout_add.assert_not_called()
            daemon.notifications.show_paste_skipped_focus_changed.assert_called_once()


class TestCapturedWindowStillPoppedUnconditionally(object):
    """Audit fix [LOW 4] must still hold under the new async flow: the
    captured-window entry is popped synchronously, before copy() is even
    dispatched, so it can never leak regardless of how/when on_complete
    fires (or doesn't)."""

    def test_pop_happens_before_on_complete_fires(self, daemon):
        daemon.config.clipboard.auto_copy = True
        daemon.config.clipboard.auto_paste = True
        daemon._test_mode = False
        daemon._captured_by_job["job-1"] = MagicMock()
        _install_controllable_clipboard(daemon)

        daemon._emit_transcription_success(
            _result(), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
        )

        assert "job-1" not in daemon._captured_by_job
