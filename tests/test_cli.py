"""Tests for CLI functionality."""

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from whisper_aloud.__main__ import daemon_main, main, _format_uptime, _format_time_ago, _error
from whisper_aloud.exceptions import WhisperAloudError


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Isolate every test in this module from the real $HOME/.config --
    cmd_config() (and WhisperAloudConfig.load() underneath it) read
    ~/.config/whisper_aloud/config.json by default, and these tests must
    never touch the real one."""
    monkeypatch.setenv("HOME", str(tmp_path))


# ─── Utility tests ────────────────────────────────────────────────────────────


def test_format_uptime_seconds():
    assert _format_uptime(45) == "45s"


def test_format_uptime_minutes():
    assert _format_uptime(125) == "2m 5s"


def test_format_uptime_hours():
    assert _format_uptime(7380) == "2h 3m"


def test_format_time_ago_just_now():
    assert _format_time_ago(30) == "just now"


def test_format_time_ago_minutes():
    assert _format_time_ago(300) == "5m ago"


def test_format_time_ago_hours():
    assert _format_time_ago(7200) == "2h ago"


def test_format_time_ago_days():
    assert _format_time_ago(90000) == "1d ago"


def test_error_returns_1(capsys):
    result = _error("something broke")
    assert result == 1
    assert "whisper-aloud: error: something broke" in capsys.readouterr().err


def test_error_with_hint(capsys):
    _error("daemon is not running", "Start it with: whisper-aloud --daemon")
    captured = capsys.readouterr().err
    assert "daemon is not running" in captured
    assert "Start it with" in captured


# ─── Help and version ─────────────────────────────────────────────────────────


def test_cli_help():
    """Test CLI help output."""
    with patch('sys.argv', ['whisper-aloud', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_cli_version():
    """Test CLI version output."""
    with patch('sys.argv', ['whisper-aloud', '--version']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_cli_no_args_shows_help(capsys):
    """No arguments shows help text."""
    with patch('sys.argv', ['whisper-aloud']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "usage:" in output.lower() or "whisper-aloud" in output


# ─── Transcribe subcommand ────────────────────────────────────────────────────


def test_cli_transcribe_missing_file():
    """Test transcribe with missing audio file."""
    with patch('sys.argv', ['whisper-aloud', 'transcribe', 'nonexistent.wav']):
        exit_code = main()
        assert exit_code == 1


def test_cli_transcribe_invalid_model():
    """Test transcribe with invalid model name."""
    with patch('sys.argv', ['whisper-aloud', 'transcribe', '--model', 'invalid', 'dummy.wav']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2


@patch('whisper_aloud.__main__.Transcriber')
def test_cli_transcribe_success(mock_transcriber_class):
    """Test successful transcription flow."""
    mock_transcriber = mock_transcriber_class.return_value
    mock_result = mock_transcriber.transcribe_file.return_value
    mock_result.text = "Hello world"
    mock_result.language = "en"
    mock_result.duration = 2.5
    mock_result.processing_time = 1.2
    mock_result.confidence = 0.95

    with patch('sys.argv', ['whisper-aloud', 'transcribe', 'test.wav']):
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 0
            mock_transcriber.load_model.assert_called_once()
            mock_transcriber.transcribe_file.assert_called_once()


@patch('whisper_aloud.__main__.Transcriber')
def test_cli_transcribe_verbose(mock_transcriber_class):
    """Test verbose transcription output."""
    mock_transcriber = mock_transcriber_class.return_value
    mock_result = mock_transcriber.transcribe_file.return_value
    mock_result.text = "Test transcription"
    mock_result.language = "es"
    mock_result.duration = 3.0
    mock_result.processing_time = 2.1
    mock_result.confidence = 0.87

    with patch('sys.argv', ['whisper-aloud', '-v', 'transcribe', 'test.wav']):
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 0
            mock_transcriber.load_model.assert_called_once()


@patch('whisper_aloud.__main__.Transcriber')
def test_cli_transcribe_keyboard_interrupt(mock_transcriber_class):
    """Test transcribe handles keyboard interrupt."""
    mock_transcriber = mock_transcriber_class.return_value
    mock_transcriber.transcribe_file.side_effect = KeyboardInterrupt()

    with patch('sys.argv', ['whisper-aloud', 'transcribe', 'test.wav']):
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 130


@patch('whisper_aloud.__main__.Transcriber')
def test_cli_transcribe_unexpected_exception(mock_transcriber_class, capsys):
    """An unexpected (non-WhisperAloudError) failure is reported, not left to crash the CLI."""
    mock_transcriber = mock_transcriber_class.return_value
    mock_transcriber.transcribe_file.side_effect = RuntimeError("ctranslate2 segfaulted")

    with patch('sys.argv', ['whisper-aloud', 'transcribe', 'test.wav']):
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 1
            assert "ctranslate2 segfaulted" in capsys.readouterr().err


@patch('whisper_aloud.__main__.Transcriber')
def test_cli_transcribe_unexpected_exception_verbose_prints_traceback(mock_transcriber_class, capsys):
    """-v (placed *after* the subcommand) additionally prints a traceback on failure."""
    mock_transcriber = mock_transcriber_class.return_value
    mock_transcriber.transcribe_file.side_effect = RuntimeError("ctranslate2 segfaulted")

    with patch('sys.argv', ['whisper-aloud', 'transcribe', '-v', 'test.wav']):
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 1
            assert "Traceback" in capsys.readouterr().err


def test_verbose_flag_works_before_or_after_subcommand():
    from whisper_aloud.__main__ import build_parser

    args = build_parser().parse_args(["-v", "transcribe", "test.wav"])
    assert args.verbose is True
    assert build_parser().parse_args(["transcribe", "-v", "test.wav"]).verbose is True
    assert build_parser().parse_args(["transcribe", "test.wav"]).verbose is False


# ─── Daemon commands ──────────────────────────────────────────────────────────


@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_daemon_command_when_not_running(mock_check, capsys):
    """Daemon commands error when service not running."""
    with patch('sys.argv', ['whisper-aloud', 'start']):
        exit_code = main()
        assert exit_code == 1
        assert "not running" in capsys.readouterr().err


# ─── Field bug 5: CLI D-Bus activation ──────────────────────────────────────


@patch('whisper_aloud.__main__._activate_daemon_connection', return_value=True)
@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_require_daemon_falls_back_to_activation_probe(mock_check, mock_activate, capsys):
    """When GetNameOwner says nothing is there, _require_daemon must still
    try to D-Bus-activate the daemon (field bug 5) before giving up."""
    from whisper_aloud.__main__ import _require_daemon

    assert _require_daemon() is True
    mock_activate.assert_called_once()


@patch('whisper_aloud.__main__._activate_daemon_connection')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_require_daemon_skips_activation_probe_when_already_running(
    mock_check, mock_activate
):
    from whisper_aloud.__main__ import _require_daemon

    assert _require_daemon() is True
    mock_activate.assert_not_called()


@patch('whisper_aloud.__main__._activate_daemon_connection', return_value=False)
@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_require_daemon_prints_flatpak_hint_when_sandboxed(mock_check, mock_activate, capsys):
    with patch('whisper_aloud.__main__.is_flatpak_sandbox', return_value=True):
        from whisper_aloud.__main__ import _require_daemon

        assert _require_daemon() is False
    err = capsys.readouterr().err
    assert "daemon is not running" in err
    assert "flatpak run --command=whisper-aloud org.fede.whisperaloud --daemon" in err


@patch('whisper_aloud.__main__._activate_daemon_connection', return_value=False)
@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_require_daemon_prints_non_flatpak_hint_otherwise(mock_check, mock_activate, capsys):
    with patch('whisper_aloud.__main__.is_flatpak_sandbox', return_value=False):
        from whisper_aloud.__main__ import _require_daemon

        assert _require_daemon() is False
    err = capsys.readouterr().err
    assert "systemctl --user start whisper-aloud" in err


@patch('whisper_aloud.__main__._activate_daemon_connection')
@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_quit_does_not_trigger_activation(mock_check, mock_activate, capsys):
    """quit must never start the daemon just to immediately stop it."""
    with patch('sys.argv', ['whisper-aloud', 'quit']):
        exit_code = main()
    assert exit_code == 1
    mock_activate.assert_not_called()


class TestConfigureDaemonLogging:
    """Field bug 6: nothing on the --daemon path ever called
    logging.basicConfig, so only WARNING+ reached the journal and
    --daemon --verbose changed nothing."""

    def _reset_logging(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        root.setLevel(logging.WARNING)

    def test_default_level_is_info(self, monkeypatch):
        from whisper_aloud.__main__ import _configure_daemon_logging

        monkeypatch.delenv("WHISPER_ALOUD_LOG_LEVEL", raising=False)
        self._reset_logging()
        _configure_daemon_logging(verbose=False)
        assert logging.getLogger().getEffectiveLevel() == logging.INFO
        self._reset_logging()

    def test_verbose_sets_debug(self, monkeypatch):
        from whisper_aloud.__main__ import _configure_daemon_logging

        monkeypatch.delenv("WHISPER_ALOUD_LOG_LEVEL", raising=False)
        self._reset_logging()
        _configure_daemon_logging(verbose=True)
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
        self._reset_logging()

    def test_env_var_overrides_verbose_flag(self, monkeypatch):
        from whisper_aloud.__main__ import _configure_daemon_logging

        monkeypatch.setenv("WHISPER_ALOUD_LOG_LEVEL", "WARNING")
        self._reset_logging()
        _configure_daemon_logging(verbose=True)
        assert logging.getLogger().getEffectiveLevel() == logging.WARNING
        self._reset_logging()

    def test_invalid_env_var_falls_back_to_verbose_flag(self, monkeypatch):
        from whisper_aloud.__main__ import _configure_daemon_logging

        monkeypatch.setenv("WHISPER_ALOUD_LOG_LEVEL", "not-a-level")
        self._reset_logging()
        _configure_daemon_logging(verbose=True)
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
        self._reset_logging()

    def test_handle_daemon_command_configures_logging(self, monkeypatch):
        """handle_daemon_command must configure logging before anything
        else runs (so startup messages aren't silently dropped)."""
        from whisper_aloud import __main__ as cli

        called = {}
        monkeypatch.setattr(
            cli, "_configure_daemon_logging", lambda verbose=False: called.setdefault("v", verbose)
        )
        fake_bus = MagicMock()
        fake_owner = MagicMock()
        with (
            patch("pydbus.SessionBus", return_value=fake_bus),
            patch("whisper_aloud.service.daemon.acquire_bus_name", return_value=fake_owner),
            patch("whisper_aloud.service.WhisperAloudService") as mock_service_cls,
        ):
            mock_service_cls.return_value.run.return_value = None

            class _Args:
                verbose = True

            cli.handle_daemon_command(_Args())

        assert called == {"v": True}


def test_activate_daemon_connection_uses_client_wait(monkeypatch):
    from whisper_aloud.__main__ import _activate_daemon_connection

    mock_client = MagicMock()
    mock_client.connect_with_activation_wait.return_value = True
    mock_client_cls = MagicMock(return_value=mock_client)
    monkeypatch.setattr(
        "whisper_aloud.service.client.WhisperAloudClient", mock_client_cls
    )

    assert _activate_daemon_connection(max_wait_seconds=5.0) is True
    mock_client.connect_with_activation_wait.assert_called_once_with(max_wait_seconds=5.0)
    mock_client.disconnect.assert_called_once()


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_status_formatted_output(mock_check, mock_call, capsys):
    """Status command shows formatted output."""
    mock_call.return_value = {
        "state": "idle",
        "version": "0.2.0",
        "model": "base",
        "device": "cpu",
        "hotkey_backend": "xdg-portal",
        "uptime": 3720.5,
    }
    with patch('sys.argv', ['whisper-aloud', 'status']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "WhisperAloud v0.2.0" in output
        assert "idle" in output
        assert "base" in output
        assert "1h 2m" in output


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_status_shows_bound_trigger_when_present(mock_check, mock_call, capsys):
    """Status should show the portal's actual bound trigger alongside the
    backend name, e.g. for a GNOME-chosen accelerator that differs from
    our configured preference."""
    mock_call.return_value = {
        "state": "idle",
        "version": "0.2.0",
        "model": "base",
        "device": "cpu",
        "hotkey_backend": "portal",
        "hotkey_trigger": "Press <Control>space",
        "uptime": 3720.5,
    }
    with patch('sys.argv', ['whisper-aloud', 'status']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "portal" in output
        assert "Press <Control>space" in output


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_status_shows_model_state(mock_check, mock_call, capsys):
    """API 1.6.0: status reports the model readiness state."""
    mock_call.return_value = {
        "state": "idle", "version": "0.3.0", "model": "base", "device": "cpu",
        "hotkey_backend": "none", "uptime": 5.0,
        "model_state": "loading", "model_error": "",
    }
    with patch('sys.argv', ['whisper-aloud', 'status']):
        assert main() == 0
        output = capsys.readouterr().out
        assert "Model:    base (cpu) [loading]" in output
        assert "Model error" not in output


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_status_shows_model_error_detail(mock_check, mock_call, capsys):
    mock_call.return_value = {
        "state": "idle", "version": "0.3.0", "model": "base", "device": "cpu",
        "hotkey_backend": "none", "uptime": 5.0,
        "model_state": "missing", "model_error": "Model 'base' is not downloaded",
    }
    with patch('sys.argv', ['whisper-aloud', 'status']):
        assert main() == 0
        output = capsys.readouterr().out
        assert "[missing]" in output
        assert "Model 'base' is not downloaded" in output


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_status_without_model_state_from_older_daemon(mock_check, mock_call, capsys):
    mock_call.return_value = {
        "state": "idle", "version": "0.2.0", "model": "base", "device": "cpu",
        "hotkey_backend": "none", "uptime": 5.0,
    }
    with patch('sys.argv', ['whisper-aloud', 'status']):
        assert main() == 0
        output = capsys.readouterr().out
        assert "Model:    base (cpu)\n" in output


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_start_command(mock_check, mock_call, capsys):
    """Start command calls daemon and prints confirmation."""
    with patch('sys.argv', ['whisper-aloud', 'start']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("StartRecording")
        assert "Recording started" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_start_command_reports_model_not_available(mock_check, mock_call, capsys):
    """1.6.1: a refused StartRecording() (missing/errored model) prints the
    reason and the download hint, and exits non-zero."""
    mock_call.side_effect = [
        False,
        {
            "state": "idle", "model": "base", "model_state": "missing",
            "model_error": "Model 'base' is not downloaded",
        },
    ]
    with patch('sys.argv', ['whisper-aloud', 'start']):
        exit_code = main()
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "Model 'base' is not downloaded" in err
        assert "download-model" in err


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_toggle_command_reports_model_not_available(mock_check, mock_call, capsys):
    mock_call.side_effect = [
        "error",
        {"state": "idle", "model_state": "error", "model_error": "CUDA exploded"},
    ]
    with patch('sys.argv', ['whisper-aloud', 'toggle']):
        exit_code = main()
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "CUDA exploded" in err


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_toggle_command_error_without_model_gate_falls_back(mock_check, mock_call, capsys):
    """A ToggleRecording "error" unrelated to the model (e.g. a transcription
    already in flight) still reports *something* non-zero, generically."""
    mock_call.side_effect = [
        "error",
        {"state": "transcribing", "model_state": "ready", "model_error": ""},
    ]
    with patch('sys.argv', ['whisper-aloud', 'toggle']):
        exit_code = main()
        assert exit_code == 1
        assert capsys.readouterr().err.strip()


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_quit_command(mock_check, mock_call, capsys):
    """Quit command calls daemon Quit."""
    with patch('sys.argv', ['whisper-aloud', 'quit']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("Quit")
        assert "Daemon stopped" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_stop_command(mock_check, mock_call, capsys):
    """Stop command calls daemon StopRecording."""
    with patch('sys.argv', ['whisper-aloud', 'stop']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("StopRecording")
        assert "transcribing" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_toggle_command(mock_check, mock_call, capsys):
    """Toggle command prints the state returned by the daemon."""
    mock_call.return_value = "recording"
    with patch('sys.argv', ['whisper-aloud', 'toggle']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("ToggleRecording")
        assert "State: recording" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_cancel_command_while_recording(mock_check, mock_call, capsys):
    """Cancel command reports success when a recording was actually cancelled."""
    mock_call.return_value = True
    with patch('sys.argv', ['whisper-aloud', 'cancel']):
        exit_code = main()
        assert exit_code == 0
        assert "Recording cancelled" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_cancel_command_when_idle(mock_check, mock_call, capsys):
    """Cancel command reports 'Not recording' when there was nothing to cancel."""
    mock_call.return_value = False
    with patch('sys.argv', ['whisper-aloud', 'cancel']):
        exit_code = main()
        assert exit_code == 0
        assert "Not recording" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_reload_command_success(mock_check, mock_call, capsys):
    """Reload command reports success when the daemon reloads cleanly."""
    mock_call.return_value = True
    with patch('sys.argv', ['whisper-aloud', 'reload']):
        exit_code = main()
        assert exit_code == 0
        assert "Configuration reloaded" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_reload_command_failure_still_exits_zero(mock_check, mock_call, capsys):
    """A daemon-reported reload failure prints the message but the CLI call itself succeeded."""
    mock_call.return_value = False
    with patch('sys.argv', ['whisper-aloud', 'reload']):
        exit_code = main()
        assert exit_code == 0
        assert "Configuration reload failed" in capsys.readouterr().out


@patch(
    'whisper_aloud.__main__.call_service_method',
    side_effect=WhisperAloudError("D-Bus call timed out"),
)
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_start_command_reports_service_error(mock_check, mock_call, capsys):
    """A WhisperAloudError from the D-Bus call is surfaced as a CLI error, not a crash."""
    with patch('sys.argv', ['whisper-aloud', 'start']):
        exit_code = main()
        assert exit_code == 1
        assert "D-Bus call timed out" in capsys.readouterr().err


# ─── History subcommand ───────────────────────────────────────────────────────


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_lists_recent_entries(mock_check, mock_call, capsys):
    mock_call.return_value = [
        {"id": 1, "text": "hello there", "timestamp": 0},
    ]
    with patch('sys.argv', ['whisper-aloud', 'history']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("GetHistory", 10)
        assert "hello there" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_empty_prints_placeholder(mock_check, mock_call, capsys):
    mock_call.return_value = []
    with patch('sys.argv', ['whisper-aloud', 'history']):
        exit_code = main()
        assert exit_code == 0
        assert "No transcriptions yet." in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_truncates_long_text(mock_check, mock_call, capsys):
    mock_call.return_value = [{"id": 1, "text": "x" * 100, "timestamp": 0}]
    with patch('sys.argv', ['whisper-aloud', 'history']):
        exit_code = main()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "..." in out
        assert "x" * 100 not in out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_search_passes_query_through(mock_check, mock_call, capsys):
    mock_call.return_value = []
    with patch('sys.argv', ['whisper-aloud', 'history', 'search', 'budget meeting']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("SearchHistory", "budget meeting", 10)


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_list_explicit_subaction(mock_check, mock_call, capsys):
    """`history list` is an explicit spelling of the default (no-subaction) behavior."""
    mock_call.return_value = [{"id": 1, "text": "hello there", "timestamp": 0}]
    with patch('sys.argv', ['whisper-aloud', 'history', 'list', '--limit', '5']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("GetHistory", 5)
        assert "hello there" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_list_favorites_flag(mock_check, mock_call, capsys):
    mock_call.return_value = []
    with patch('sys.argv', ['whisper-aloud', 'history', 'list', '--favorites']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("GetFavoriteHistory", 10)


# ─── History export subcommand (WHISP-79) ─────────────────────────────────────


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_export_prints_to_stdout_by_default(mock_check, mock_call, capsys):
    mock_call.return_value = "[]"
    with patch('sys.argv', ['whisper-aloud', 'history', 'export', '--format', 'json']):
        exit_code = main()
        assert exit_code == 0
        name, fmt, filters = mock_call.call_args[0]
        assert (name, fmt) == ("ExportHistory", "json")
        assert filters == {}
        assert "[]" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_export_writes_to_output_file(mock_check, mock_call, tmp_path, capsys):
    mock_call.return_value = "ID,Timestamp\r\n"
    output_file = tmp_path / "out.csv"
    with patch('sys.argv', [
        'whisper-aloud', 'history', 'export', '--format', 'csv', '--output', str(output_file),
    ]):
        exit_code = main()
        assert exit_code == 0
        with open(output_file, "r", newline="", encoding="utf-8") as f:
            assert f.read() == "ID,Timestamp\r\n"
        assert "Exported" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_history_export_passes_all_filters(mock_check, mock_call, capsys):
    mock_call.return_value = "[]"
    with patch('sys.argv', [
        'whisper-aloud', 'history', 'export', '--format', 'json',
        '--since', '2026-01-01', '--until', '2026-01-31',
        '--favorites', '--query', 'budget', '--limit', '25',
    ]):
        exit_code = main()
        assert exit_code == 0
        _, _, filters = mock_call.call_args[0]
        # filters are wrapped as GLib.Variant-like; unwrap for the assertion
        unwrapped = {k: (v.unpack() if hasattr(v, 'unpack') else v) for k, v in filters.items()}
        assert unwrapped == {
            "since": "2026-01-01",
            "until": "2026-01-31",
            "favorites_only": True,
            "query": "budget",
            "limit": 25,
        }


@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_history_export_requires_daemon(mock_check, capsys):
    with patch('sys.argv', ['whisper-aloud', 'history', 'export', '--format', 'json']):
        exit_code = main()
        assert exit_code == 1
        assert "daemon is not running" in capsys.readouterr().err


# ─── Config subcommand ────────────────────────────────────────────────────────


def test_config_validate(capsys):
    """Config validate shows config summary."""
    with patch('sys.argv', ['whisper-aloud', 'config', 'validate']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "valid" in output.lower()


def test_config_show(capsys):
    """Config show displays current configuration."""
    with patch('sys.argv', ['whisper-aloud', 'config', 'show']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "[Model]" in output
        assert "[Audio]" in output


def test_config_no_action_defaults_to_show(capsys):
    """Config with no action defaults to show."""
    with patch('sys.argv', ['whisper-aloud', 'config']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "[Model]" in output


def test_config_unknown_action_is_an_error(capsys):
    """cmd_config() defends against an action outside {'show', 'validate'}.

    Not reachable through the argparse subparser (it only ever produces those two
    values or None), but cmd_config() guards it anyway -- exercise that branch
    directly.
    """
    from whisper_aloud.__main__ import cmd_config

    class _Args:
        config_action = "frobnicate"

    exit_code = cmd_config(_Args())
    assert exit_code == 1
    assert "unknown config action" in capsys.readouterr().err


def test_config_validate_reports_error_on_failure(capsys, monkeypatch):
    """A broken config load surfaces as a CLI error from `config validate`."""
    import whisper_aloud.__main__ as cli

    def _boom(*args, **kwargs):
        raise RuntimeError("config.json is not valid JSON")

    monkeypatch.setattr(cli.WhisperAloudConfig, "load", staticmethod(_boom))
    with patch('sys.argv', ['whisper-aloud', 'config', 'validate']):
        exit_code = main()
        assert exit_code == 1
        assert "configuration error" in capsys.readouterr().err


def test_config_show_reports_error_on_failure(capsys, monkeypatch):
    """A broken config load surfaces as a CLI error from `config show`."""
    import whisper_aloud.__main__ as cli

    def _boom(*args, **kwargs):
        raise RuntimeError("config.json is not valid JSON")

    monkeypatch.setattr(cli.WhisperAloudConfig, "load", staticmethod(_boom))
    with patch('sys.argv', ['whisper-aloud', 'config', 'show']):
        exit_code = main()
        assert exit_code == 1
        assert "configuration error" in capsys.readouterr().err


# ─── WHISP-75: validate/show on a malformed config file ──────────────────────


def _write_malformed_config():
    """Write an unparseable config.json under the isolated $HOME (see
    isolated_home above) and return its path."""
    config_dir = Path(os.environ["HOME"]) / ".config" / "whisper_aloud"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    config_path.write_text("{ not valid json")
    return config_path


def test_config_validate_fails_on_malformed_json(capsys):
    """WHISP-75: `config validate` must use load(strict=True) so a config
    file that exists but is malformed JSON is reported as invalid, not
    silently treated as valid defaults."""
    config_path = _write_malformed_config()
    with patch('sys.argv', ['whisper-aloud', 'config', 'validate']):
        exit_code = main()
        assert exit_code != 0
        output = capsys.readouterr()
        assert "valid" not in output.out.lower()
        # The error must name the file and the underlying parse error.
        assert str(config_path) in output.err
        assert "json" in output.err.lower() or "expecting" in output.err.lower()


def test_config_validate_never_writes_the_file(capsys):
    """`config validate` must never write back to config.json, even when
    reporting it as invalid."""
    config_path = _write_malformed_config()
    original_bytes = config_path.read_bytes()
    with patch('sys.argv', ['whisper-aloud', 'config', 'validate']):
        main()
    assert config_path.read_bytes() == original_bytes


def test_config_show_warns_and_falls_back_to_defaults_on_malformed_json(capsys):
    """WHISP-75: `config show` must warn on stderr that it is falling back
    to defaults because the file is malformed, rather than silently
    displaying defaults as if they came from a valid file."""
    config_path = _write_malformed_config()
    with patch('sys.argv', ['whisper-aloud', 'config', 'show']):
        exit_code = main()
        assert exit_code == 0
        output = capsys.readouterr()
        assert "[Model]" in output.out
        assert str(config_path) in output.err
        assert "malformed" in output.err.lower() or "invalid" in output.err.lower()


def test_config_show_never_writes_the_file(capsys):
    """`config show` must never write back to config.json, even when
    falling back to defaults because it is malformed."""
    config_path = _write_malformed_config()
    original_bytes = config_path.read_bytes()
    with patch('sys.argv', ['whisper-aloud', 'config', 'show']):
        main()
    assert config_path.read_bytes() == original_bytes


def test_config_validate_still_succeeds_on_a_well_formed_file(capsys):
    """Sanity check: the strict load change must not break the happy path."""
    config_dir = Path(os.environ["HOME"]) / ".config" / "whisper_aloud"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps({"transcription": {"language": "fr"}}))
    with patch('sys.argv', ['whisper-aloud', 'config', 'validate']):
        exit_code = main()
        assert exit_code == 0
        assert "valid" in capsys.readouterr().out.lower()


# ─── Bus name ─────────────────────────────────────────────────────────────────


def test_cli_uses_correct_bus_name():
    """CLI should use org.fede.whisperaloud bus name."""
    import inspect
    import whisper_aloud.__main__ as cli
    source = inspect.getsource(cli)
    assert "org.fede.whisperaloud" in source
    assert "org.fede.whisperAloud" not in source


# ─── Daemon entrypoint ────────────────────────────────────────────────────────


@patch('whisper_aloud.__main__.handle_daemon_command')
def test_daemon_entrypoint(mock_handle):
    """Dedicated daemon entrypoint calls daemon handler."""
    mock_handle.return_value = 0
    exit_code = daemon_main()
    assert exit_code == 0
    mock_handle.assert_called_once()


@patch('whisper_aloud.__main__.handle_daemon_command')
def test_daemon_flag(mock_handle):
    """--daemon flag triggers daemon mode."""
    mock_handle.return_value = 0
    with patch('sys.argv', ['whisper-aloud', '--daemon']):
        exit_code = main()
        assert exit_code == 0
        mock_handle.assert_called_once()


# ─── download-model command ───────────────────────────────────────────────────


def test_download_model_success(monkeypatch, capsys):
    """download-model calls ensure_model and reports the resolved path."""
    import whisper_aloud.stt.model_download as md
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", "parakeet-tdt-0.6b-v3"])
    assert rc == 0
    assert "/cache/parakeet-tdt-0.6b-v3" in capsys.readouterr().out


def test_download_model_unknown(monkeypatch, capsys):
    """An unknown model name exits non-zero with an error."""
    import whisper_aloud.stt.model_download as md
    def _raise(name):
        raise ValueError("Unknown model 'nope'. Known: parakeet-tdt-0.6b-v3")
    monkeypatch.setattr(md, "ensure_model", _raise)
    rc = main(["download-model", "nope"])
    assert rc == 1
    assert "Unknown model" in capsys.readouterr().err


def test_download_model_network_failure(monkeypatch, capsys):
    """A download/extract failure (anything but ValueError) is reported, not raised."""
    import whisper_aloud.stt.model_download as md
    def _raise(name):
        raise ConnectionError("huggingface.co unreachable")
    monkeypatch.setattr(md, "ensure_model", _raise)
    rc = main(["download-model", "parakeet-tdt-0.6b-v3"])
    assert rc == 1
    assert "download failed" in capsys.readouterr().err


# ─── models command ────────────────────────────────────────────────────────────


def test_models_lists_all_catalog_engines_and_names(capsys):
    """`whisper-aloud models --all` prints every catalog entry's engine and name,
    including ones pending license review (hidden by default -- see the
    needs_review tests below)."""
    from whisper_aloud.stt import catalog
    rc = main(["models", "--all"])
    assert rc == 0
    out = capsys.readouterr().out
    for entry in catalog.entries():
        assert entry.engine in out
        assert entry.name in out


def test_models_marks_recommended_entries(capsys):
    rc = main(["models"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "recommended" in out
    assert "yes" in out


# ─── WHISP-56 CHANGES_REQUIRED item 4: needs_review gating ─────────────────────
#
# WHISP-86: nemotron-streaming's license review completed (needs_review is now
# False -- see tests/stt/test_catalog.py), so it's no longer this gate's real-world
# example. These tests now exercise the general needs_review mechanism against a
# synthetic catalog entry instead; nemotron's own new (no-gate, notice-only)
# behavior is covered separately below.


def _synthetic_needs_review_entry(**overrides):
    from whisper_aloud.stt.catalog import ModelEntry

    fields = dict(
        engine="fake-pending-engine",
        name="fake-pending-model",
        label="Fake Pending Model",
        size_mb=1,
        languages=None,
        streaming=False,
        license="Fake-Pending-License",
        devices=("cpu",),
        tier="fast",
        recommended=False,
        needs_review=True,
    )
    fields.update(overrides)
    return ModelEntry(**fields)


def test_models_hides_needs_review_entries_by_default(monkeypatch, capsys):
    from whisper_aloud.stt import catalog
    entry = _synthetic_needs_review_entry()
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (entry,))
    rc = main(["models"])
    assert rc == 0
    out = capsys.readouterr().out
    assert entry.name not in out


def test_models_all_shows_needs_review_entries_marked(monkeypatch, capsys):
    from whisper_aloud.stt import catalog
    entry = _synthetic_needs_review_entry()
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (entry,))
    rc = main(["models", "--all"])
    assert rc == 0
    out = capsys.readouterr().out
    assert entry.name in out
    assert "pending license review" in out


def test_download_model_refuses_needs_review_without_accept_license(monkeypatch, capsys):
    from whisper_aloud.stt import catalog
    entry = _synthetic_needs_review_entry(name="fake-pending-dl")
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (entry,))
    rc = main(["download-model", entry.name])
    assert rc == 1
    err = capsys.readouterr().err
    assert "pending license review" in err
    assert entry.license in err


def test_download_model_proceeds_with_accept_license(monkeypatch, capsys):
    from whisper_aloud.stt import catalog
    import whisper_aloud.stt.model_download as md
    entry = _synthetic_needs_review_entry(name="fake-pending-dl2")
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (entry,))
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", entry.name, "--accept-license"])
    assert rc == 0
    assert f"/cache/{entry.name}" in capsys.readouterr().out


# ─── WHISP-86: Nemotron license review completed -- no gate, notice instead ────


def test_models_shows_nemotron_by_default():
    """Nemotron's needs_review=False now, so it's a normal, visible catalog entry
    (no --all needed) -- unlike before WHISP-86."""
    from whisper_aloud.stt import catalog

    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    assert entry.needs_review is False


def test_models_lists_nemotron_without_all_flag(capsys):
    rc = main(["models"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nemotron-3.5-streaming-560ms" in out


def test_download_model_nemotron_no_longer_requires_accept_license(monkeypatch, capsys):
    import whisper_aloud.stt.model_download as md
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", "nemotron-3.5-streaming-560ms"])
    assert rc == 0
    assert "/cache/nemotron-3.5-streaming-560ms" in capsys.readouterr().out


def test_download_model_nemotron_prints_one_line_license_notice(monkeypatch, capsys):
    """Non-blocking: the license notice is printed to stdout alongside the
    success message, not required via --accept-license."""
    import whisper_aloud.stt.model_download as md
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", "nemotron-3.5-streaming-560ms"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "License notice" in out
    assert "OpenMDW-1.1" in out
    assert "THIRD_PARTY_MODELS.md" in out


def test_download_model_nemotron_resolves_via_model_sources(monkeypatch, tmp_path, capsys):
    """WHISP-86: `download-model nemotron-3.5-streaming-560ms` must actually
    resolve through `ensure_model`'s normal MODEL_SOURCES lookup (same path as
    parakeet), not just happen to print a success message -- only `_download`
    is monkeypatched here (copies a local fixture tarball), so this exercises
    `ensure_model`/`MODEL_SOURCES` for real, with no network access."""
    import tarfile
    from pathlib import Path

    import whisper_aloud.stt.model_download as md

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    src = tmp_path / "src" / "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8"
    src.mkdir(parents=True)
    (src / "tokens.txt").write_text("<blk> 0\n")
    (src / "encoder.onnx").write_bytes(b"\x00")
    (src / "decoder.onnx").write_bytes(b"\x00")
    (src / "joiner.onnx").write_bytes(b"\x00")
    fixture = tmp_path / "fixture.tar.bz2"
    with tarfile.open(fixture, "w:bz2") as tf:
        tf.add(src, arcname=src.name)

    def _fake_download(url, dest):
        assert url == md.MODEL_SOURCES["nemotron-3.5-streaming-560ms"]
        Path(dest).write_bytes(fixture.read_bytes())
    monkeypatch.setattr(md, "_download", _fake_download)

    rc = main(["download-model", "nemotron-3.5-streaming-560ms"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(md.model_dir("nemotron-3.5-streaming-560ms")) in out


def test_download_model_standard_license_prints_no_notice(monkeypatch, capsys):
    import whisper_aloud.stt.model_download as md
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", "tiny"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "License notice" not in out


def test_download_model_accept_license_is_a_no_op_for_non_review_models(monkeypatch, capsys):
    """--accept-license on an ordinary model is harmless (no gate to pass)."""
    import whisper_aloud.stt.model_download as md
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", "parakeet-tdt-0.6b-v3", "--accept-license"])
    assert rc == 0


# ─── WHISP-63: daemon-start bus-name race ──────────────────────────────────


class TestHandleDaemonCommandBusRace:
    """handle_daemon_command must check/claim the D-Bus name *before*
    constructing WhisperAloudService, so a losing start never pays for
    audio/tray/model init just to die on `bus.publish()`."""

    def test_already_running_exits_with_dedicated_code_and_skips_service(self):
        from whisper_aloud import __main__ as cli
        from whisper_aloud.service.daemon import AlreadyRunningError, EXIT_ALREADY_RUNNING

        fake_bus = MagicMock()
        err = AlreadyRunningError(
            "another instance owns org.fede.whisperaloud (pid 123)", pid=123
        )
        with patch("pydbus.SessionBus", return_value=fake_bus), \
             patch(
                 "whisper_aloud.service.daemon.acquire_bus_name", side_effect=err
             ) as mock_acquire, \
             patch("whisper_aloud.service.WhisperAloudService") as mock_service_cls:
            rc = cli.handle_daemon_command(MagicMock())

        assert rc == EXIT_ALREADY_RUNNING
        mock_acquire.assert_called_once_with(fake_bus)
        mock_service_cls.assert_not_called()

    def test_already_running_message_has_no_traceback(self, capsys):
        from whisper_aloud import __main__ as cli
        from whisper_aloud.service.daemon import AlreadyRunningError

        fake_bus = MagicMock()
        err = AlreadyRunningError("another instance owns org.fede.whisperaloud (pid 123)", pid=123)
        with patch("pydbus.SessionBus", return_value=fake_bus), \
             patch("whisper_aloud.service.daemon.acquire_bus_name", side_effect=err), \
             patch("whisper_aloud.service.WhisperAloudService"):
            cli.handle_daemon_command(MagicMock())

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "org.fede.whisperaloud" in captured.err

    def test_name_acquired_then_service_constructed_with_bus_and_owner(self):
        from whisper_aloud import __main__ as cli

        fake_bus = MagicMock()
        fake_owner = MagicMock()
        with patch("pydbus.SessionBus", return_value=fake_bus), \
             patch(
                 "whisper_aloud.service.daemon.acquire_bus_name", return_value=fake_owner
             ) as mock_acquire, \
             patch("whisper_aloud.service.WhisperAloudService") as mock_service_cls:
            mock_service_cls.return_value.run.return_value = None
            rc = cli.handle_daemon_command(MagicMock())

        assert rc == 0
        mock_acquire.assert_called_once_with(fake_bus)
        mock_service_cls.assert_called_once_with(bus=fake_bus, name_owner=fake_owner)
        mock_service_cls.return_value.run.assert_called_once()
