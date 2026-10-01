"""Tests for CLI functionality."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from whisper_aloud.__main__ import daemon_main, main, _format_uptime, _format_time_ago, _error
from whisper_aloud.exceptions import WhisperAloudError


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

    def _boom():
        raise RuntimeError("config.json is not valid JSON")

    monkeypatch.setattr(cli.WhisperAloudConfig, "load", staticmethod(_boom))
    with patch('sys.argv', ['whisper-aloud', 'config', 'validate']):
        exit_code = main()
        assert exit_code == 1
        assert "configuration error" in capsys.readouterr().err


def test_config_show_reports_error_on_failure(capsys, monkeypatch):
    """A broken config load surfaces as a CLI error from `config show`."""
    import whisper_aloud.__main__ as cli

    def _boom():
        raise RuntimeError("config.json is not valid JSON")

    monkeypatch.setattr(cli.WhisperAloudConfig, "load", staticmethod(_boom))
    with patch('sys.argv', ['whisper-aloud', 'config', 'show']):
        exit_code = main()
        assert exit_code == 1
        assert "configuration error" in capsys.readouterr().err


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
    """`whisper-aloud models` prints every catalog entry's engine and name."""
    from whisper_aloud.stt import catalog
    rc = main(["models"])
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
