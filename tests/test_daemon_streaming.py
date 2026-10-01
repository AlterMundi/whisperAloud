"""Daemon streaming-session tests (WHISP-57).

Covers the daemon's behaviour when the configured engine `is_streaming`:
StartRecording wiring (start_session + on_processed_chunk hook + partial
timer), the mandatory recorder.stop() -> engine.finalize() order, the
partial-transcript throttle (only emit on seq change), the cancel path
(recorder.cancel() -> engine.cancel_session()), session_id propagation into
both TranscriptionReady and PartialTranscript, and that a non-streaming
engine's behaviour is unaffected.

Uses tests/_daemon_harness.py so GainController, MprisController, the
indicator, HotkeyManager and notifications are always mocked — this suite
never constructs a real WhisperAloudService.
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


class _PartialResult:
    def __init__(self, committed, tentative, seq):
        self.committed = committed
        self.tentative = tentative
        self.seq = seq


class _StreamingEngineDouble:
    """A minimal StreamingSpeechEngine test double with inspectable call order."""

    is_streaming = True

    def __init__(self):
        self.order: list = []
        self.load_model = MagicMock()
        self.start_session = MagicMock(side_effect=lambda language: self.order.append("start_session"))
        self.accept_chunk = MagicMock()
        self.cancel_session = MagicMock(side_effect=lambda: self.order.append("cancel_session"))
        self._partials: list = []
        self._partial_index = 0
        self.finalize = MagicMock(side_effect=self._do_finalize)
        self._finalize_result = _result(text="streamed result")

    def queue_partials(self, partials):
        self._partials = partials
        self._partial_index = 0

    def get_partial(self):
        if self._partial_index < len(self._partials):
            p = self._partials[self._partial_index]
            self._partial_index += 1
            return p
        if self._partials:
            return self._partials[-1]
        return _PartialResult("", "", 0)

    def _do_finalize(self):
        self.order.append("finalize")
        return self._finalize_result


def _result(text="hello world", confidence=0.9, duration=1.0, language="en"):
    result = MagicMock()
    result.text = text
    result.confidence = confidence
    result.duration = duration
    result.language = language
    return result


def _install_streaming_engine(daemon_obj) -> _StreamingEngineDouble:
    engine = _StreamingEngineDouble()
    daemon_obj.transcriber = engine
    return engine


class TestStreamingStartRecording:
    def test_start_recording_starts_engine_session_and_wires_hook(self, daemon):
        engine = _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False

        state = daemon.ToggleRecording()

        assert state == "recording"
        engine.start_session.assert_called_once_with(daemon.config.transcription.language)
        assert daemon.recorder.on_processed_chunk is engine.accept_chunk
        assert daemon._streaming_session is True
        assert daemon._current_session_id

    def test_start_recording_loads_model_before_session(self, daemon):
        engine = _install_streaming_engine(daemon)
        daemon._model_loaded = False
        daemon.recorder.is_recording = False

        daemon.ToggleRecording()

        assert daemon._model_loaded is True

    def test_start_recording_starts_partial_timer(self, daemon):
        _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon._mock_glib.timeout_add.reset_mock()

        daemon.ToggleRecording()

        daemon._mock_glib.timeout_add.assert_any_call(
            daemon._daemon_module.PARTIAL_TRANSCRIPT_INTERVAL_MS,
            daemon._emit_partial_transcript,
        )

    def test_non_streaming_engine_unaffected(self, daemon):
        """A plain (non-streaming) transcriber double must not trip the streaming
        path, even though MagicMock auto-creates a truthy `.is_streaming`
        attribute by default -- the daemon must check `is True`, not truthiness."""
        daemon.recorder.is_recording = False

        daemon.ToggleRecording()

        assert daemon._streaming_session is False
        assert daemon.recorder.on_processed_chunk is None
        daemon.transcriber.start_session.assert_not_called()


class TestStreamingPartialThrottle:
    def test_emits_only_when_seq_changes(self, daemon):
        engine = _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.ToggleRecording()
        daemon.PartialTranscript.reset_mock()

        engine.queue_partials(
            [
                _PartialResult("", "the", 1),
                _PartialResult("", "the", 1),  # repeated seq -> no emission
                _PartialResult("the", "quick", 2),
                _PartialResult("the", "quick", 2),  # repeated again
                _PartialResult("the quick", "brown", 3),
            ]
        )

        for _ in range(5):
            daemon._emit_partial_transcript()

        assert daemon.PartialTranscript.call_count == 3
        calls = [c.args for c in daemon.PartialTranscript.call_args_list]
        seqs = [c[3] for c in calls]
        assert seqs == [1, 2, 3]
        session_ids = {c[0] for c in calls}
        assert session_ids == {daemon._current_session_id}
        assert calls[-1][1] == "the quick"  # committed grows
        assert calls[-1][2] == "brown"


class TestStreamingStopRecording:
    def test_stop_calls_recorder_stop_before_engine_finalize(self, daemon):
        engine = _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.recorder.stop = MagicMock(
            side_effect=lambda: engine.order.append("recorder_stop") or np.zeros(16000, dtype=np.float32)
        )
        daemon.history_manager.add_transcription.return_value = 9
        daemon.ToggleRecording()  # idle -> recording (sets up streaming session)
        daemon.recorder.is_recording = True

        state = daemon.StopRecording()

        assert state == "transcribing"
        assert engine.order == ["start_session", "recorder_stop", "finalize"]
        daemon.TranscriptionReady.assert_called_once()
        emitted_text, meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "streamed result"

    def test_transcription_ready_meta_includes_session_id(self, daemon):
        _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.history_manager.add_transcription.return_value = 1
        daemon.ToggleRecording()
        session_id = daemon._current_session_id
        daemon.recorder.is_recording = True

        daemon.StopRecording()

        _text, meta = daemon.TranscriptionReady.call_args[0]
        assert meta["session_id"] == session_id

    def test_stop_recording_stops_partial_timer(self, daemon):
        _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.history_manager.add_transcription.return_value = 1
        daemon.ToggleRecording()
        daemon.recorder.is_recording = True

        daemon._mock_glib.source_remove.reset_mock()
        daemon.StopRecording()

        daemon._mock_glib.source_remove.assert_called()
        assert daemon._partial_timer_id is None

    def test_dropped_chunks_read_before_recorder_stop(self, daemon):
        """AudioRecorder.dropped_chunks resets once its chunk session is torn
        down (which stop() does internally), so it must be read before stop()
        is called, not after."""
        _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.recorder.dropped_chunks = 3
        daemon.history_manager.add_transcription.return_value = 1

        def _stop():
            # Simulate AudioRecorder.stop() resetting the counter once its
            # session is torn down.
            daemon.recorder.dropped_chunks = 0
            return np.zeros(16000, dtype=np.float32)

        daemon.ToggleRecording()
        daemon.recorder.is_recording = True
        daemon.recorder.stop = MagicMock(side_effect=_stop)

        daemon.StopRecording()

        _text, meta = daemon.TranscriptionReady.call_args[0]
        assert meta["dropped_chunks"] == 3

    def test_non_streaming_session_id_in_meta_too(self, daemon):
        """Additive change: even the plain batch path now includes session_id
        in TranscriptionReady's meta, for client-side correlation."""
        daemon.recorder.is_recording = False
        daemon.ToggleRecording()
        session_id = daemon._current_session_id
        daemon.recorder.is_recording = True
        daemon.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
        daemon.transcriber.transcribe_numpy.return_value = _result()
        daemon.history_manager.add_transcription.return_value = 1

        daemon.StopRecording()

        _text, meta = daemon.TranscriptionReady.call_args[0]
        assert meta["session_id"] == session_id


class TestStreamingCancel:
    def test_cancel_during_recording_calls_cancel_session_after_recorder_cancel(self, daemon):
        engine = _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.recorder.cancel = MagicMock(side_effect=lambda: engine.order.append("recorder_cancel"))
        daemon.ToggleRecording()
        daemon.recorder.is_recording = True

        result = daemon.CancelRecording()

        assert result is True
        assert engine.order == ["start_session", "recorder_cancel", "cancel_session"]
        assert daemon._streaming_session is False

    def test_cancel_during_recording_stops_partial_timer(self, daemon):
        _install_streaming_engine(daemon)
        daemon.recorder.is_recording = False
        daemon.ToggleRecording()
        daemon.recorder.is_recording = True

        daemon._mock_glib.source_remove.reset_mock()
        daemon.CancelRecording()

        daemon._mock_glib.source_remove.assert_called()

    def test_non_streaming_cancel_does_not_touch_cancel_session(self, daemon):
        daemon.recorder.is_recording = True
        daemon.CancelRecording()
        assert not hasattr(daemon.transcriber, "cancel_session") or (
            not daemon.transcriber.cancel_session.called
        )
