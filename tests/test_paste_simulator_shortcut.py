"""Tests for PasteSimulator shortcut selection."""

from unittest.mock import MagicMock, patch


def _make_config(paste_shortcut="ctrl+v", paste_delay_ms=0, timeout_seconds=5.0):
    cfg = MagicMock()
    cfg.paste_shortcut = paste_shortcut
    cfg.paste_delay_ms = paste_delay_ms
    cfg.timeout_seconds = timeout_seconds
    return cfg


def test_wayland_ctrl_v_keycodes():
    from whisper_aloud.clipboard.paste_simulator import PasteSimulator
    sim = PasteSimulator("wayland", _make_config("ctrl+v"))
    assert sim._ydotool_keys() == ['29:1', '47:1', '47:0', '29:0']


def test_wayland_ctrl_shift_v_keycodes():
    from whisper_aloud.clipboard.paste_simulator import PasteSimulator
    sim = PasteSimulator("wayland", _make_config("ctrl+shift+v"))
    assert sim._ydotool_keys() == ['29:1', '42:1', '47:1', '47:0', '42:0', '29:0']


def test_x11_shortcut_string_ctrl_v():
    from whisper_aloud.clipboard.paste_simulator import PasteSimulator
    sim = PasteSimulator("x11", _make_config("ctrl+v"))
    assert sim._xdotool_shortcut() == "ctrl+v"


def test_x11_shortcut_string_ctrl_shift_v():
    from whisper_aloud.clipboard.paste_simulator import PasteSimulator
    sim = PasteSimulator("x11", _make_config("ctrl+shift+v"))
    assert sim._xdotool_shortcut() == "ctrl+shift+v"


def test_wayland_paste_uses_shift_keycodes():
    from whisper_aloud.clipboard.paste_simulator import PasteSimulator
    sim = PasteSimulator("wayland", _make_config("ctrl+shift+v"))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        sim._paste_wayland()
        call_args = mock_run.call_args[0][0]
        assert '42:1' in call_args
        assert '42:0' in call_args


def test_x11_paste_uses_configured_shortcut():
    from whisper_aloud.clipboard.paste_simulator import PasteSimulator
    sim = PasteSimulator("x11", _make_config("ctrl+shift+v"))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        sim._paste_x11()
        call_args = mock_run.call_args[0][0]
        assert 'ctrl+shift+v' in call_args
