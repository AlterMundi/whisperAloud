"""Tests for auto-paste behavior in daemon transcription flow.

This is a hand-rolled mirror of `_emit_transcription_success`'s
clipboard-copy/auto-paste decision logic in
src/whisper_aloud/service/daemon.py, kept for a quick, dependency-free
sanity check of the auto_copy/auto_paste/empty-text gating conditions.

It intentionally mirrors the real *post-fix* shape: the paste only
fires from `copy()`'s `on_complete` callback (the real "clipboard
selection owned" event), never merely because `copy()` was called --
see tests/test_clipboard.py::TestWaylandCopyOrdering and
tests/test_daemon_clipboard_ordering.py for the real-code coverage of
that race fix (0.3.0 Flatpak/GNOME-Wayland field bug: auto-paste used
to land the previous clipboard content because it fired before wl-copy
had actually taken ownership of the selection).
"""

from unittest.mock import MagicMock


def _make_paste_config(auto_copy=True, auto_paste=True, paste_delay_ms=0):
    cfg = MagicMock()
    cfg.clipboard.auto_copy = auto_copy
    cfg.clipboard.auto_paste = auto_paste
    cfg.clipboard.paste_delay_ms = paste_delay_ms
    return cfg


def _run_emit_logic(config, clipboard_manager, result_text, simulator_factory):
    """Mirrors the logic in daemon._emit_transcription_success(): copy()
    is given an on_complete callback, and only that callback's success
    value gates the paste -- never the synchronous return of copy()
    itself."""
    if not (config.clipboard.auto_copy and clipboard_manager):
        return

    def _on_complete(success):
        if not (success
                and config.clipboard.auto_paste
                and clipboard_manager
                and result_text):
            return
        sim = simulator_factory(clipboard_manager._session_type, config.clipboard)
        sim.simulate_paste()

    clipboard_manager.copy(result_text, on_complete=_on_complete)


def test_auto_paste_called_after_copy_completes():
    mock_simulator = MagicMock()
    mock_simulator.simulate_paste.return_value = True
    mock_clipboard_mgr = MagicMock()
    mock_clipboard_mgr._session_type = "wayland"

    captured = {}

    def _copy(text, on_complete=None):
        captured["on_complete"] = on_complete
        return True

    mock_clipboard_mgr.copy.side_effect = _copy

    _run_emit_logic(_make_paste_config(True, True), mock_clipboard_mgr,
                     "hello world", lambda st, c: mock_simulator)

    mock_clipboard_mgr.copy.assert_called_once()
    # The paste must not fire until on_complete actually reports success.
    mock_simulator.simulate_paste.assert_not_called()
    captured["on_complete"](True)
    mock_simulator.simulate_paste.assert_called_once()


def test_auto_paste_skipped_when_completion_reports_failure():
    mock_simulator = MagicMock()
    mock_clipboard_mgr = MagicMock()
    mock_clipboard_mgr._session_type = "wayland"

    captured = {}

    def _copy(text, on_complete=None):
        captured["on_complete"] = on_complete
        return True

    mock_clipboard_mgr.copy.side_effect = _copy

    _run_emit_logic(_make_paste_config(True, True), mock_clipboard_mgr,
                     "hello world", lambda st, c: mock_simulator)

    captured["on_complete"](False)
    mock_simulator.simulate_paste.assert_not_called()


def test_auto_paste_skipped_when_auto_copy_false():
    mock_simulator = MagicMock()
    mock_clipboard_mgr = MagicMock()

    _run_emit_logic(_make_paste_config(False, True), mock_clipboard_mgr,
                     "text", lambda st, c: mock_simulator)

    mock_clipboard_mgr.copy.assert_not_called()
    mock_simulator.simulate_paste.assert_not_called()


def test_auto_paste_skipped_for_empty_text():
    mock_simulator = MagicMock()
    mock_clipboard_mgr = MagicMock()
    mock_clipboard_mgr._session_type = "wayland"

    captured = {}

    def _copy(text, on_complete=None):
        captured["on_complete"] = on_complete
        return True

    mock_clipboard_mgr.copy.side_effect = _copy

    _run_emit_logic(_make_paste_config(True, True), mock_clipboard_mgr,
                     "", lambda st, c: mock_simulator)

    captured["on_complete"](True)
    mock_simulator.simulate_paste.assert_not_called()


def test_auto_paste_skipped_when_auto_paste_false():
    mock_simulator = MagicMock()
    mock_clipboard_mgr = MagicMock()
    mock_clipboard_mgr._session_type = "x11"

    captured = {}

    def _copy(text, on_complete=None):
        captured["on_complete"] = on_complete
        return True

    mock_clipboard_mgr.copy.side_effect = _copy

    _run_emit_logic(_make_paste_config(True, False), mock_clipboard_mgr,
                     "some text", lambda st, c: mock_simulator)

    mock_clipboard_mgr.copy.assert_called_once()
    captured["on_complete"](True)
    mock_simulator.simulate_paste.assert_not_called()
