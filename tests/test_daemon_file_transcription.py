"""Unit tests for WhisperAloudService.TranscribeFile (WHISP-96).

Uses tests/_daemon_harness.py's make_daemon(), which mocks every process
boundary (D-Bus, GLib, audio, history, engine construction) and runs
executor jobs inline (see _SyncExecutor), so these tests exercise the real
TranscribeFile()/_run_file_transcription() code paths without any GTK/D-Bus/
model runtime dependency.

The model-readiness gating tests near the bottom (TestModelGateConsistency)
pin TranscribeFile() to the same contract StartRecording()/_transcribe_and_emit
already have (see tests/test_daemon_model_preload.py's TestBatchJobWaitsForReady,
which this mirrors for the file-transcription path): `missing`/`error` fails
fast with Error "model_not_available"; `loading` blocks the job (on the
executor thread, via the ready EVENT, never a poll) until the load settles.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from tests._daemon_harness import make_daemon
from whisper_aloud.service import model_state as ms


def _available(daemon_obj, value: bool):
    """Patch the offline availability check on the module object the daemon
    under test actually uses (the harness re-imports the daemon module)."""
    return patch.object(
        daemon_obj._daemon_module.model_state, "is_model_available", return_value=value
    )


@pytest.fixture
def daemon():
    service = make_daemon()
    yield service
    service._model_state.close()
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


class _GatedFileEngine:
    """Batch engine whose load_model() blocks on `gate`, like
    test_daemon_model_preload.py's _GatedEngine but with `transcribe_file`
    instead of `transcribe_numpy`."""

    is_streaming = False

    def __init__(self, gate: threading.Event | None = None):
        self.gate = gate
        self.load_entered = threading.Event()
        self.transcribe_file = MagicMock(return_value=_result("from engine"))

    def load_model(self):
        self.load_entered.set()
        if self.gate is not None and not self.gate.wait(timeout=10):
            raise TimeoutError("test gate never released")


class TestModelGateConsistency:
    """TranscribeFile() must handle model readiness exactly like the
    recording path (StartRecording -> _transcribe_and_emit): missing/error
    fails fast with Error("model_not_available", ...); loading blocks the
    job -- on the executor thread, via the ready event -- until the load
    settles."""

    def test_waits_for_loading_model_via_ready_event_then_succeeds(self, daemon, tmp_path):
        audio_file = tmp_path / "clip.wav"
        audio_file.write_bytes(b"x")
        gate = threading.Event()
        engine = _GatedFileEngine(gate=gate)
        daemon.transcriber = engine
        daemon.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-wa-file")

        waiting = threading.Event()
        real_wait = daemon._model_state.wait_while_loading

        def _spy_wait(*a, **kw):
            waiting.set()
            return real_wait(*a, **kw)

        with _available(daemon, True):
            daemon._start_model_load()
            assert engine.load_entered.wait(timeout=10)

            with patch.object(
                daemon._model_state, "wait_while_loading", side_effect=_spy_wait
            ):
                job_id = daemon.TranscribeFile(str(audio_file), {})
                assert job_id != ""
                assert waiting.wait(timeout=10), "file job never blocked on the ready event"
                engine.transcribe_file.assert_not_called()

                gate.set()  # model finishes loading -> job proceeds
                daemon.executor.shutdown(wait=True)

        engine.transcribe_file.assert_called_once()
        daemon.FileTranscriptionReady.assert_called_once()
        daemon.FileTranscriptionError.assert_not_called()

    def test_rejected_fast_when_model_missing(self, daemon, tmp_path):
        audio_file = tmp_path / "clip.wav"
        audio_file.write_bytes(b"x")
        engine = _GatedFileEngine()
        daemon.transcriber = engine
        daemon._model_state.set_state(ms.MISSING, "Model 'base' is not downloaded")

        with _available(daemon, False):
            job_id = daemon.TranscribeFile(str(audio_file), {})

        assert job_id != ""  # the job starts; it fails asynchronously inside it
        engine.transcribe_file.assert_not_called()
        daemon.FileTranscriptionError.assert_called_once()
        _job_id, code, message = daemon.FileTranscriptionError.call_args[0]
        assert code == "model_not_available"
        assert "base" in message
        daemon.history_manager.add_transcription.assert_not_called()
        daemon.FileTranscriptionReady.assert_not_called()
        # Consistent with StartRecording's refusal: never perturbs the
        # recording state machine/status.
        daemon.StatusChanged.assert_not_called()

    def test_rejected_fast_when_model_errored(self, daemon, tmp_path):
        audio_file = tmp_path / "clip.wav"
        audio_file.write_bytes(b"x")
        engine = _GatedFileEngine()
        daemon.transcriber = engine
        daemon._model_state.set_state(ms.ERROR, "CUDA exploded")

        with _available(daemon, True):
            daemon.TranscribeFile(str(audio_file), {})

        engine.transcribe_file.assert_not_called()
        _job_id, code, message = daemon.FileTranscriptionError.call_args[0]
        assert code == "model_not_available"
        assert "CUDA exploded" in message
