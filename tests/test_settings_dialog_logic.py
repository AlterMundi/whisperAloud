"""Logic tests for SettingsDialog close/dirty behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

gi = pytest.importorskip("gi")
if "unittest.mock" in type(gi).__module__:
    pytest.skip("Skipping SettingsDialog logic tests when gi is mocked", allow_module_level=True)
gi.require_version("Gtk", "4.0")

from whisper_aloud.ui.settings_dialog import SettingsDialog


def test_on_close_request_allows_when_flag_set():
    dummy = SimpleNamespace(_allow_close=True)
    assert SettingsDialog._on_close_request(dummy, None) is False


def test_on_close_request_blocks_and_prompts_when_dirty():
    show_discard = MagicMock()
    dummy = SimpleNamespace(
        _allow_close=False,
        _has_unsaved_changes=lambda: True,
        _show_discard_confirmation=show_discard,
    )

    result = SettingsDialog._on_close_request(dummy, None)
    assert result is True
    show_discard.assert_called_once()


def test_on_close_request_allows_when_clean():
    show_discard = MagicMock()
    dummy = SimpleNamespace(
        _allow_close=False,
        _has_unsaved_changes=lambda: False,
        _show_discard_confirmation=show_discard,
    )

    result = SettingsDialog._on_close_request(dummy, None)
    assert result is False
    show_discard.assert_not_called()


def test_mark_dirty_uses_has_unsaved_changes():
    dummy = SimpleNamespace(
        _dirty=False,
        _has_unsaved_changes=lambda: True,
    )
    SettingsDialog._mark_dirty(dummy)
    assert dummy._dirty is True


def test_window_active_changed_ignores_when_child_dialog_open():
    window = MagicMock()
    window.get_property.return_value = False
    window.is_visible.return_value = True
    dummy = SimpleNamespace(_child_dialog_open=True)

    with patch("whisper_aloud.ui.settings_dialog.GLib.idle_add") as idle_add:
        SettingsDialog._on_window_active_changed(dummy, window, None)
        idle_add.assert_not_called()


def test_window_active_changed_closes_when_inactive_and_visible():
    window = MagicMock()
    window.get_property.return_value = False
    window.is_visible.return_value = True
    dummy = SimpleNamespace(_child_dialog_open=False)

    with patch("whisper_aloud.ui.settings_dialog.GLib.idle_add") as idle_add:
        SettingsDialog._on_window_active_changed(dummy, window, None)
        idle_add.assert_called_once_with(window.close)
