"""Regression tests for the WhisperAloudService recording/transcription state machine.

WHISP-45: covers idle -> recording -> transcribing -> idle, cancel (during recording and
during transcription), transcription error handling, auto-stop at max duration, and
toggling while a previous transcription is still in flight.

New file (per the WHISP-45 brief) rather than additions to tests/test_daemon.py, to avoid
merge conflicts with other in-flight work on that file. Uses tests/_daemon_harness.py so
GainController, MprisController, the indicator, HotkeyManager and notifications are always
mocked — this suite never constructs a real WhisperAloudService.
"""
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests._daemon_harness import make_daemon


@pytest.fixture
def daemon():
    service = make_daemon()
    yield service
    service._patches.close()


def _result(text="hello world", confidence=0.9, duration=1.0, language="en"):
    result = MagicMock()
    result.text = text
    result.confidence = confidence
    result.duration = duration
    result.language = language
    return result


class TestFullLifecycle:
    """idle -> recording -> transcribing -> idle, driven through ToggleRecording."""

    def test_toggle_starts_recording_from_idle(self, daemon):
        daemon.recorder.is_recording = False
        state = daemon.ToggleRecording()
        assert state == "recording"
        daemon.recorder.start.assert_called_once()
        daemon.StatusChanged.assert_called_with("recording")

    def test_toggle_stops_recording_and_enters_transcribing(self, daemon):
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.transcriber.transcribe_numpy.return_value = _result()
        daemon.history_manager.add_transcription.return_value = 7

        state = daemon.ToggleRecording()

        assert state == "transcribing"
        assert daemon._transcribing is False  # synchronous executor + idle_add runs inline
        daemon.StatusChanged.assert_any_call("transcribing")
        daemon.StatusChanged.assert_any_call("idle")
        daemon.TranscriptionReady.assert_called_once()
        emitted_text, meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "hello world"
        assert meta["history_id"] == 7

    def test_full_round_trip_returns_to_idle_ready_to_record_again(self, daemon):
        # Start
        daemon.recorder.is_recording = False
        assert daemon.ToggleRecording() == "recording"

        # Stop -> transcribe (synchronous executor/idle_add in the harness)
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.transcriber.transcribe_numpy.return_value = _result(text="done")
        daemon.history_manager.add_transcription.return_value = 1
        assert daemon.ToggleRecording() == "transcribing"
        assert daemon._transcribing is False

        # A fresh toggle should be able to start recording again.
        daemon.recorder.is_recording = False
        daemon.recorder.start.reset_mock()
        assert daemon.ToggleRecording() == "recording"
        daemon.recorder.start.assert_called_once()


class TestCancel:
    def test_cancel_during_recording_returns_to_idle(self, daemon):
        daemon.recorder.is_recording = True
        result = daemon.CancelRecording()
        assert result is True
        daemon.recorder.cancel.assert_called_once()
        daemon.StatusChanged.assert_called_with("idle")

    def test_cancel_during_transcribing_returns_to_idle(self, daemon):
        daemon.recorder.is_recording = False
        daemon._transcribing = True
        result = daemon.CancelRecording()
        assert result is True
        daemon.transcriber.cancel_transcription.assert_called_once()
        assert daemon._transcribing is False
        daemon.StatusChanged.assert_called_with("idle")

    def test_cancel_when_fully_idle_is_a_noop(self, daemon):
        daemon.recorder.is_recording = False
        daemon._transcribing = False
        result = daemon.CancelRecording()
        assert result is False
        daemon.recorder.cancel.assert_not_called()


