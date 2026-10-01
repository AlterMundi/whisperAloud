"""Tests for WindowManager."""
from unittest.mock import patch, MagicMock
from whisper_aloud.clipboard.window_manager import WindowManager, WindowInfo


class TestWindowInfo:
    def test_dataclass_fields(self):
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        assert info.window_id == 12345
        assert info.title == "Terminal"
        assert info.backend == "xdotool"


class TestWindowManagerBackendDetection:
    def test_fallback_to_none_when_nothing_available(self):
        with patch("whisper_aloud.clipboard.window_manager.subprocess.run",
                   side_effect=FileNotFoundError):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
                assert wm.backend == "none"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_xdotool_probes_getactivewindow(self, mock_run):
        """xdotool detection runs actual getactivewindow, not just 'which'."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b"12345\n")
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
            assert wm.backend == "xdotool"
            mock_run.assert_called_once_with(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )


class TestXdotoolBackend:
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_get_active_window(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"99\n"),  # _check_xdotool during __init__
            MagicMock(returncode=0, stdout=b"12345\n"),  # getactivewindow
            MagicMock(returncode=0, stdout=b"My Terminal\n"),  # getwindowname
        ]
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
        info = wm.get_active_window()
        assert info is not None
        assert info.window_id == 12345
        assert info.title == "My Terminal"
        assert info.backend == "xdotool"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_activate_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        wm = WindowManager()
        wm.backend = "xdotool"
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        assert wm.activate_window(info) is True

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_get_active_window_fails_gracefully(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        wm = WindowManager()
        wm.backend = "xdotool"
        assert wm.get_active_window() is None

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_activate_window_fails_gracefully(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
        wm = WindowManager()
        wm.backend = "xdotool"
        info = WindowInfo(window_id=99999, title="Gone", backend="xdotool")
        assert wm.activate_window(info) is False


class TestNoneBackend:
    def test_get_active_window_returns_none(self):
        with patch.object(WindowManager, "_detect_backend", return_value="none"):
            wm = WindowManager()
        assert wm.get_active_window() is None

    def test_activate_window_returns_false(self):
        with patch.object(WindowManager, "_detect_backend", return_value="none"):
            wm = WindowManager()
        info = WindowInfo(window_id=1, title="x", backend="none")
        assert wm.activate_window(info) is False
