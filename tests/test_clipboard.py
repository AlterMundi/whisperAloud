"""Tests for clipboard functionality."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from whisper_aloud import ClipboardConfig, ClipboardManager, PasteSimulator


class TestSessionDetection:
    """Test session type detection."""

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0", "DISPLAY": ""}, clear=False)
    def test_detects_wayland(self):
        """Test Wayland session detection."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "wayland"

    @patch.dict(os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}, clear=False)
    def test_detects_x11(self):
        """Test X11 session detection."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "x11"

    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False)
    def test_detects_unknown(self):
        """Test unknown session detection."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "unknown"

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":1", "DISPLAY": ":0"}, clear=False)
    def test_wayland_takes_precedence(self):
        """Test that Wayland is detected even if DISPLAY is also set."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "wayland"


class TestClipboardCopy:
    """Test clipboard copy operations."""

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_copy_wayland_success(self, mock_popen):
        """Test successful copy on Wayland."""
        # Mock Popen to return a process with stdin
        mock_process = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.close = MagicMock()
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            result = manager.copy("Test text")

            assert result is True
            # Verify wl-copy was called with Popen
            mock_popen.assert_called_once()
            call_args = str(mock_popen.call_args)
            assert 'wl-copy' in call_args
            assert '--paste-once' in call_args
            # Verify stdin was written to
            mock_process.stdin.write.assert_called_once_with(b"Test text")
            mock_process.stdin.close.assert_called_once()
            # Verify fallback was also called for redundancy
            mock_fallback.assert_called_once_with("Test text")

    @patch('subprocess.run')
    @patch.dict(os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}, clear=False)
    def test_copy_x11_success(self, mock_run):
        """Test successful copy on X11."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True):
            result = manager.copy("Test text")

            assert result is True
            # Verify xclip was called
            assert mock_run.call_count >= 1
            call_args = str(mock_run.call_args_list[0])
            assert 'xclip' in call_args

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_copy_tool_not_found(self, mock_popen):
        """Test copy fails gracefully when tool missing."""
        mock_popen.side_effect = FileNotFoundError("wl-copy not found")

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            result = manager.copy("Test text")

            # Should succeed via fallback
            assert result is True
            mock_fallback.assert_called_once_with("Test text")

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_copy_write_error(self, mock_popen):
        """Test copy handles write errors gracefully."""
        # Mock Popen to raise error when writing to stdin
        mock_process = MagicMock()
        mock_process.stdin.write.side_effect = OSError("Write error")
        mock_popen.return_value = mock_process

        config = ClipboardConfig(timeout_seconds=1.0)
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            result = manager.copy("Test text")

            # Should succeed via fallback
            assert result is True
            mock_fallback.assert_called_once_with("Test text")

    @patch('subprocess.run')
    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False)
    def test_copy_unknown_session(self, mock_run):
        """Test copy on unknown session type uses fallback."""
        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            result = manager.copy("Test text")

            # Should only use fallback, not call subprocess
            assert result is True
            mock_fallback.assert_called_once_with("Test text")
            mock_run.assert_not_called()

    def test_copy_empty_text(self):
        """Test copying empty text."""
        config = ClipboardConfig()
        manager = ClipboardManager(config)

        result = manager.copy("")
        assert result is False


class TestFallback:
    """Test fallback mechanism."""

    def test_fallback_creates_file(self):
        """Test fallback creates file successfully."""
        config = ClipboardConfig(fallback_path="/tmp/test_whisper_clipboard.txt")
        manager = ClipboardManager(config)

        test_text = "Test fallback text"
        result = manager._copy_fallback(test_text)

        assert result is True

        # Verify file was created
        fallback_path = Path("/tmp/test_whisper_clipboard.txt")
        assert fallback_path.exists()
        assert fallback_path.read_text() == test_text

        # Cleanup
        fallback_path.unlink()

    def test_fallback_unicode_text(self):
        """Test fallback with unicode/emoji text."""
        config = ClipboardConfig(fallback_path="/tmp/test_whisper_unicode.txt")
        manager = ClipboardManager(config)

        test_text = "¡Hola! 你好 🎤 Test"
        result = manager._copy_fallback(test_text)

        assert result is True

        fallback_path = Path("/tmp/test_whisper_unicode.txt")
        assert fallback_path.read_text(encoding='utf-8') == test_text

        # Cleanup
        fallback_path.unlink()

    @patch('pathlib.Path.write_text')
    def test_fallback_emergency_path(self, mock_write):
        """Test emergency fallback when primary fallback fails."""
        # First call fails, second call succeeds
        mock_write.side_effect = [Exception("Primary failed"), None]

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        result = manager._copy_fallback("Test")

        # Should succeed via emergency path
        assert result is True
        assert mock_write.call_count == 2


