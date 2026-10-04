"""Tests for clipboard functionality."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        with patch.dict(
            os.environ,
            {
                "DISPLAY": "",
                "WAYLAND_DISPLAY": "",
                "XDG_SESSION_TYPE": "",
                "XDG_RUNTIME_DIR": "",
            },
            clear=False,
        ):
            session_type = ClipboardManager.detect_session_type()
        assert session_type == "unknown"

    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":1", "DISPLAY": ":0"}, clear=False)
    def test_wayland_takes_precedence(self):
        """Test that Wayland is detected even if DISPLAY is also set."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "wayland"

    @patch.dict(
        os.environ,
        {"DISPLAY": "", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "wayland"},
        clear=False,
    )
    def test_detects_wayland_from_xdg_session_type(self):
        """Detect Wayland from XDG_SESSION_TYPE when WAYLAND_DISPLAY is missing."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "wayland"

    @patch.dict(
        os.environ,
        {"DISPLAY": "", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"},
        clear=False,
    )
    def test_detects_x11_from_xdg_session_type(self):
        """Detect X11 from XDG_SESSION_TYPE when DISPLAY is missing."""
        session_type = ClipboardManager.detect_session_type()
        assert session_type == "x11"

    def test_detects_wayland_from_runtime_socket(self, tmp_path):
        """Detect Wayland from runtime socket when env vars are absent."""
        (tmp_path / "wayland-1").touch()
        with patch.dict(
            os.environ,
            {
                "DISPLAY": "",
                "WAYLAND_DISPLAY": "",
                "XDG_SESSION_TYPE": "",
                "XDG_RUNTIME_DIR": str(tmp_path),
            },
            clear=False,
        ):
            session_type = ClipboardManager.detect_session_type()
        assert session_type == "wayland"


class TestClipboardCopy:
    """Test clipboard copy operations."""

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_copy_wayland_success(self, mock_popen):
        """Test successful copy on Wayland: waits for wl-copy's exit (the
        event that means the selection is actually owned) before reporting
        success, and never passes --paste-once (WHISP field bug: a
        clipboard manager or any reader consuming the one-shot selection
        left later pastes with stale content)."""
        # Mock Popen to return a process with stdin that exits 0 (success).
        mock_process = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.close = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            result = manager.copy("Test text")

            assert result is True
            # Verify wl-copy was called with Popen, without --paste-once.
            mock_popen.assert_called_once()
            call_args = str(mock_popen.call_args)
            assert 'wl-copy' in call_args
            assert '--paste-once' not in call_args
            # Verify stdin was written to
            mock_process.stdin.write.assert_called_once_with(b"Test text")
            mock_process.stdin.close.assert_called_once()
            # The fix: we must wait for the process to actually exit before
            # reporting success -- that exit is the event meaning the
            # selection was set.
            mock_process.wait.assert_called_once_with(timeout=config.timeout_seconds)
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

    def test_copy_empty_text_notifies_completion_false(self):
        """on_complete must still fire (with False) for empty text."""
        config = ClipboardConfig()
        manager = ClipboardManager(config)

        on_complete = MagicMock()
        result = manager.copy("", on_complete=on_complete)

        assert result is False
        on_complete.assert_called_once_with(False)


class TestWaylandCopyOrdering:
    """Field bug (0.3.0 Flatpak, GNOME Wayland): auto-paste pasted the
    PREVIOUS clipboard content because the daemon reported "copied" and
    triggered the paste the instant wl-copy's stdin was closed, racing
    wl-copy's fork into the background (the event that actually means the
    selection is owned). These tests pin down the fix: _copy_wayland must
    wait for that process exit -- off the calling thread when a completion
    callback is supplied -- before reporting success, and no --paste-once."""

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_on_complete_dispatches_to_background_thread(self, mock_popen):
        """With on_complete given, copy() must return immediately (not
        block the caller/main-loop thread) while the wait for wl-copy's
        exit happens on a background thread."""
        import threading as _threading

        release_wait = _threading.Event()
        entered_wait = _threading.Event()

        mock_process = MagicMock()

        def _blocking_wait(timeout=None):
            entered_wait.set()
            release_wait.wait(timeout=5)
            return 0

        mock_process.wait.side_effect = _blocking_wait
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)
        on_complete = MagicMock()

        with patch.object(manager, '_copy_fallback', return_value=True):
            dispatched = manager.copy("hello", on_complete=on_complete)

        # copy() returned without waiting for wl-copy's exit.
        assert dispatched is True
        assert entered_wait.wait(timeout=2), "wl-copy wait() should have started"
        on_complete.assert_not_called()  # exit hasn't happened yet

        release_wait.set()
        # Give the background thread a moment to finish and invoke the callback.
        for _ in range(100):
            if on_complete.called:
                break
            _threading.Event().wait(0.01)

        on_complete.assert_called_once_with(True)

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_paste_not_invoked_before_copy_process_exits(self, mock_popen):
        """Simulates the exact field-bug sequencing at the ClipboardManager
        level: a consumer must not observe on_complete(True) -- and
        therefore must not trigger a paste -- until wl-copy's parent
        process has actually exited."""
        import threading as _threading

        release_wait = _threading.Event()
        mock_process = MagicMock()
        mock_process.wait.side_effect = lambda timeout=None: (release_wait.wait(5), 0)[1]
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        paste_triggered = _threading.Event()

        def _on_complete(success):
            if success:
                paste_triggered.set()

        with patch.object(manager, '_copy_fallback', return_value=True):
            manager.copy("hello", on_complete=_on_complete)

            # The process has not exited yet -- paste must not have fired.
            assert not paste_triggered.is_set()

            release_wait.set()
            assert paste_triggered.wait(timeout=2), "paste should fire once wl-copy exits"

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_nonzero_exit_reports_failure_no_paste(self, mock_popen):
        """A failing wl-copy (non-zero exit) must report failure via
        on_complete, write the fallback file, and log a warning -- never
        signal success (which would otherwise trigger a paste)."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 1
        mock_process.stderr.read.return_value = b"some error"
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)
        on_complete = MagicMock()

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            manager.copy("hello", on_complete=on_complete)

        on_complete.assert_called_once_with(False)
        mock_fallback.assert_called_once_with("hello")

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_timeout_waiting_for_exit_reports_failure_no_paste(self, mock_popen):
        """If wl-copy never exits within the configured timeout, we must
        degrade to clipboard-only (no paste) -- never treat the timeout as
        a green light to paste."""
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired('wl-copy', 5)
        mock_popen.return_value = mock_process

        config = ClipboardConfig(timeout_seconds=1.0)
        manager = ClipboardManager(config)
        on_complete = MagicMock()

        with patch.object(manager, '_copy_fallback', return_value=True) as mock_fallback:
            manager.copy("hello", on_complete=on_complete)

        on_complete.assert_called_once_with(False)
        mock_fallback.assert_called_once_with("hello")
        mock_process.kill.assert_called_once()

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_no_paste_once_flag_and_stdout_is_devnull(self, mock_popen):
        """Defect 2: --paste-once makes wl-copy serve exactly one paste and
        exit, so a clipboard manager or any reader consumes it first and
        later pastes get stale content -- it must never be passed. Defect
        2 (continued): stdout must be DEVNULL so the backgrounded child
        never inherits a pipe we never drain."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True):
            manager.copy("hello")

        args, kwargs = mock_popen.call_args
        assert args[0] == ['wl-copy']
        assert kwargs.get('stdout') == subprocess.DEVNULL

    @patch('subprocess.Popen')
    @patch.dict(os.environ, {"WAYLAND_DISPLAY": ":0"}, clear=False)
    def test_sync_call_without_on_complete_blocks_until_exit(self, mock_popen):
        """A caller that doesn't pass on_complete gets the old-style
        synchronous contract back, but now backed by a real wait for
        process exit rather than an assumed instant success."""
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        config = ClipboardConfig()
        manager = ClipboardManager(config)

        with patch.object(manager, '_copy_fallback', return_value=True):
            result = manager.copy("hello")

        assert result is True
        mock_process.wait.assert_called_once_with(timeout=config.timeout_seconds)


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

    @patch('subprocess.run')
    def test_paste_wayland_nonzero_exit(self, mock_run):
        """ydotool exiting non-zero (check=True) is reported as a failed paste."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, 'ydotool', stderr=b'device busy'
        )

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_wayland_unexpected_exception(self, mock_run):
        """Any other ydotool failure is caught and reported rather than propagating."""
        mock_run.side_effect = OSError("unexpected failure")

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_x11_tool_not_found(self, mock_run):
        """Test X11 paste fails gracefully when xdotool is missing."""
        mock_run.side_effect = FileNotFoundError("xdotool not found")

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_x11_timeout(self, mock_run):
        """Test X11 paste timeout handling."""
        mock_run.side_effect = subprocess.TimeoutExpired('xdotool', 5)

        config = ClipboardConfig(timeout_seconds=1.0)
        simulator = PasteSimulator('x11', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_x11_nonzero_exit(self, mock_run):
        """xdotool exiting non-zero (check=True) is reported as a failed paste."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, 'xdotool', stderr=b'no window focused'
        )

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        result = simulator.simulate_paste()

        assert result is False

    @patch('subprocess.run')
    def test_paste_x11_unexpected_exception(self, mock_run):
        """Any other xdotool failure is caught and reported rather than propagating."""
        mock_run.side_effect = OSError("unexpected failure")

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        result = simulator.simulate_paste()

        assert result is False


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

    @patch('subprocess.run')
    def test_check_tool_lookup_raises(self, mock_run):
        """An unexpected error from `which` is reported, not propagated."""
        mock_run.side_effect = OSError("which is broken")

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        status = simulator.check_availability()

        assert status['available'] is False
        assert 'Error checking xdotool' in status['reason']

    @patch('subprocess.run')
    def test_check_x11_available_skips_ydotool_permission_check(self, mock_run):
        """X11 availability is reported as soon as xdotool is found; no service/group check."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('x11', config)
        status = simulator.check_availability()

        assert status == {'available': True, 'reason': '', 'fix': ''}
        mock_run.assert_called_once()  # only the `which xdotool` call

    @patch('subprocess.run')
    def test_check_wayland_available_runs_ydotool_permission_check(self, mock_run):
        """Wayland availability also requires the ydotool service + group checks to pass."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        with patch('os.getgroups', return_value=[0]), \
             patch('grp.getgrgid') as mock_getgrgid:
            mock_getgrgid.return_value.gr_name = 'input'
            status = simulator.check_availability()

        assert status == {'available': True, 'reason': '', 'fix': ''}

    @patch('subprocess.run')
    def test_ydotool_service_not_running_user_and_system(self, mock_run):
        """Neither the user nor system ydotool.service is active -> not available."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # `which ydotool`
            MagicMock(returncode=1),  # systemctl --user is-active
            MagicMock(returncode=1),  # systemctl is-active (system)
        ]

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        status = simulator.check_availability()

        assert status['available'] is False
        assert 'service not running' in status['reason']

    @patch('subprocess.run')
    def test_ydotool_system_service_running_is_enough(self, mock_run):
        """The user-service check can fail as long as the system service is active."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # `which ydotool`
            MagicMock(returncode=1),  # systemctl --user is-active
            MagicMock(returncode=0),  # systemctl is-active (system) succeeds
        ]

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        with patch('os.getgroups', return_value=[0]), \
             patch('grp.getgrgid') as mock_getgrgid:
            mock_getgrgid.return_value.gr_name = 'input'
            status = simulator._check_ydotool_permissions()

        assert status == {'available': True, 'reason': '', 'fix': ''}

    @patch('subprocess.run')
    def test_ydotool_user_not_in_input_group(self, mock_run):
        """Service is active but the user lacks the `input` group -> not available."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        with patch('os.getgroups', return_value=[0]), \
             patch('grp.getgrgid') as mock_getgrgid:
            mock_getgrgid.return_value.gr_name = 'users'
            status = simulator._check_ydotool_permissions()

        assert status['available'] is False
        assert 'input group' in status['reason']

    @patch('subprocess.run')
    def test_ydotool_service_check_timeout_is_non_fatal(self, mock_run):
        """A timeout checking the service falls through to the group check instead of failing."""
        mock_run.side_effect = subprocess.TimeoutExpired('systemctl', 2)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        with patch('os.getgroups', return_value=[0]), \
             patch('grp.getgrgid') as mock_getgrgid:
            mock_getgrgid.return_value.gr_name = 'input'
            status = simulator._check_ydotool_permissions()

        assert status == {'available': True, 'reason': '', 'fix': ''}

    @patch('subprocess.run')
    def test_ydotool_group_check_error_is_non_fatal(self, mock_run):
        """An error reading group membership degrades gracefully to 'available'."""
        mock_run.return_value = MagicMock(returncode=0)

        config = ClipboardConfig()
        simulator = PasteSimulator('wayland', config)
        with patch('os.getgroups', side_effect=OSError("no groups")):
            status = simulator._check_ydotool_permissions()

        assert status == {'available': True, 'reason': '', 'fix': ''}


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


