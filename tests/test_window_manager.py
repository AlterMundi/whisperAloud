"""Tests for WindowManager."""
import logging
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


class TestBackendReprobeOnFailure:
    """WHISP-90: the backend detected at __init__ is no longer cached
    forever -- a real operation failure re-probes it."""

    def test_reprobes_and_falls_back_when_gnome_operation_fails(self, caplog):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        assert wm.backend == "gnome-extension"

        wm._gnome_proxy = MagicMock()
        wm._gnome_proxy.call_sync.side_effect = RuntimeError("extension gone")

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"12345\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    result = wm.get_active_window()

        assert result is None
        assert wm.backend == "xdotool"
        assert any(
            "backend changed" in r.message and "gnome-extension -> xdotool" in r.message
            for r in caplog.records
        )

    def test_reprobes_to_none_when_xdotool_binary_disappears(self, caplog):
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
        assert wm.backend == "xdotool"

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    result = wm.get_active_window()

        assert result is None
        assert wm.backend == "none"
        assert any("backend changed" in r.message for r in caplog.records)

    def test_activate_window_reprobes_on_gnome_failure(self, caplog):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        wm._gnome_proxy = MagicMock()
        wm._gnome_proxy.call_sync.side_effect = RuntimeError("extension gone")
        info = WindowInfo(window_id=1, title="x", backend="gnome-extension")

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    result = wm.activate_window(info)

        assert result is False
        assert wm.backend == "none"

    def test_no_reprobe_log_when_backend_unchanged(self, caplog):
        """A failure that re-probes to the SAME backend logs no transition
        (only a debug line) -- avoid noisy INFO spam when nothing changed."""
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
        assert wm.backend == "xdotool"

        call_count = {"n": 0}

        def _flaky_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FileNotFoundError
            return MagicMock(returncode=0, stdout=b"42\n")

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run", side_effect=_flaky_run
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    wm.get_active_window()

        assert wm.backend == "xdotool"
        assert not any("backend changed" in r.message for r in caplog.records)

    def test_no_reprobe_on_plain_no_active_window(self, caplog):
        """A non-zero exit (no active window right now) is not a backend
        failure -- it must not trigger a re-probe."""
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=1),
        ) as mock_run:
            with caplog.at_level(logging.INFO):
                result = wm.get_active_window()

        assert result is None
        assert wm.backend == "xdotool"
        mock_run.assert_called_once()  # no extra _detect_backend() probing


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
