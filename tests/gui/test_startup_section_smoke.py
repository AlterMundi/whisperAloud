"""Headless GTK smoke test for the Settings "Startup" switch (WHISP-87).

Opt-in only, via scripts/test_gui.sh (Xvfb, private bus, temp HOME/XDG);
see tests/gui/test_settings_dialog_smoke.py for why the DISPLAY handling
below is safe. The Background portal is a fake that holds each request
until the test answers it: no real portal, no consent dialog anywhere.
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
    and _GUI_DISPLAY.split(".")[0] not in (":0", ":1")
    and not os.environ.get("WHISPER_ALOUD_TEST_REAL_HOME", os.path.expanduser("~")).startswith("/home/")
)

if _GUI_ENABLED:
    os.environ["DISPLAY"] = _GUI_DISPLAY
    if _GUI_XAUTHORITY:
        os.environ["XAUTHORITY"] = _GUI_XAUTHORITY

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(
        not _GUI_ENABLED,
        reason="GUI smoke tests run only via scripts/test_gui.sh",
    ),
]


class _FakePortal:
    def __init__(self):
        self.requests = []

    def request(self, autostart, reason, on_done, *, previous, parent_window="", commandline=()):
        self.requests.append({"autostart": autostart, "on_done": on_done, "previous": previous})

    def answer(self, response, background, autostart):
        from whisper_aloud.background_portal import interpret_response

        req = self.requests[-1]
        req["on_done"](
            interpret_response(
                req["autostart"],
                response,
                {"background": background, "autostart": autostart},
                previous=req["previous"],
            )
        )

    def close(self):
        pass


def _gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    Adw.init()
    return Gtk


def _dialog(monkeypatch, controller, config=None):
    _gtk()
    from gi.repository import Gtk

    from whisper_aloud.audio.device_manager import AudioDevice
    from whisper_aloud.config import WhisperAloudConfig
    from whisper_aloud.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(
        "whisper_aloud.audio.DeviceManager.list_input_devices",
        staticmethod(
            lambda: [
                AudioDevice(
                    id=0, name="Fake Mic", channels=1, sample_rate=16000.0,
                    is_default=True, hostapi="fake",
                )
            ]
        ),
    )
    fake_client = MagicMock()
    fake_client.get_status.return_value = {"hotkey_backend": "none"}
    monkeypatch.setattr(
        "whisper_aloud.service.client.WhisperAloudClient", lambda *a, **kw: fake_client
    )
    config = config or WhisperAloudConfig()
    return SettingsDialog(
        parent=Gtk.Window(), config=config, autostart_controller=controller
    ), config


def _portal_controller(initial=False):
    from whisper_aloud.ui.startup_logic import AutostartController

    portal = _FakePortal()
    stored = []
    ctl = AutostartController(
        sandboxed=True, initial=initial, persist=stored.append, portal_factory=lambda: portal
    )
    return ctl, portal, stored


def test_switch_reflects_initial_state(monkeypatch):
    ctl, _portal, _ = _portal_controller(initial=True)
    dialog, _ = _dialog(monkeypatch, ctl)
    section = dialog._startup_section
    assert section.switch.get_active() is True
    assert section.switch.get_state() is True
    assert section.switch.get_sensitive() is True


def test_granted_flow(monkeypatch):
    ctl, portal, stored = _portal_controller()
    dialog, _ = _dialog(monkeypatch, ctl)
    switch = dialog._startup_section.switch

    switch.set_active(True)
    assert portal.requests and portal.requests[0]["autostart"] is True
    assert switch.get_sensitive() is False  # waiting for the consent dialog
    assert switch.get_state() is False  # nothing confirmed yet
    assert dialog._startup_section.spinner.get_visible() is True

    portal.answer(0, True, True)
    assert switch.get_active() is True
    assert switch.get_state() is True
    assert switch.get_sensitive() is True
    assert dialog._startup_section.spinner.get_visible() is False
    assert stored == [True]
    assert "login" in dialog._startup_section.status_label.get_text()


def test_denied_flow_puts_the_switch_back(monkeypatch):
    ctl, portal, stored = _portal_controller()
    dialog, _ = _dialog(monkeypatch, ctl)
    section = dialog._startup_section
    section.switch.set_active(True)
    portal.answer(0, False, False)
    assert section.switch.get_active() is False
    assert section.switch.get_state() is False
    assert section.switch.get_sensitive() is True
    assert section.status_label.has_css_class("error")
    assert stored == [False]


def test_toggle_off(monkeypatch):
    ctl, portal, stored = _portal_controller(initial=True)
    dialog, _ = _dialog(monkeypatch, ctl)
    section = dialog._startup_section
    section.switch.set_active(False)
    assert portal.requests[-1]["autostart"] is False
    portal.answer(0, True, False)
    assert section.switch.get_active() is False
    assert stored == [False]


def test_answer_after_dialog_closed_is_still_stored(monkeypatch):
    ctl, portal, stored = _portal_controller()
    dialog, _ = _dialog(monkeypatch, ctl)
    dialog._startup_section.switch.set_active(True)
    dialog._startup_section.detach()
    # Adw.PreferencesDialog (libadwaita >= 1.5) or the window fallback.
    if hasattr(dialog, "force_close"):
        dialog.force_close()
    else:
        dialog.destroy()
    portal.answer(0, True, True)
    assert stored == [True]
    assert ctl.enabled is True


def test_startup_row_is_not_part_of_the_set_config_binding(monkeypatch):
    """The Startup row applies through the controller (portal/autostart
    file), never as a Preferences SetConfig of its own."""
    ctl, portal, _ = _portal_controller()
    dialog, _ = _dialog(monkeypatch, ctl)
    dialog._startup_section.switch.set_active(True)
    portal.answer(0, True, True)
    assert dialog.last_error is None  # no client: a SetConfig would have failed


def test_without_controller_the_switch_is_unavailable(monkeypatch):
    dialog, _ = _dialog(monkeypatch, None)
    section = dialog._startup_section
    assert section.switch.get_sensitive() is False
    assert section.status_label.get_text()


def test_native_switch_writes_the_temp_autostart_entry(monkeypatch, tmp_path):
    from whisper_aloud import autostart
    from whisper_aloud.ui.startup_logic import AutostartController

    adir = tmp_path / "autostart"
    stored = []
    ctl = AutostartController(
        sandboxed=False, initial=False, persist=stored.append,
        native_dir=adir, native_command="/usr/bin/whisper-aloud",
    )
    dialog, _ = _dialog(monkeypatch, ctl)
    switch = dialog._startup_section.switch
    switch.set_active(True)
    assert (adir / autostart.MANAGED_FILENAME).exists()
    assert switch.get_state() is True
    switch.set_active(False)
    assert not (adir / autostart.MANAGED_FILENAME).exists()
    assert switch.get_state() is False
    assert stored == [True, False]


def test_main_window_owns_one_controller_and_persists_without_daemon(monkeypatch):
    """MainWindow wiring: one controller for the GUI's lifetime (portal mode
    when sandboxed), and the confirmed state lands in the config file when
    the daemon cannot take SetConfig (the fake client has no set_config)."""
    from tests.gui.test_main_window_partial_smoke import _make_window
    from whisper_aloud.config import WhisperAloudConfig

    monkeypatch.setattr("whisper_aloud.sandbox.is_flatpak_sandbox", lambda *a, **k: True)
    window, _client = _make_window()
    ctl = window._get_autostart_controller()
    assert ctl is window._get_autostart_controller()
    assert ctl.mode == "portal"
    # Shared disposable HOME across this session: start from whatever is there.
    initial = window.config.startup.autostart_background
    assert ctl.enabled is initial

    window._persist_autostart(not initial)
    assert window.config.startup.autostart_background is (not initial)
    assert WhisperAloudConfig.load().startup.autostart_background is (not initial)

    closed = []
    ctl.close = lambda: closed.append(True)
    window.client = None  # the fake has no disconnect()
    window.cleanup()
    assert closed == [True]
