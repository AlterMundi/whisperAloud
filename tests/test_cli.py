"""Tests for CLI functionality."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from whisper_aloud.__main__ import main


def test_cli_help():
    """Test CLI help output."""
    with patch('sys.argv', ['whisper-aloud-transcribe', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_cli_version():
    """Test CLI version output."""
    with patch('sys.argv', ['whisper-aloud-transcribe', '--version']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_cli_missing_file():
    """Test CLI with missing audio file."""
    with patch('sys.argv', ['whisper-aloud-transcribe', 'nonexistent.wav']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 1


def test_cli_invalid_model():
    """Test CLI with invalid model name."""
    with patch('sys.argv', ['whisper-aloud-transcribe', '--model', 'invalid', 'dummy.wav']):
        with patch('pathlib.Path.exists', return_value=True):
            with pytest.raises(SystemExit) as excinfo:
                main()
            assert excinfo.value.code == 1


@patch('whisper_aloud.transcriber.Transcriber')
def test_cli_success_flow(mock_transcriber_class):
    """Test successful CLI transcription flow."""
    # Mock the transcriber and its result
    mock_transcriber = mock_transcriber_class.return_value
    mock_result = mock_transcriber.transcribe_file.return_value
    mock_result.text = "Hello world"
    mock_result.language = "en"
    mock_result.duration = 2.5
    mock_result.processing_time = 1.2
    mock_result.confidence = 0.95

    with patch('sys.argv', ['whisper-aloud-transcribe', 'test.wav']):
        with patch('pathlib.Path.exists', return_value=True):
            with patch('sys.stdout') as mock_stdout:
                with patch('sys.stderr') as mock_stderr:
                    exit_code = main()
                    assert exit_code == 0

                    # Check that output was written to stdout
                    mock_stdout.write.assert_called_with("Hello world")


@patch('whisper_aloud.transcriber.Transcriber')
def test_cli_verbose_output(mock_transcriber_class):
    """Test CLI verbose output."""
    # Mock the transcriber and its result
    mock_transcriber = mock_transcriber_class.return_value
    mock_result = mock_transcriber.transcribe_file.return_value
    mock_result.text = "Test transcription"
    mock_result.language = "es"
    mock_result.duration = 3.0
    mock_result.processing_time = 2.1
    mock_result.confidence = 0.87

    with patch('sys.argv', ['whisper-aloud-transcribe', '--verbose', 'test.wav']):
        with patch('pathlib.Path.exists', return_value=True):
            with patch('sys.stdout') as mock_stdout:
                with patch('sys.stderr') as mock_stderr:
                    exit_code = main()
                    assert exit_code == 0

                    # Check that metadata was written to stderr
                    stderr_calls = [call.args[0] for call in mock_stderr.write.call_args_list]
                    assert any("Language: es" in call for call in stderr_calls)
                    assert any("Duration: 3.00s" in call for call in stderr_calls)
                    assert any("Processing time: 2.10s" in call for call in stderr_calls)
                    assert any("Confidence: 87.0%" in call for call in stderr_calls)


def test_cli_keyboard_interrupt():
    """Test CLI handles keyboard interrupt gracefully."""
    with patch('sys.argv', ['whisper-aloud-transcribe', 'test.wav']):
        with patch('pathlib.Path.exists', return_value=True):
            with patch('whisper_aloud.transcriber.Transcriber') as mock_transcriber:
                mock_instance = mock_transcriber.return_value
                mock_instance.transcribe_file.side_effect = KeyboardInterrupt()

                with patch('sys.stderr') as mock_stderr:
                    exit_code = main()
                    assert exit_code == 130

                    # Check error message
                    stderr_calls = [call.args[0] for call in mock_stderr.write.call_args_list]
                    assert any("Interrupted by user" in call for call in stderr_calls)