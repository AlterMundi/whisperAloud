"""Headless GTK smoke test for SettingsDialog (WHISP-42 audit item #7).

settings_dialog.py has zero automated coverage anywhere else in this suite (only its
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


def _make_dialog(config, monkeypatch, fake_devices=None):
    """Construct a real SettingsDialog against a disposable config, with
    DeviceManager/WhisperAloudClient faked out (no real audio hardware, no real
    daemon/D-Bus call) per the audit's requirement."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    Adw.init()

    from whisper_aloud.audio.device_manager import AudioDevice
    from whisper_aloud.ui.settings_dialog import SettingsDialog

    if fake_devices is None:
        fake_devices = [
            AudioDevice(
                id=0, name="Fake Mic", channels=1, sample_rate=16000.0,
                is_default=True, hostapi="fake",
            ),
        ]
    monkeypatch.setattr(
        "whisper_aloud.audio.DeviceManager.list_input_devices",
        staticmethod(lambda: fake_devices),
    )

    fake_client = MagicMock()
    fake_client.get_status.return_value = {"hotkey_backend": "none"}
    monkeypatch.setattr(
        "whisper_aloud.service.client.WhisperAloudClient",
        lambda *a, **kw: fake_client,
    )

    parent = Gtk.Window()
    dialog = SettingsDialog(parent=parent, config=config)
    return dialog


@pytest.fixture
def config():
    from whisper_aloud.config import WhisperAloudConfig

    return WhisperAloudConfig()


def test_dialog_constructs_without_crashing(config, monkeypatch):
    """Regression test for audit BLOCKER #1: SettingsDialog() used to raise
    AttributeError on every construction (notice widgets read before creation)."""
    dialog = _make_dialog(config, monkeypatch)
    # The default config must resolve to a real, selected Model-row entry — never
    # left unselected.
    from gi.repository import Gtk

    assert dialog.model_row.get_selected() != Gtk.INVALID_LIST_POSITION
    assert dialog._model_entries, "Model row must be populated on construction"

    # Exercise the hotkey status button too (uses the faked WhisperAloudClient),
    # since the audit asked for fakes of both DeviceManager and WhisperAloudClient.
    dialog._on_register_hotkey_clicked(None)
    assert dialog._hotkey_status_label.get_text()  # didn't crash, got some status text


def test_model_and_engine_sync_both_directions(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)

    # Advanced -> Engine: switch to Parakeet, Model row must show Parakeet entries.
    parakeet_idx = dialog._engine_keys.index("parakeet")
    dialog.engine_row.set_selected(parakeet_idx)
    assert dialog._model_entries
    assert all(e.engine == "parakeet" for e in dialog._model_entries)
    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert selected.engine == "parakeet"

    # Model row -> Advanced->Engine (reverse sync): switch Advanced->Engine back to
    # faster-whisper (repopulating the Model row with faster-whisper entries), then
    # pick one explicitly and confirm Engine stays in sync.
    dialog.engine_row.set_selected(dialog._engine_keys.index("faster-whisper"))
    assert dialog._model_entries
    assert all(e.engine == "faster-whisper" for e in dialog._model_entries)
    dialog.model_row.set_selected(0)
    assert dialog._engine_keys[dialog.engine_row.get_selected()] == "faster-whisper"


def test_language_fallback_when_engine_has_no_entries_for_language(config, monkeypatch):
    """Reproduces the WHISP-42 audit HIGH #2 bug: language="ja" + Advanced->Parakeet
    must show Parakeet's entries (not silently keep faster-whisper) and surface the
    fallback notice."""
    config.transcription.language = "ja"
    dialog = _make_dialog(config, monkeypatch)
    assert dialog._language_fallback_notice.get_visible() is False

    dialog.engine_row.set_selected(dialog._engine_keys.index("parakeet"))

    assert dialog._model_entries, "must not end up with an empty Model row"
    assert all(e.engine == "parakeet" for e in dialog._model_entries), (
        "must show Parakeet's own entries, not silently keep the previous "
        "engine's (faster-whisper) list"
    )
    assert dialog._language_fallback_notice.get_visible() is True
    assert "ja" in dialog._language_fallback_notice.get_text()


def test_selecting_from_full_catalog_updates_model_and_engine_rows(config, monkeypatch):
    from whisper_aloud.stt import catalog

    dialog = _make_dialog(config, monkeypatch)

    parakeet_entry = catalog.get("parakeet", "parakeet-tdt-0.6b-v3")

    class _FakeWindow:
        def close(self):
            pass

    dialog._on_catalog_entry_chosen(None, parakeet_entry, _FakeWindow())

    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert selected.name == "parakeet-tdt-0.6b-v3"
    assert dialog._engine_keys[dialog.engine_row.get_selected()] == "parakeet"


def test_needs_review_entry_from_catalog_does_not_crash_engine_sync(config, monkeypatch):
    """Audit MEDIUM #3: a needs_review entry (e.g. WHISP-56's nemotron-streaming,
    engine not in catalog.ENGINES) chosen from the full-catalog view must not raise
    when _on_model_row_changed tries to sync Advanced->Engine."""
    from whisper_aloud.stt import catalog

    dialog = _make_dialog(config, monkeypatch)
    needs_review_entries = [e for e in catalog.entries() if e.needs_review]
    if not needs_review_entries:
        pytest.skip("no needs_review catalog entry present on this branch")
    entry = needs_review_entries[0]
    assert entry.engine not in dialog._engine_keys  # precondition for the guard

    class _FakeWindow:
        def close(self):
            pass

    # Must not raise even though entry.engine has no Advanced->Engine row option.
    dialog._on_catalog_entry_chosen(None, entry, _FakeWindow())
    selected = dialog._model_entries[dialog.model_row.get_selected()]
    assert selected.name == entry.name


def test_save_mapping_writes_engine_name_device_and_compute_type(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    monkeypatch.setattr(dialog._config, "save", lambda: None)

    dialog.engine_row.set_selected(dialog._engine_keys.index("parakeet"))
    dialog.device_row.set_selected(0)  # "Auto"
    dialog.compute_type_row.set_selected(1)  # "float16"

    dialog._on_save_clicked(None)

    assert dialog._config.model.engine == "parakeet"
    assert dialog._config.model.name == "parakeet-tdt-0.6b-v3"
    assert dialog._config.model.device == "auto"
    assert dialog._config.model.compute_type == "float16"


def test_dirty_tracking_flags_a_changed_compute_type(config, monkeypatch):
    dialog = _make_dialog(config, monkeypatch)
    assert dialog._has_unsaved_changes() is False

    current = dialog.compute_type_row.get_selected()
    other = (current + 1) % dialog.compute_type_row.get_model().get_n_items()
    dialog.compute_type_row.set_selected(other)

    assert dialog._has_unsaved_changes() is True
