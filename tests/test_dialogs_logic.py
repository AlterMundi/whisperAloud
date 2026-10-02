"""Unit tests for the Adw.AlertDialog / Adw.MessageDialog version-selection helper
(WHISP-78). These only exercise the pure class-selection logic -- never construct
or present an actual dialog -- so they run under plain `pytest -q` with no display.
"""

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw

from whisper_aloud.ui import dialogs


def test_select_dialog_class_uses_alert_dialog_at_minimum_supported_version():
    assert dialogs._select_dialog_class(5) is Adw.AlertDialog


def test_select_dialog_class_uses_alert_dialog_above_minimum_version():
    assert dialogs._select_dialog_class(7) is Adw.AlertDialog


def test_select_dialog_class_falls_back_to_message_dialog_below_minimum(monkeypatch):
    assert dialogs._select_dialog_class(4) is Adw.MessageDialog


def test_select_dialog_class_falls_back_on_old_lts_version():
    """libadwaita 1.4 (e.g. some LTS distros) must not use AlertDialog."""
    assert dialogs._select_dialog_class(4) is Adw.MessageDialog


def test_dialog_class_reads_runtime_adw_minor_version(monkeypatch):
    monkeypatch.setattr(Adw, "get_minor_version", lambda: 5)
    assert dialogs._dialog_class() is Adw.AlertDialog

    monkeypatch.setattr(Adw, "get_minor_version", lambda: 2)
    assert dialogs._dialog_class() is Adw.MessageDialog