class TestTranscriptionError:
    def test_transcription_failure_emits_error_and_returns_to_idle(self, daemon):
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.transcriber.transcribe_numpy.side_effect = RuntimeError("model exploded")

        state = daemon.ToggleRecording()

        assert state == "transcribing"  # StopRecording's immediate return value
        assert daemon._transcribing is False
        daemon.Error.assert_called_once()
        error_code, error_message = daemon.Error.call_args[0]
        assert error_code == "transcription_failed"
        assert "model exploded" in error_message
        daemon.StatusChanged.assert_any_call("idle")
        # No TranscriptionReady on failure
        daemon.TranscriptionReady.assert_not_called()

    def test_history_save_failure_does_not_block_transcription_ready(self, daemon):
        """A broken history DB must not swallow the TranscriptionReady signal."""
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.transcriber.transcribe_numpy.return_value = _result(text="saved anyway")
        daemon.history_manager.add_transcription.side_effect = RuntimeError("disk full")

        daemon.ToggleRecording()

        daemon.TranscriptionReady.assert_called_once()
        emitted_text, meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "saved anyway"
        assert meta["history_id"] == -1


class TestAutoStop:
    """_on_recorder_auto_stop fires from AudioRecorder when max_recording_duration hits."""

    def test_auto_stop_transitions_to_transcribing_then_idle(self, daemon):
        daemon.transcriber.transcribe_numpy.return_value = _result(text="auto stopped")
        daemon.history_manager.add_transcription.return_value = 3

        audio = np.zeros(16000, dtype=np.float32)
        daemon._on_recorder_auto_stop(audio)

        daemon.RecordingStopped.assert_called_once()
        daemon.StatusChanged.assert_any_call("transcribing")
        daemon.StatusChanged.assert_any_call("idle")
        daemon.TranscriptionReady.assert_called_once()
        emitted_text, _meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "auto stopped"
        assert daemon._transcribing is False

    def test_auto_stop_restores_gain_and_resumes_media(self, daemon):
        daemon.config.recording_flow.pause_media = True
        daemon.transcriber.transcribe_numpy.return_value = _result()
        daemon.history_manager.add_transcription.return_value = 1

        daemon._on_recorder_auto_stop(np.zeros(1600, dtype=np.float32))

        daemon._gain.restore.assert_called_once()
        daemon._mpris.resume_ours.assert_called_once()

    def test_auto_stop_survives_gain_restore_failure(self, daemon):
        """A broken GainController must not prevent the stop/transcribe flow from completing."""
        daemon._gain.restore.side_effect = RuntimeError("wpctl vanished")
        daemon.transcriber.transcribe_numpy.return_value = _result(text="still works")
        daemon.history_manager.add_transcription.return_value = 2

        daemon._on_recorder_auto_stop(np.zeros(1600, dtype=np.float32))

        daemon.TranscriptionReady.assert_called_once()


class TestToggleWhileTranscribing:
    """WHISP-57 audit (HIGH): a Stop->Start double-tap while the previous
    recording's transcription is still in flight on the executor must not
    start a new recording -- doing so could run engine.start_session() for
    a new session while the old one's finalize()/transcribe_numpy() is still
    touching the same engine instance's shared state."""

    def test_toggle_while_transcribing_does_not_start_a_new_recording(self, daemon):
        """Once StopRecording returns, recorder.is_recording is False again even
        though _transcribing is still True (the previous job is running in the
        executor). A toggle at that point must not call recorder.start() --
        StartRecording's _transcribing guard blocks it.
        """
        daemon.recorder.is_recording = False
        daemon._transcribing = True
        daemon.recorder.start.reset_mock()

        daemon.ToggleRecording()

        daemon.recorder.start.assert_not_called()
        # The in-flight transcription flag is untouched.
        assert daemon._transcribing is True

    def test_start_recording_returns_false_while_transcribing(self, daemon):
        daemon._transcribing = True
        daemon.RecordingStarted.reset_mock()
        daemon.StatusChanged.reset_mock()

        result = daemon.StartRecording()

        assert result is False
        daemon.recorder.start.assert_not_called()
        daemon.RecordingStarted.assert_not_called()
        daemon.StatusChanged.assert_not_called()
        daemon.Error.assert_not_called()
