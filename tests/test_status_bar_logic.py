"""Unit tests for StatusBar.set_status() logic.

Tests the set_status / _clear_status state machine in isolation using a
minimal stub that reproduces the relevant attributes and methods from
StatusBar — no GTK runtime required.
"""

from unittest.mock import MagicMock, patch


class _StatusBarStub:
    """
    Minimal stub that mirrors the set_status/_clear_status logic from
    StatusBar without any GTK dependency.
    """

    def __init__(self, glib):
        self._glib = glib
        self.status_msg_label = MagicMock()
        self._status_clear_id: int = 0

    def set_status(self, text: str, timeout_ms: int = 4000) -> None:
        if self._status_clear_id:
            self._glib.source_remove(self._status_clear_id)
            self._status_clear_id = 0
        self.status_msg_label.set_text(text)
        if text:
            self._status_clear_id = self._glib.timeout_add(
                timeout_ms, self._clear_status
            )

    def _clear_status(self) -> bool:
        self.status_msg_label.set_text("")
        self._status_clear_id = 0
        return False


def _make_stub():
    mock_glib = MagicMock()
    mock_glib.timeout_add.return_value = 42
    return _StatusBarStub(mock_glib), mock_glib


def test_set_status_updates_label():
    """set_status() sets the label text."""
    bar, _ = _make_stub()
    bar.set_status("Recording...")
    bar.status_msg_label.set_text.assert_called_with("Recording...")


def test_set_status_schedules_timeout():
    """set_status() with non-empty text schedules a GLib timeout."""
    bar, mock_glib = _make_stub()
    bar.set_status("Recording...", timeout_ms=3000)
    mock_glib.timeout_add.assert_called_once_with(3000, bar._clear_status)
    assert bar._status_clear_id == 42


def test_set_status_empty_clears_immediately():
    """set_status('') clears the label without scheduling a timeout."""
    bar, mock_glib = _make_stub()
    bar.set_status("")
    bar.status_msg_label.set_text.assert_called_with("")
    mock_glib.timeout_add.assert_not_called()


def test_set_status_cancels_previous_timeout():
    """Calling set_status() a second time cancels the previous timeout."""
    bar, mock_glib = _make_stub()
    bar.set_status("First")
    first_id = bar._status_clear_id  # 42

    mock_glib.timeout_add.return_value = 99
    bar.set_status("Second")

    mock_glib.source_remove.assert_called_once_with(first_id)
    assert bar._status_clear_id == 99


def test_clear_status_resets_state():
    """_clear_status() clears the label, resets id, returns False."""
    bar, _ = _make_stub()
    bar._status_clear_id = 42
    result = bar._clear_status()
    bar.status_msg_label.set_text.assert_called_with("")
    assert bar._status_clear_id == 0
    assert result is False
