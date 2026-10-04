"""`whisper-aloud status` shows the API 1.7.0 readiness keys (WHISP-87)."""

from unittest.mock import patch

import pytest

from whisper_aloud.__main__ import main


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))


def _status(capsys, **extra):
    base = {
        "state": "idle", "version": "0.3.0", "model": "base", "device": "cpu",
        "hotkey_backend": "portal", "hotkey_trigger": "Press <Super><Alt>r", "uptime": 5.0,
        "model_state": "ready", "model_error": "",
    }
    base.update(extra)
    with patch("whisper_aloud.__main__.check_service_running", return_value=True), \
         patch("whisper_aloud.__main__.call_service_method", return_value=base), \
         patch("sys.argv", ["whisper-aloud", "status"]):
        assert main() == 0
    return capsys.readouterr().out


def test_bound_hotkey_and_autostart_on(capsys):
    out = _status(capsys, hotkey_bound=True, autostart=True)
    assert "Hotkey:   portal (Press <Super><Alt>r) [bound]" in out
    assert "Login:    starts in background" in out


def test_unbound_hotkey_and_autostart_off(capsys):
    out = _status(capsys, hotkey_bound=False, autostart=False)
    assert "[not bound]" in out
    assert "Login:    not started at login" in out


def test_older_daemon_without_the_keys_prints_as_before(capsys):
    out = _status(capsys)
    assert "Hotkey:   portal (Press <Super><Alt>r)\n" in out
    assert "bound]" not in out
    assert "Login:" not in out
