"""Unit tests for WhisperAloudService.TranscribeFile (WHISP-96).

Uses tests/_daemon_harness.py's make_daemon(), which mocks every process
boundary (D-Bus, GLib, audio, history, engine construction) and runs
executor jobs inline (see _SyncExecutor), so these tests exercise the real
TranscribeFile()/_run_file_transcription() code paths without any GTK/D-Bus/
model runtime dependency.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests._daemon_harness import make_daemon


@pytest.fixture
def daemon():
    service = make_daemon()
    yield service
    service._patches.close()


def _result(text="hello world", language="en", confidence=0.95, duration=2.5):
    result = MagicMock()
    result.text = text
    result.language = language
    result.confidence = confidence
    result.duration = duration
    result.processing_time = 0.1
    result.segments = []
    return result


def test_invalid_path_returns_empty_job_id_and_emits_error(daemon, tmp_path):
    missing = tmp_path / "does-not-exist.wav"

    job_id = daemon.TranscribeFile(str(missing), {})

    assert job_id == ""
    daemon.Error.assert_called_once()
    code, _message = daemon.Error.call_args[0]
    assert code == "file_transcription_invalid_path"
    daemon.history_manager.add_transcription.assert_not_called()


def test_empty_path_rejected_without_touching_filesystem(daemon):
    job_id = daemon.TranscribeFile("", {})

    assert job_id == ""
    daemon.Error.assert_called_once()


def test_success_emits_ready_signal_and_writes_tagged_history(daemon, tmp_path):
    audio_file = tmp_path / "memo.wav"
    audio_file.write_bytes(b"RIFF....fake-wav-bytes")

    daemon.transcriber.transcribe_file = MagicMock(return_value=_result("hello world"))
    daemon.history_manager.add_transcription = MagicMock(return_value=42)

    job_id = daemon.TranscribeFile(str(audio_file), {})

    assert job_id != ""
    daemon.transcriber.transcribe_file.assert_called_once_with(str(audio_file))

    daemon.history_manager.add_transcription.assert_called_once()
    daemon.history_manager.add_tag.assert_called_once_with(42, "source:memo.wav")

    daemon.FileTranscriptionReady.assert_called_once()
    ready_job_id, text, meta = daemon.FileTranscriptionReady.call_args[0]
    assert ready_job_id == job_id
    assert text == "hello world"
    assert meta["history_id"] == 42
    assert meta["source_name"] == "memo.wav"

    daemon.FileTranscriptionError.assert_not_called()
    # A file job must never perturb the recording state machine.
    daemon.StatusChanged.assert_not_called()


def test_language_option_is_unpacked_and_forwarded(daemon, tmp_path):
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_bytes(b"fake-mp3-bytes")
    daemon.transcriber.transcribe_file = MagicMock(return_value=_result())

    variant = MagicMock()
    variant.unpack.return_value = "es"

    daemon.TranscribeFile(str(audio_file), {"language": variant})

    daemon.transcriber.transcribe_file.assert_called_once_with(
        str(audio_file), language="es"
    )


def test_engine_failure_emits_error_and_writes_no_history(daemon, tmp_path):
    audio_file = tmp_path / "broken.wav"
    audio_file.write_bytes(b"not really audio")
    daemon.transcriber.transcribe_file = MagicMock(side_effect=RuntimeError("boom"))

    job_id = daemon.TranscribeFile(str(audio_file), {})

    assert job_id != ""
    daemon.FileTranscriptionError.assert_called_once()
    error_job_id, code, message = daemon.FileTranscriptionError.call_args[0]
    assert error_job_id == job_id
    assert code == "transcription_failed"
    assert "boom" in message
    daemon.history_manager.add_transcription.assert_not_called()
    daemon.FileTranscriptionReady.assert_not_called()


def test_progress_signal_emitted_before_result(daemon, tmp_path):
    audio_file = tmp_path / "progress.wav"
    audio_file.write_bytes(b"x")
    daemon.transcriber.transcribe_file = MagicMock(return_value=_result())

    daemon.TranscribeFile(str(audio_file), {})

    daemon.FileTranscriptionProgress.assert_called_once()
    job_id_arg, stage = daemon.FileTranscriptionProgress.call_args[0]
    assert stage == "transcribing"
