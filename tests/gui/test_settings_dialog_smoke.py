"""Headless GTK smoke tests for the Preferences dialog (HIG-07; WHISP-42 audit item #7).

Covers the dialog's structure (Adw.PreferencesDialog, pages, Adwaita rows, no
Save/Cancel) and instant apply against a fake client: every row change is a
SetConfig, a rejection puts the row back, and ConfigChanged updates rows.

settings_dialog.py had zero automated coverage anywhere else in this suite (only its
pure *_logic.py helpers are unit-tested) — which is exactly how the WHISP-42 audit's
BLOCKER #1 (AttributeError on every dialog open) shipped unnoticed. This module
builds the real dialog under a live Xvfb display so that class of bug fails CI
instead of a user's desktop.

Opt-in only, via scripts/test_gui.sh — never run directly with a plain `pytest`.

Why these tests need a real DISPLAY, and why that doesn't weaken the safety guard:
The root conftest.py (commit 4c877c5) unconditionally strips DISPLAY/WAYLAND_DISPLAY
from the test process for every pytest run from this rootdir, specifically so no test
can ever reach a real desktop. We do not touch or bypass that guard. Instead,
scripts/test_gui.sh captures the Xvfb DISPLAY xvfb-run sets, under the name
WHISPER_ALOUD_GUI_DISPLAY (which conftest.py never looks at), *before* conftest.py
runs and pops the real DISPLAY. Only this module — and only when the wrapper has set
WHISPER_ALOUD_GUI_TEST=1 — restores os.environ["DISPLAY"] from that preserved value,
right here at import time, before any GTK call that would need it. A plain `pytest`/
`pytest -q` run never sets WHISPER_ALOUD_GUI_TEST, so it is completely unaffected and
still gets conftest.py's fully display-less environment.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

_GUI_DISPLAY = os.environ.get("WHISPER_ALOUD_GUI_DISPLAY", "")
_GUI_XAUTHORITY = os.environ.get("WHISPER_ALOUD_GUI_XAUTHORITY", "")
_GUI_ENABLED = (
    os.environ.get("WHISPER_ALOUD_GUI_TEST") == "1"
    and "WAYLAND_DISPLAY" not in os.environ
    and _GUI_DISPLAY.startswith(":")
    # Never the user's own X/Xwayland displays, and only inside the wrapper's
    # disposable HOME: a hand-set WHISPER_ALOUD_GUI_TEST must not put windows
    # on the real screen.
    and _GUI_DISPLAY.split(".")[0] not in (":0", ":1")
    and not os.environ.get("WHISPER_ALOUD_TEST_REAL_HOME", os.path.expanduser("~")).startswith("/home/")
)

if _GUI_ENABLED:
    # See module docstring: restore only what scripts/test_gui.sh preserved, only here.
    # XAUTHORITY is needed too — conftest.py pops it as part of the same desktop-
    # isolation guard, but xvfb-run's Xvfb instance requires its Xauthority cookie to
    # authenticate new connections (symptom without this: Gtk.init_check() succeeds
    # but gtk_icon_theme_get_for_display/Gdk.Display.get_default() still fail).
    os.environ["DISPLAY"] = _GUI_DISPLAY
    if _GUI_XAUTHORITY:
        os.environ["XAUTHORITY"] = _GUI_XAUTHORITY

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(
        not _GUI_ENABLED,
        reason=(
            "GUI smoke tests require WHISPER_ALOUD_GUI_TEST=1, no WAYLAND_DISPLAY, and "
            "an Xvfb DISPLAY preserved as WHISPER_ALOUD_GUI_DISPLAY. Run via "
            "scripts/test_gui.sh — never directly."
        ),
    ),
]


class FakeClient:
    """Stands in for WhisperAloudClient: records SetConfig calls (already
    encoded for D-Bus, e.g. the -1 microphone sentinel) and serves GetConfig."""

    def __init__(self, accept=True, config=None):
        self.is_connected = True
        self.accept = accept
        self.sent = []
        self.config = dict(config or {})

    def set_config(self, changes):
        self.sent.append(dict(changes))
        return self.accept

    def get_config(self):
        return dict(self.config)

    def get_status(self):
        return {"hotkey_backend": "none"}


def _gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    Adw.init()
    return Gtk


def _make_dialog(config, monkeypatch, fake_devices=None, client=None):
    """A real Preferences dialog against a disposable config, with
    DeviceManager and the download client faked out (no audio hardware, no
    real daemon/D-Bus call)."""
    gtk = _gtk()

    from whisper_aloud.audio.device_manager import AudioDevice
    from whisper_aloud.ui.settings_dialog import SettingsDialog

    if fake_devices is None:
        fake_devices = [
            AudioDevice(
                id=0, name="Fake Mic", channels=1, sample_rate=16000.0,
                is_default=True, hostapi="fake",
            ),
            AudioDevice(
                id=7, name="Second Mic", channels=1, sample_rate=16000.0,
                is_default=False, hostapi="fake",
            ),
        ]
    monkeypatch.setattr(
        "whisper_aloud.audio.DeviceManager.list_input_devices",
        staticmethod(lambda: fake_devices),
    )

    download_client = MagicMock()
    monkeypatch.setattr(
        "whisper_aloud.service.client.WhisperAloudClient",
        lambda *a, **kw: download_client,
    )

    if client is None:
        client = FakeClient()
    parent = gtk.Window()
    dialog = SettingsDialog(parent=parent, config=config, client=client)
    return dialog


@pytest.fixture
def config():
    from whisper_aloud.config import WhisperAloudConfig

    return WhisperAloudConfig()


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


# ─── structure ─────────────────────────────────────────────────────────────


def test_dialog_is_an_adw_preferences_dialog_with_search(config, monkeypatch):
    from gi.repository import Adw

    from whisper_aloud.ui import settings_dialog

    dialog = _make_dialog(config, monkeypatch)
    if settings_dialog.USE_PREFERENCES_DIALOG:
        assert isinstance(dialog, Adw.PreferencesDialog)
    else:
        assert isinstance(dialog, Adw.PreferencesWindow)
    assert dialog.get_title() == "Preferences"
    assert dialog.get_search_enabled() is True


def test_pages_are_general_audio_history_with_icons(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    pages = [dialog.general_page, dialog.audio_page, dialog.history_page]
    assert [p.get_title() for p in pages] == ["General", "Audio", "History"]
    assert all(p.get_icon_name() for p in pages)


def test_no_save_cancel_or_saved_alert_machinery(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    labels = {
        w.get_label() for w in _walk(dialog) if hasattr(w, "get_label") and w.get_label()
    }
    assert "Save" not in labels
    for gone in ("_on_save_clicked", "_on_cancel_clicked", "_show_discard_confirmation",
                 "_has_unsaved_changes", "_show_message"):
        assert not hasattr(dialog, gone)


def test_general_page_hosts_startup_and_top_bar_groups(config, monkeypatch):
    from gi.repository import Adw

    dialog = _make_dialog(config, monkeypatch)
    startup = dialog._startup_section.widget
    assert isinstance(startup, Adw.PreferencesGroup)
    assert startup.get_ancestor(Adw.PreferencesPage) is dialog.general_page
    shell = dialog._shell_extension_section
    assert shell is not None
    assert shell.widget.get_ancestor(Adw.PreferencesPage) is dialog.general_page


def test_rows_use_adwaita_row_types(config, monkeypatch):
    from gi.repository import Adw

    dialog = _make_dialog(config, monkeypatch)
    assert isinstance(dialog.auto_copy_row, Adw.SwitchRow)
    assert isinstance(dialog.auto_paste_row, Adw.ExpanderRow)
    assert dialog.auto_paste_row.get_show_enable_switch() is True
    assert isinstance(dialog.paste_backend_row, Adw.ComboRow)
    assert isinstance(dialog.paste_delay_row, Adw.SpinRow)
    assert isinstance(dialog.notifications_row, Adw.ExpanderRow)
    assert isinstance(dialog.microphone_row, Adw.ComboRow)
    assert isinstance(dialog.hotkey_accel_row, Adw.EntryRow)
    assert isinstance(dialog.cleanup_days_row, Adw.SpinRow)


def test_rows_start_from_the_daemon_config(config, monkeypatch):
    client = FakeClient(config={
        "clipboard.auto_copy": False,
        "clipboard.paste_backend": "clipboard",
        "clipboard.paste_delay_ms": 300,
        "audio.device_id": 7,
    })
    dialog = _make_dialog(config, monkeypatch, client=client)
    assert dialog.auto_copy_row.get_active() is False
    model = dialog.paste_backend_row.get_model()
    assert model.get_string(dialog.paste_backend_row.get_selected()) == "Clipboard Only"
    assert dialog.paste_delay_row.get_value() == 300
    assert dialog.microphone_row.get_selected() == 2  # System Default, Fake Mic, Second Mic
    assert client.sent == []  # building the dialog sends nothing


# ─── instant apply ─────────────────────────────────────────────────────────


def test_switch_applies_immediately(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.auto_copy_row.set_active(False)
    assert client.sent == [{"clipboard.auto_copy": False}]


def test_expander_enable_switch_applies(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.auto_paste_row.set_enable_expansion(False)
    dialog.notifications_row.set_enable_expansion(False)
    assert client.sent == [{"clipboard.auto_paste": False}, {"notifications.enabled": False}]


def test_notification_type_switch_applies(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.notification_rows["notifications.error"].set_active(False)
    assert client.sent == [{"notifications.error": False}]


def test_terminal_shortcut_switch_writes_paste_shortcut(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    assert dialog.terminal_paste_row.get_active() is True  # default ctrl+shift+v
    dialog.terminal_paste_row.set_active(False)
    assert client.sent == [{"clipboard.paste_shortcut": "ctrl+v"}]


def test_paste_backend_combo_applies_every_value(config, monkeypatch):
    from whisper_aloud.config import PASTE_BACKENDS

    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    assert "screen-sharing" in dialog.paste_backend_row.get_subtitle()
    for index, value in enumerate(PASTE_BACKENDS):
        dialog.paste_backend_row.set_selected(index)
        if value != "auto":
            assert client.sent[-1] == {"clipboard.paste_backend": value}
    assert {"clipboard.paste_backend": "remote-desktop"} in client.sent


def test_spin_row_applies_an_int(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.paste_delay_row.set_value(250)
    assert client.sent == [{"clipboard.paste_delay_ms": 250}]
    assert isinstance(client.sent[0]["clipboard.paste_delay_ms"], int)


def test_spin_row_applies_a_float(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.denoising_strength_row.set_value(0.75)
    assert client.sent == [{"audio_processing.denoising_strength": 0.75}]


def test_microphone_system_default_is_sent_as_the_sentinel(config, monkeypatch):
    client = FakeClient(config={"audio.device_id": 7})
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.microphone_row.set_selected(0)
    assert client.sent == [{"audio.device_id": -1}]
    dialog.microphone_row.set_selected(2)
    assert client.sent[-1] == {"audio.device_id": 7}


def test_history_page_rows_apply(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.edit_history_row.set_active(False)
    dialog.cleanup_days_row.set_value(30)
    assert client.sent == [
        {"persistence.edit_history_enabled": False},
        {"persistence.auto_cleanup_days": 30},
    ]


def test_entry_row_applies_on_apply_signal(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    row = dialog.hotkey_accel_row
    row.set_text("<Super><Alt>d")
    assert client.sent == []  # typing alone sends nothing
    row.emit("apply")
    assert client.sent == [{"hotkey.toggle_recording": "<Super><Alt>d"}]
    assert not row.has_css_class("error")


def test_entry_row_invalid_value_is_inline_error_and_not_sent(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    row = dialog.hotkey_accel_row
    row.set_text("<Bogus>@@")
    row.emit("apply")
    assert client.sent == []
    assert row.has_css_class("error")
    assert row.get_text() == "<Bogus>@@"  # the user's text is kept
    assert dialog.last_error


def test_rejected_change_reverts_row_and_keeps_other_settings(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.auto_copy_row.set_active(False)  # accepted
    client.accept = False
    dialog.notifications_row.set_enable_expansion(False)  # rejected
    assert dialog.notifications_row.get_enable_expansion() is True
    assert dialog.auto_copy_row.get_active() is False
    assert dialog.binding.get("clipboard.auto_copy") is False
    assert dialog.last_error


def test_no_client_refuses_with_a_toast(config, monkeypatch):
    from whisper_aloud.ui.settings_dialog import SettingsDialog

    _make_dialog(config, monkeypatch)  # installs the device fakes
    gtk = _gtk()
    dialog = SettingsDialog(parent=gtk.Window(), config=config, client=None)
    dialog.auto_copy_row.set_active(False)
    assert dialog.auto_copy_row.get_active() is True
    assert "not connected" in dialog.last_error


# ─── live updates (ConfigChanged) ──────────────────────────────────────────


def test_config_changed_updates_rows_without_sending(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.config_changed({
        "clipboard.auto_copy": False,
        "clipboard.paste_backend": "remote-desktop",
        "clipboard.paste_delay_ms": 900,
        "hotkey.toggle_recording": "<Ctrl><Alt>h",
        "audio.device_id": 7,
        "notifications.enabled": False,
    })
    assert dialog.auto_copy_row.get_active() is False
    model = dialog.paste_backend_row.get_model()
    assert model.get_string(dialog.paste_backend_row.get_selected()) == "Remote Desktop Portal"
    assert dialog.paste_delay_row.get_value() == 900
    assert dialog.hotkey_accel_row.get_text() == "<Ctrl><Alt>h"
    assert dialog.microphone_row.get_selected() == 2
    assert dialog.notifications_row.get_enable_expansion() is False
    assert client.sent == []


def test_config_changed_language_refreshes_language_and_model_rows(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.config_changed({"transcription.language": "es"})
    model = dialog.language_row.get_model()
    assert model.get_string(dialog.language_row.get_selected()) == "Español"
    assert client.sent == []


def test_reload_config_refetches_everything(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    client.config = {"clipboard.auto_paste": False, "persistence.max_entries": 500}
    dialog.config_changed({})
    assert dialog.auto_paste_row.get_enable_expansion() is False
    assert dialog.max_entries_row.get_value() == 500
    assert client.sent == []


# ─── speech model rows (instant apply) ─────────────────────────────────────


def test_dialog_constructs_with_a_selected_model(config, monkeypatch):
    """Regression for audit BLOCKER #1 (notice widgets read before creation)."""
    from gi.repository import Gtk

    dialog = _make_dialog(config, monkeypatch)
    assert dialog.model_row.get_selected() != Gtk.INVALID_LIST_POSITION
    assert dialog._model_entries
    dialog._on_register_hotkey_clicked(None)
    assert dialog._hotkey_status_label.get_text()


