"""GetStatus readiness summary for indicators (WHISP-87, API 1.7.0):
"hotkey_bound" and "autostart", next to the existing "model_state" and
"hotkey_trigger", so a tray/extension can show "all OK" without the window."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.test_daemon import _make_daemon
from whisper_aloud.service.readiness import hotkey_bound


class _Portal:
    def __init__(self, bound):
        self._bound = bound

    def is_bound(self, sid):
        return sid in self._bound


class TestHotkeyBound:
    def test_no_manager(self):
        assert hotkey_bound(None) is False

    def test_backend_none(self):
        assert hotkey_bound(SimpleNamespace(backend="none")) is False

    def test_portal_bound(self):
        mgr = SimpleNamespace(backend="portal", _portal_hotkeys=_Portal({"toggle"}))
        assert hotkey_bound(mgr) is True

    def test_portal_still_waiting_for_consent(self):
        mgr = SimpleNamespace(backend="portal", _portal_hotkeys=_Portal(set()))
        assert hotkey_bound(mgr) is False

    def test_portal_session_gone(self):
        assert hotkey_bound(SimpleNamespace(backend="portal", _portal_hotkeys=None)) is False

    def test_keybinder_registered(self):
        mgr = SimpleNamespace(backend="keybinder", _registered_accels=["<Super><Alt>r"])
        assert hotkey_bound(mgr) is True

    def test_keybinder_registration_failed(self):
        assert hotkey_bound(SimpleNamespace(backend="keybinder", _registered_accels=[])) is False

    def test_public_is_bound_method_wins_when_the_manager_grows_one(self):
        class _Mgr:
            backend = "portal"
            _portal_hotkeys = _Portal({"toggle"})

            def is_bound(self):
                return False

        assert hotkey_bound(_Mgr()) is False

    def test_never_raises(self):
        class _Broken:
            backend = "portal"

            @property
            def _portal_hotkeys(self):
                raise RuntimeError("boom")

        assert hotkey_bound(_Broken()) is False

    def test_mock_auto_attributes_are_not_mistaken_for_bound(self):
        assert hotkey_bound(MagicMock(backend="portal")) is False


@pytest.fixture
def daemon():
    d = _make_daemon()
    d.recorder.state = MagicMock()
    d.recorder.state.value = "idle"
    d._transcribing = False
    return d


def _status(daemon):
    with patch.object(daemon._daemon_module, "GLib") as mock_glib:
        mock_glib.Variant = lambda t, v: v
        return daemon.GetStatus()


def test_status_reports_hotkey_bound(daemon):
    daemon.hotkey_manager = SimpleNamespace(
        backend="portal",
        _portal_hotkeys=_Portal({"toggle"}),
        get_bound_trigger=lambda: "Press <Super><Alt>r",
    )
    status = _status(daemon)
    assert status["hotkey_bound"] is True
    assert status["hotkey_trigger"] == "Press <Super><Alt>r"


def test_status_hotkey_not_bound_without_manager(daemon):
    daemon.hotkey_manager = None
    assert _status(daemon)["hotkey_bound"] is False


def test_status_reports_autostart_from_config(daemon):
    assert _status(daemon)["autostart"] is False
    daemon.config.startup.autostart_background = True
    assert _status(daemon)["autostart"] is True


def test_setconfig_autostart_is_visible_in_status(daemon, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert daemon.SetConfig({"startup.autostart_background": True}) is True
    assert _status(daemon)["autostart"] is True


def test_api_version_is_1_7_1(daemon):
    # 1.7.1 (model gate, fix/model-gate-reprobe): no new surface, but a
    # documented behaviour change -- see docs/dbus-api.md's Changelog.
    assert daemon._daemon_module.API_VERSION == "1.7.1"
