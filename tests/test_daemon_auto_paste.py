"""Tests for auto-paste behavior in daemon transcription flow."""

import threading
from unittest.mock import MagicMock


def _make_paste_config(auto_copy=True, auto_paste=True, paste_delay_ms=0):
    cfg = MagicMock()
    cfg.clipboard.auto_copy = auto_copy
    cfg.clipboard.auto_paste = auto_paste
    cfg.clipboard.paste_delay_ms = paste_delay_ms
    return cfg


def _run_emit_logic(config, clipboard_manager, result_text, simulator_factory):
    """Mirrors the logic we're adding to daemon._emit_success()."""
    if config.clipboard.auto_copy and clipboard_manager:
        clipboard_manager.copy(result_text)
    if (config.clipboard.auto_copy
            and config.clipboard.auto_paste
            and clipboard_manager
            and result_text):
        sim = simulator_factory(clipboard_manager._session_type, config.clipboard)
        t = threading.Thread(target=sim.simulate_paste, daemon=True)
        t.start()
        t.join(timeout=2)


def test_auto_paste_called_after_copy():
    mock_simulator = MagicMock()
    mock_simulator.simulate_paste.return_value = True
    mock_clipboard_mgr = MagicMock()
    mock_clipboard_mgr._session_type = "wayland"
    mock_clipboard_mgr.copy.return_value = True

    _run_emit_logic(_make_paste_config(True, True), mock_clipboard_mgr,
                    "hello world", lambda st, c: mock_simulator)

    mock_clipboard_mgr.copy.assert_called_once_with("hello world")
    mock_simulator.simulate_paste.assert_called_once()


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

    _run_emit_logic(_make_paste_config(True, True), mock_clipboard_mgr,
                    "", lambda st, c: mock_simulator)

    mock_simulator.simulate_paste.assert_not_called()


def test_auto_paste_skipped_when_auto_paste_false():
    mock_simulator = MagicMock()
    mock_clipboard_mgr = MagicMock()
    mock_clipboard_mgr._session_type = "x11"
    mock_clipboard_mgr.copy.return_value = True

    _run_emit_logic(_make_paste_config(True, False), mock_clipboard_mgr,
                    "some text", lambda st, c: mock_simulator)

    mock_clipboard_mgr.copy.assert_called_once()
    mock_simulator.simulate_paste.assert_not_called()
