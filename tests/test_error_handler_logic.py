"""Unit tests for ErrorDialog's response-list mapping (WHISP-80).

ErrorDialog.show_error used to build a Gtk.MessageDialog (deprecated) with a
variable number of buttons (an optional recovery action plus an always-present
Close). ``_build_responses`` extracts that button-set decision into a pure
function -- no GTK widget construction, no dialog presentation -- so it runs
under plain `pytest -q` with no display, same as test_dialogs_logic.py's
_select_dialog_class tests.
"""

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from whisper_aloud.ui.error_handler import ErrorDialog


def test_build_responses_without_recovery_action_is_close_only():
    responses = ErrorDialog._build_responses(None)

    assert responses == [("close", "Close", Adw.ResponseAppearance.DEFAULT)]


def test_build_responses_with_recovery_action_puts_recovery_first():
    recovery_action = ("Open Settings", lambda: None)

    responses = ErrorDialog._build_responses(recovery_action)

    assert [r[0] for r in responses] == ["recovery", "close"]
    assert responses[0] == ("recovery", "Open Settings", Adw.ResponseAppearance.SUGGESTED)
    assert responses[1] == ("close", "Close", Adw.ResponseAppearance.DEFAULT)


def test_build_responses_uses_recovery_action_label():
    recovery_action = ("Retry", lambda: None)

    responses = ErrorDialog._build_responses(recovery_action)

    assert responses[0][1] == "Retry"


def test_build_responses_close_is_always_last_and_default_appearance():
    for recovery_action in (None, ("Retry", lambda: None)):
        responses = ErrorDialog._build_responses(recovery_action)
        assert responses[-1] == ("close", "Close", Adw.ResponseAppearance.DEFAULT)