def test_engine_change_applies_engine_and_model(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.engine_row.set_selected(dialog._engine_keys.index("parakeet"))
    assert dialog._model_entries
    assert all(e.engine == "parakeet" for e in dialog._model_entries)
    assert client.sent == [{"model.engine": "parakeet", "model.name": "parakeet-tdt-0.6b-v3"}]


def test_model_row_change_applies_and_syncs_engine(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    current = dialog.model_row.get_selected()
    other = next(i for i, e in enumerate(dialog._model_entries) if i != current)
    entry = dialog._model_entries[other]
    dialog.model_row.set_selected(other)
    assert client.sent[-1]["model.name"] == entry.name
    assert dialog._engine_keys[dialog.engine_row.get_selected()] == entry.engine


def test_device_and_compute_type_apply(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.device_row.set_selected(1)  # CPU
    dialog.compute_type_row.set_selected(1)  # float16
    assert client.sent == [{"model.device": "cpu"}, {"model.compute_type": "float16"}]


def test_language_row_applies(config, monkeypatch):
    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    from whisper_aloud.ui.settings_logic import LANGUAGE_OPTIONS, option_index

    dialog.language_row.set_selected(option_index(LANGUAGE_OPTIONS, "de"))
    assert client.sent == [{"transcription.language": "de"}]


def test_rejected_model_change_puts_rows_back(config, monkeypatch):
    client = FakeClient(accept=False)
    dialog = _make_dialog(config, monkeypatch, client=client)
    dialog.engine_row.set_selected(dialog._engine_keys.index("parakeet"))
    assert dialog._engine_keys[dialog.engine_row.get_selected()] == "faster-whisper"
    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert (selected.engine, selected.name) == ("faster-whisper", "base")


def test_language_fallback_when_engine_has_no_entries_for_language(config, monkeypatch):
    """WHISP-42 audit HIGH #2: language="ja" + Advanced->Parakeet shows
    Parakeet's entries and the fallback notice."""
    config.transcription.language = "ja"
    dialog = _make_dialog(config, monkeypatch)
    assert dialog._language_fallback_notice.get_visible() is False

    dialog.engine_row.set_selected(dialog._engine_keys.index("parakeet"))

    assert dialog._model_entries
    assert all(e.engine == "parakeet" for e in dialog._model_entries)
    assert dialog._language_fallback_notice.get_visible() is True
    assert "ja" in dialog._language_fallback_notice.get_text()


def test_configured_model_hidden_by_language_filter_is_still_shown(config, monkeypatch):
    config.transcription.language = "ja"
    config.model.engine = "parakeet"
    config.model.name = "parakeet-tdt-0.6b-v3"
    dialog = _make_dialog(config, monkeypatch)
    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert (selected.engine, selected.name) == ("parakeet", "parakeet-tdt-0.6b-v3")


def test_selecting_from_full_catalog_applies_model_and_engine(config, monkeypatch):
    from whisper_aloud.stt import catalog

    client = FakeClient()
    dialog = _make_dialog(config, monkeypatch, client=client)
    parakeet_entry = catalog.get("parakeet", "parakeet-tdt-0.6b-v3")

    class _FakeWindow:
        def close(self):
            pass

    dialog._on_catalog_entry_chosen(None, parakeet_entry, _FakeWindow())

    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert selected.name == "parakeet-tdt-0.6b-v3"
    assert dialog._engine_keys[dialog.engine_row.get_selected()] == "parakeet"
    assert client.sent == [{"model.engine": "parakeet", "model.name": "parakeet-tdt-0.6b-v3"}]


def test_needs_review_entry_from_catalog_does_not_crash_engine_sync(config, monkeypatch):
    """Audit MEDIUM #3: an entry whose engine has no Engine-row option."""
    from whisper_aloud.stt import catalog

    dialog = _make_dialog(config, monkeypatch)
    needs_review_entries = [e for e in catalog.entries() if e.needs_review]
    if not needs_review_entries:
        pytest.skip("no needs_review catalog entry present on this branch")
    entry = needs_review_entries[0]
    assert entry.engine not in dialog._engine_keys

    class _FakeWindow:
        def close(self):
            pass

    dialog._on_catalog_entry_chosen(None, entry, _FakeWindow())
    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert selected.name == entry.name


# ─── WHISP-68: in-app model download row ───────────────────────────────────


def _not_downloaded(monkeypatch):
    from whisper_aloud.ui import model_picker_logic

    monkeypatch.setattr(
        "whisper_aloud.ui.settings_dialog.download_state_for",
        lambda entry: model_picker_logic.DownloadState(downloaded=False, detection="unknown"),
    )


def _fake_download_client(dialog, monkeypatch):
    fake_client = MagicMock()
    fake_client.download_model.return_value = True
    monkeypatch.setattr(dialog, "_get_download_client", lambda: fake_client)
    return fake_client


def test_download_button_visible_for_not_downloaded_model(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    _not_downloaded(monkeypatch)
    dialog._update_download_row()

    assert dialog._download_row_box.get_visible() is True
    assert dialog._download_button.get_visible() is True
    assert dialog._download_progress.get_visible() is False
    assert dialog._download_cancel_button.get_visible() is False
    assert dialog._download_error_label.get_visible() is False


def test_download_button_click_starts_download_and_shows_progress(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    _not_downloaded(monkeypatch)
    fake_client = _fake_download_client(dialog, monkeypatch)

    dialog._update_download_row()
    dialog._on_download_clicked(None)

    entry = dialog._selected_entry()
    fake_client.download_model.assert_called_once()
    assert dialog._downloading is True
    assert dialog._download_button.get_visible() is False
    assert dialog._download_progress.get_visible() is True
    assert dialog._download_cancel_button.get_visible() is True

    dialog._on_download_progress_signal(entry.engine, entry.name, 50, 100)
    assert dialog._download_progress_state == (50, 100)
    dialog._on_download_progress_signal("other-engine", "other-name", 1, 2)
    assert dialog._download_progress_state == (50, 100)


def test_download_cancel_button_calls_client_cancel_download(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    _not_downloaded(monkeypatch)
    fake_client = _fake_download_client(dialog, monkeypatch)

    dialog._on_download_clicked(None)
    dialog._on_download_cancel_clicked(None)

    fake_client.cancel_download.assert_called_once()


def test_download_finished_failure_shows_error_and_refreshes_row(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    _not_downloaded(monkeypatch)
    _fake_download_client(dialog, monkeypatch)

    dialog._on_download_clicked(None)
    entry = dialog._download_active_entry
    dialog._on_download_finished_signal(entry[0], entry[1], False, "network exploded")

    assert dialog._downloading is False
    assert dialog._download_error_label.get_visible() is True
    assert "network exploded" in dialog._download_error_label.get_text()
    assert dialog._download_button.get_visible() is True


def test_download_finished_success_hides_row_once_downloaded(config, monkeypatch):
    from whisper_aloud.ui import model_picker_logic

    dialog = _make_dialog(config, monkeypatch)
    _not_downloaded(monkeypatch)
    _fake_download_client(dialog, monkeypatch)

    dialog._on_download_clicked(None)
    entry = dialog._download_active_entry
    monkeypatch.setattr(
        "whisper_aloud.ui.settings_dialog.download_state_for",
        lambda e: model_picker_logic.DownloadState(downloaded=True, detection="unknown"),
    )
    dialog._on_download_finished_signal(entry[0], entry[1], True, "")

    assert dialog._downloading is False
    assert dialog._download_row_box.get_visible() is False
    assert dialog._download_error_label.get_visible() is False


# ─── WHISP-68 audit BLOCKER #1: explicit license consent dialog ────────────


def test_download_click_on_nonstandard_license_opens_consent_dialog(config, monkeypatch):
    from whisper_aloud.stt import catalog

    dialog = _make_dialog(config, monkeypatch)
    fake_client = _fake_download_client(dialog, monkeypatch)
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    shown = {}
    monkeypatch.setattr(dialog, "_show_license_consent_dialog", lambda e: shown.update(entry=e))
    monkeypatch.setattr(dialog, "_selected_entry", lambda: entry)

    dialog._on_download_clicked(None)

    assert shown["entry"] is entry
    fake_client.download_model.assert_not_called()


def test_license_consent_cancel_sends_nothing_accept_sends_true(config, monkeypatch):
    from whisper_aloud.stt import catalog

    dialog = _make_dialog(config, monkeypatch)
    fake_client = _fake_download_client(dialog, monkeypatch)
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")

    dialog._on_license_consent_response(None, "cancel", entry)
    fake_client.download_model.assert_not_called()

    dialog._on_license_consent_response(None, "accept", entry)
    fake_client.download_model.assert_called_once_with(entry.engine, entry.name, True)
    assert dialog._downloading is True


def test_license_consent_dialog_presents_over_the_preferences(config, monkeypatch):
    """Real construction: the consent AlertDialog is presented with the
    Preferences dialog as its parent, without raising."""
    from whisper_aloud.stt import catalog

    gtk = _gtk()
    dialog = _make_dialog(config, monkeypatch)
    parent = gtk.Window()
    parent.present()
    dialog.present_for(parent)
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    dialog._show_license_consent_dialog(entry)
    dialog.close()


# ─── MainWindow wiring ─────────────────────────────────────────────────────


def _pump():
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def test_main_window_opens_preferences_and_forwards_config_changed(monkeypatch):
    from tests.gui.test_main_window_partial_smoke import _make_window

    # Only the device list is faked: faking WhisperAloudClient here would let
    # MainWindow's own pending connect attempt "succeed" with the fake.
    monkeypatch.setattr(
        "whisper_aloud.audio.DeviceManager.list_input_devices", staticmethod(lambda: [])
    )
    window, client = _make_window()
    client.set_config = lambda changes: True
    window._on_settings_clicked(None)
    dialog = window._preferences_dialog
    assert dialog is not None

    # Same dialog on a second Ctrl+, (no duplicate windows).
    window._on_settings_clicked(None)
    assert window._preferences_dialog is dialog

    # Another client changes settings: the main window's config, its
    # language selector and the open dialog all follow.
    client._config_changed_cb({"transcription.language": "es", "clipboard.auto_copy": False})
    _pump()
    assert window.config.transcription.language == "es"
    assert window._language_codes[window.lang_dropdown.get_selected()] == "es"
    assert dialog.auto_copy_row.get_active() is False

    dialog.close()
    _pump()
    assert window._preferences_dialog is None