class TestSmoke:
    """Deterministic clipboard smoke tests."""

    @patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False)
    def test_copy_uses_fallback_without_display(self, tmp_path):
        """Copy should succeed via fallback path in headless environments."""
        fallback_path = tmp_path / "clipboard_fallback.txt"
        config = ClipboardConfig(fallback_path=str(fallback_path))
        manager = ClipboardManager(config)

        result = manager.copy("Integration test text")
        assert result is True
        assert fallback_path.exists()
        assert "Integration test text" in fallback_path.read_text(encoding="utf-8")


class TestClipboardManagerFactoryTestMode:
    """WHISP-46 incident follow-up: a real e2e run overwrote the operator's
    real Wayland clipboard because unsetting WAYLAND_DISPLAY alone does not
    stop ClipboardManager.detect_session_type() from falling back to
    scanning $XDG_RUNTIME_DIR for a real wayland-* socket, nor stop wl-copy
    itself from connecting to the real compositor by default. Under
    WHISPER_ALOUD_TEST_MODE=1, build_clipboard_manager() must return a
    TestClipboardStub that makes zero subprocess calls, ever -- not a real
    ClipboardManager with a session type that happens to resolve to
    'unknown'."""

    def test_build_clipboard_manager_returns_stub_in_test_mode(self, monkeypatch):
        from whisper_aloud.clipboard import TestClipboardStub, build_clipboard_manager

        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        config = ClipboardConfig()
        manager = build_clipboard_manager(config)
        assert isinstance(manager, TestClipboardStub)

    def test_build_clipboard_manager_returns_real_manager_without_test_mode(self, monkeypatch):
        from whisper_aloud.clipboard import build_clipboard_manager

        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        config = ClipboardConfig()
        manager = build_clipboard_manager(config)
        assert isinstance(manager, ClipboardManager)

    def test_stub_copy_makes_zero_subprocess_calls(self, monkeypatch):
        """Even with a real wayland-* socket present (simulating the exact
        incident), the stub must never shell out."""
        from whisper_aloud.clipboard import TestClipboardStub

        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

        def _boom(*a, **k):
            raise AssertionError("TestClipboardStub must never call subprocess.run/Popen")

        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr(subprocess, "Popen", _boom)

        stub = TestClipboardStub(ClipboardConfig())
        assert stub.copy("hello") is True

    def test_stub_records_copied_text_to_env_file(self, monkeypatch, tmp_path):
        from whisper_aloud.clipboard import TestClipboardStub

        target = tmp_path / "clipboard.txt"
        monkeypatch.setenv("WHISPER_ALOUD_TEST_CLIPBOARD_FILE", str(target))

        stub = TestClipboardStub(ClipboardConfig())
        stub.copy("the quick brown fox")

        assert target.read_text(encoding="utf-8").strip() == "the quick brown fox"
        assert stub.copied == ["the quick brown fox"]

    def test_stub_without_env_file_still_records_in_memory(self):
        from whisper_aloud.clipboard import TestClipboardStub

        stub = TestClipboardStub(ClipboardConfig())
        assert stub.copy("no file configured") is True
        assert stub.copied == ["no file configured"]

    def test_stub_rejects_empty_text(self):
        from whisper_aloud.clipboard import TestClipboardStub

        stub = TestClipboardStub(ClipboardConfig())
        assert stub.copy("") is False
        assert stub.copied == []
