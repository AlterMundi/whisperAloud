"""Logic tests for settings dialog behavior without GTK runtime."""

from whisper_aloud.ui.settings_logic import (
    has_unsaved_changes,
    should_auto_close_on_focus_loss,
    should_block_close,
)


def test_has_unsaved_changes_detects_no_diff():
    initial = {"a": "x", "enabled": True}
    current = {"a": "x", "enabled": True}
    assert has_unsaved_changes(initial, current) is False


def test_has_unsaved_changes_detects_diff():
    initial = {"a": "x", "enabled": True}
    current = {"a": "y", "enabled": True}
    assert has_unsaved_changes(initial, current) is True


def test_should_block_close_when_unsaved_and_not_allowed():
    assert should_block_close(allow_close=False, unsaved_changes=True) is True


def test_should_not_block_close_when_allowed():
    assert should_block_close(allow_close=True, unsaved_changes=True) is False


def test_should_not_block_close_when_clean():
    assert should_block_close(allow_close=False, unsaved_changes=False) is False


def test_focus_loss_close_ignored_when_child_dialog_open():
    assert should_auto_close_on_focus_loss(
        child_dialog_open=True,
        is_active=False,
        is_visible=True,
    ) is False


def test_focus_loss_close_when_inactive_and_visible():
    assert should_auto_close_on_focus_loss(
        child_dialog_open=False,
        is_active=False,
        is_visible=True,
    ) is True


def test_focus_loss_no_close_when_not_visible():
    assert should_auto_close_on_focus_loss(
        child_dialog_open=False,
        is_active=False,
        is_visible=False,
    ) is False
