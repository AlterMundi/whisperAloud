"""Logic tests for MainWindow behavior without GTK runtime."""

from whisper_aloud.ui.main_window_logic import (
    is_daemon_interaction_ready,
    resolve_language_change,
    should_enter_transcribing,
    should_restore_transcribing_after_cancel,
)


def test_is_daemon_interaction_ready_requires_all_flags():
    assert is_daemon_interaction_ready(
        client_present=True,
        client_connected=True,
        daemon_available=True,
    ) is True
    assert is_daemon_interaction_ready(
        client_present=False,
        client_connected=True,
        daemon_available=True,
    ) is False
    assert is_daemon_interaction_ready(
        client_present=True,
        client_connected=False,
        daemon_available=True,
    ) is False
    assert is_daemon_interaction_ready(
        client_present=True,
        client_connected=True,
        daemon_available=False,
    ) is False


def test_resolve_language_change_rejects_invalid_index():
    codes = ["auto", "en", "es"]
    assert resolve_language_change(
        selected_idx=-1,
        language_codes=codes,
        current_language="en",
    ) is None
    assert resolve_language_change(
        selected_idx=3,
        language_codes=codes,
        current_language="en",
    ) is None


def test_resolve_language_change_rejects_same_language():
    codes = ["auto", "en", "es"]
    assert resolve_language_change(
        selected_idx=1,
        language_codes=codes,
        current_language="en",
    ) is None


def test_resolve_language_change_accepts_transition():
    codes = ["auto", "en", "es"]
    decision = resolve_language_change(
        selected_idx=2,
        language_codes=codes,
        current_language="en",
    )
    assert decision == ("es", "en")


def test_resolve_language_change_uses_auto_for_none_current():
    codes = ["auto", "en", "es"]
    decision = resolve_language_change(
        selected_idx=1,
        language_codes=codes,
        current_language=None,
    )
    assert decision == ("en", "auto")


def test_should_enter_transcribing():
    assert should_enter_transcribing("transcribing") is True
    assert should_enter_transcribing("") is False
    assert should_enter_transcribing("error") is False


def test_should_restore_transcribing_after_cancel():
    assert should_restore_transcribing_after_cancel(False) is True
    assert should_restore_transcribing_after_cancel(True) is False
