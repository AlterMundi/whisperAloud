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
        exit_code = main()
        assert exit_code == 1


def test_cli_invalid_model():
    """Test CLI with invalid model name - argparse rejects it."""
    with patch('sys.argv', ['whisper-aloud-transcribe', '--model', 'invalid', 'dummy.wav']):
        with pytest.raises(SystemExit) as excinfo:
            main()
        # argparse exits with code 2 for invalid choices
        assert excinfo.value.code == 2


@patch('whisper_aloud.__main__.Transcriber')
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
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 0
            # Verify transcriber was called
            mock_transcriber.load_model.assert_called_once()
            mock_transcriber.transcribe_file.assert_called_once()


@patch('whisper_aloud.__main__.Transcriber')
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
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 0
            # Verify transcriber was used
            mock_transcriber.load_model.assert_called_once()


@patch('whisper_aloud.__main__.Transcriber')
def test_cli_keyboard_interrupt(mock_transcriber_class):
    """Test CLI handles keyboard interrupt gracefully."""
    mock_transcriber = mock_transcriber_class.return_value
    mock_transcriber.transcribe_file.side_effect = KeyboardInterrupt()

    with patch('sys.argv', ['whisper-aloud-transcribe', 'test.wav']):
        with patch.object(Path, 'exists', return_value=True):
            exit_code = main()
            assert exit_code == 130


def test_cli_uses_correct_bus_name():
    """CLI should use org.fede.whisperaloud bus name."""
    import whisper_aloud.__main__ as cli
    import inspect
    source = inspect.getsource(cli)
    assert "org.fede.whisperaloud" in source
    assert "org.fede.whisperAloud" not in source