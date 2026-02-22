"""Tests for CLI functionality."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from whisper_aloud.__main__ import daemon_main, main, _format_uptime, _format_time_ago, _error


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


# ─── Daemon commands ──────────────────────────────────────────────────────────


@patch('whisper_aloud.__main__.check_service_running', return_value=False)
def test_daemon_command_when_not_running(mock_check, capsys):
    """Daemon commands error when service not running."""
    with patch('sys.argv', ['whisper-aloud', 'start']):
        exit_code = main()
        assert exit_code == 1
        assert "not running" in capsys.readouterr().err


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
def test_start_command(mock_check, mock_call, capsys):
    """Start command calls daemon and prints confirmation."""
    with patch('sys.argv', ['whisper-aloud', 'start']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("StartRecording")
        assert "Recording started" in capsys.readouterr().out


@patch('whisper_aloud.__main__.call_service_method')
@patch('whisper_aloud.__main__.check_service_running', return_value=True)
def test_quit_command(mock_check, mock_call, capsys):
    """Quit command calls daemon Quit."""
    with patch('sys.argv', ['whisper-aloud', 'quit']):
        exit_code = main()
        assert exit_code == 0
        mock_call.assert_called_once_with("Quit")
        assert "Daemon stopped" in capsys.readouterr().out


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