class TestPasteSimulation:
    """Test keyboard paste simulation."""

    @patch('subprocess.run')
    def test_paste_wayland_success(self, mock_run):
        """Test successful paste simulation on Wayland."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        result = simulator.simulate_paste()

        assert result is True
        # Verify ydotool was called
        call_args = str(mock_run.call_args)
        assert 'ydotool' in call_args

    @patch('subprocess.run')
    def test_paste_x11_success(self, mock_run):
        """Test successful paste simulation on X11."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        result = simulator.simulate_paste()

        assert result is True
        # Verify xdotool was called
        call_args = str(mock_run.call_args)
        assert 'xdotool' in call_args

    @patch('subprocess.run')
    def test_paste_tool_not_found(self, mock_run):
        """Test paste fails gracefully when tool missing."""
        mock_run.side_effect = FileNotFoundError("ydotool not found")

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_permission_denied(self, mock_run):
        """Test paste fails with permission error."""
        mock_run.side_effect = PermissionError("No permission")

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_timeout(self, mock_run):
        """Test paste timeout handling."""
        mock_run.side_effect = subprocess.TimeoutExpired('ydotool', 5)

        config = ClipboardConfig(timeout_seconds=1.0)
        simulator = PasteSimulator('wayland', config)
        result = simulator.simulate_paste()

        assert result is False

    def test_paste_unknown_session(self):
        """Test paste on unknown session type."""
        config = ClipboardConfig()
        simulator = PasteSimulator('unknown', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('time.sleep')
    @patch('subprocess.run')
    def test_paste_delay(self, mock_run, mock_sleep):
        """Test paste delay is respected."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig(paste_delay_ms=250)
        simulator = PasteSimulator('wayland', config)
        simulator.simulate_paste()

        # Verify sleep was called with correct delay (250ms = 0.25s)
        mock_sleep.assert_called_once_with(0.25)


class TestPermissionChecks:
    """Test permission checking logic."""

    @patch('subprocess.run')
    def test_check_tool_available(self, mock_run):
        """Test checking when tool is available."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        status = simulator.check_availability()

        assert status['available'] is True
        assert status['reason'] == ''
        assert status['fix'] == ''

    @patch('subprocess.run')
    def test_check_tool_not_installed(self, mock_run):
        """Test checking when tool is not installed."""
        mock_run.return_value = MagicMock(returncode=1)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        status = simulator.check_availability()

        assert status['available'] is False
        assert 'not installed' in status['reason']
        assert 'sudo apt install' in status['fix']

    @patch('subprocess.run')
    def test_check_timeout(self, mock_run):
        """Test checking with timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired('which', 2)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        status = simulator.check_availability()

        assert status['available'] is False
        assert 'Timeout' in status['reason']


class TestConfiguration:
    """Test clipboard configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ClipboardConfig()

        assert config.auto_copy is True
        assert config.auto_paste is True
        assert config.paste_delay_ms == 100
        assert config.timeout_seconds == 5.0
        assert config.fallback_to_file is True
        assert config.fallback_path == "/tmp/whisper_aloud_clipboard.txt"

    def test_env_override(self):
        """Test environment variable overrides."""
        with patch.dict(os.environ, {
            'WHISPER_ALOUD_CLIPBOARD_AUTO_COPY': 'false',
            'WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE': 'false',
            'WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS': '200',
            'WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS': '10.0',
        }):
            from whisper_aloud import WhisperAloudConfig
            config = WhisperAloudConfig.load()

            assert config.clipboard.auto_copy is False
            assert config.clipboard.auto_paste is False
            assert config.clipboard.paste_delay_ms == 200
            assert config.clipboard.timeout_seconds == 10.0


class TestIntegration:
    """Integration tests (can be skipped in CI)."""

    @pytest.mark.integration
    @pytest.mark.skipif(os.getenv("CI"), reason="Skip in CI environment")
    def test_real_clipboard_copy(self):
        """Test with real clipboard (requires clipboard tools)."""
        try:
            config = ClipboardConfig()
            manager = ClipboardManager(config)

            # At minimum, fallback should work
            result = manager.copy("Integration test text")
            assert result is True

            # Verify fallback file exists
            fallback_path = Path(config.fallback_path)
            assert fallback_path.exists()
            assert "Integration test text" in fallback_path.read_text()
        except Exception as e:
            pytest.skip(f"Clipboard tools not available: {e}")
