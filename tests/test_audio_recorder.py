"""Tests for audio recorder."""

import time
from unittest.mock import Mock, patch

import numpy as np
import pytest

from whisper_aloud.audio import AudioRecorder, RecordingState
from whisper_aloud.config import AudioConfig, AudioProcessingConfig
from whisper_aloud.exceptions import AudioRecordingError


def test_recorder_initialization():
    """Test recorder initializes correctly."""
    config = AudioConfig()
    recorder = AudioRecorder(config)

    assert recorder.state == RecordingState.IDLE
    assert recorder.recording_duration == 0.0
    assert not recorder.is_recording


def test_recorder_with_callback():
    """Test recorder with level callback."""
    config = AudioConfig()
    callback_called = False

    def level_callback(level):
        nonlocal callback_called
        callback_called = True

    AudioRecorder(config, level_callback=level_callback)
    assert not callback_called  # Not called during init


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_start_stop_recording(mock_stream, mock_validate):
    """Test start/stop cycle."""
    # Mock device
    mock_device = Mock()
    mock_device.name = "Test Mic"
    mock_validate.return_value = mock_device

    # Mock stream
    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    config = AudioConfig()
    recorder = AudioRecorder(config)

    # Start recording
    recorder.start()
    assert recorder.state == RecordingState.RECORDING

    # Simulate some frames
    recorder._frames = [np.zeros(1600, dtype=np.float32) for _ in range(10)]

    # Stop recording
    audio = recorder.stop()
    assert isinstance(audio, np.ndarray)
    assert recorder.state == RecordingState.IDLE

    # Verify stream operations
    mock_stream_instance.start.assert_called_once()
    mock_stream_instance.stop.assert_called_once()
    mock_stream_instance.close.assert_called_once()


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_start_recording_device_validation_failure(mock_stream, mock_validate):
    """Test start fails when device validation fails."""
    mock_validate.side_effect = Exception("Device error")

    config = AudioConfig()
    recorder = AudioRecorder(config)

    with pytest.raises(Exception):  # Should propagate device error
        recorder.start()

    assert recorder.state == RecordingState.ERROR


def test_stop_without_start():
    """Test stopping when not recording."""
    config = AudioConfig()
    recorder = AudioRecorder(config)

    with pytest.raises(AudioRecordingError, match="Cannot stop"):
        recorder.stop()


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_cancel_recording(mock_stream, mock_validate):
    """Test cancelling recording."""
    mock_device = Mock()
    mock_validate.return_value = mock_device

    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    config = AudioConfig()
    recorder = AudioRecorder(config)

    recorder.start()
    recorder._frames = [np.ones(100, dtype=np.float32)]  # Add some data

    recorder.cancel()

    assert recorder.state == RecordingState.IDLE
    assert len(recorder._frames) == 0  # Data should be cleared
    mock_stream_instance.stop.assert_called_once()
    mock_stream_instance.close.assert_called_once()


def test_pause_resume():
    """Test pause and resume functionality."""
    config = AudioConfig()
    recorder = AudioRecorder(config)

    # Can't pause when not recording
    recorder.pause()
    assert recorder.state == RecordingState.IDLE

    # Simulate recording state
    recorder._set_state(RecordingState.RECORDING)
    recorder.pause()
    assert recorder.state == RecordingState.PAUSED

    recorder.resume()
    assert recorder.state == RecordingState.RECORDING


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_max_duration_enforcement(mock_stream, mock_validate):
    """Test max recording duration is enforced."""
    mock_device = Mock()
    mock_validate.return_value = mock_device

    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    config = AudioConfig(max_recording_duration=1.0)  # 1 second max
    recorder = AudioRecorder(config)

    recorder.start()

    # Simulate time passing
    recorder._start_time = 0  # Mock start time

    with patch('time.time', return_value=2.0):  # 2 seconds later
        # Call audio callback — triggers STOPPING + spawns auto-stop thread
        recorder._audio_callback(np.zeros((1, 160), dtype=np.float32), 160, None, None)

    # Immediately after callback, state must be at least STOPPING (not RECORDING)
    assert recorder.state in (RecordingState.STOPPING, RecordingState.IDLE)


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_stop_with_no_frames(mock_stream, mock_validate):
    """Test stopping when no frames were recorded."""
    mock_device = Mock()
    mock_validate.return_value = mock_device

    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    config = AudioConfig()
    recorder = AudioRecorder(config)

    recorder.start()
    # Don't add any frames

    audio = recorder.stop()

    assert isinstance(audio, np.ndarray)
    assert len(audio) == 0


@patch('whisper_aloud.audio.audio_processor.AudioPipeline.process')
@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_stop_processing_failure(mock_stream, mock_validate, mock_pipeline_process):
    """Test stop handles processing failures."""
    mock_device = Mock()
    mock_validate.return_value = mock_device

    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    # Mock processing failure
    mock_pipeline_process.side_effect = Exception("Processing error")

    config = AudioConfig()
    recorder = AudioRecorder(config)

    recorder.start()
    recorder._frames = [np.ones(100, dtype=np.float32)]

    with pytest.raises(AudioRecordingError, match="Failed to stop"):
        recorder.stop()

    assert recorder.state == RecordingState.ERROR


def test_recording_duration_calculation():
    """Test recording duration calculation."""
    config = AudioConfig()
    recorder = AudioRecorder(config)

    # No start time
    assert recorder.recording_duration == 0.0

    # With start time
    recorder._start_time = 100.0
    with patch('time.time', return_value=105.5):
        assert recorder.recording_duration == 5.5


# ── C2 + H3 tests ────────────────────────────────────────────────────────────

def test_stopping_state_exists():
    """RecordingState must include STOPPING transitional state."""
    assert hasattr(RecordingState, 'STOPPING')
    assert RecordingState.STOPPING.value == "stopping"


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_stop_closes_stream_before_concatenating_frames(mock_stream, mock_validate):
    """Stream must be closed before frames are read — no callback can append after close."""
    mock_device = Mock()
    mock_device.name = "Test Mic"
    mock_validate.return_value = mock_device

    close_called_before_concat = []
    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    config = AudioConfig()
    recorder = AudioRecorder(config)
    recorder.start()

    # Inject one frame so stop() has data to process
    recorder._frames = [np.zeros(160, dtype=np.float32)]

    # Patch np.concatenate to detect when it's called relative to stream.close()
    import numpy as _np
    original_concat = _np.concatenate

    def patched_concat(arrays, *args, **kwargs):
        # At the moment of concatenation, stream should already be closed
        close_called_before_concat.append(mock_stream_instance.close.called)
        return original_concat(arrays, *args, **kwargs)

    with patch('whisper_aloud.audio.recorder.np.concatenate', side_effect=patched_concat):
        recorder.stop()

    assert close_called_before_concat, "np.concatenate was never called"
    assert all(close_called_before_concat), "Stream was not closed before frames were concatenated"


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_max_duration_triggers_full_stop_path(mock_stream, mock_validate):
    """Max-duration reached in callback must result in stream closed and state IDLE."""
    import time as _time
    mock_device = Mock()
    mock_device.name = "Test Mic"
    mock_validate.return_value = mock_device

    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    config = AudioConfig(max_recording_duration=0.01)
    recorder = AudioRecorder(config)
    recorder.start()
    recorder._start_time = _time.time() - 1.0  # force elapsed > max
    recorder._frames = [np.zeros(160, dtype=np.float32)]

    # Trigger callback — should schedule auto-stop thread
    recorder._audio_callback(np.zeros((160, 1), dtype=np.float32), 160, None, None)

    # Wait for the auto-stop thread to complete
    _time.sleep(0.3)

    assert recorder.state == RecordingState.IDLE, f"Expected IDLE, got {recorder.state}"
    assert recorder._stream is None, "Stream was not closed by auto-stop"


# ── WHISP-65: on_processed_chunk streaming hook ─────────────────────────────


def _feed_chunks(recorder: AudioRecorder, n: int, chunk_samples: int = 1600) -> None:
    """Simulate n audio callback invocations with distinct, identifiable chunks."""
    for i in range(n):
        chunk = np.full((chunk_samples, 1), fill_value=(i + 1) * 0.01, dtype=np.float32)
        recorder._audio_callback(chunk, chunk_samples, None, None)


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_hook_not_called_when_none(mock_stream, mock_validate):
    """No hook registered -> no chunk queue/worker, no calls at all."""
    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    config = AudioConfig()
    recorder = AudioRecorder(config)  # on_processed_chunk defaults to None
    recorder.start()

    assert recorder._chunk_queue is None
    assert recorder._chunk_worker is None

    _feed_chunks(recorder, 3)
    recorder._frames = [np.zeros(1600, dtype=np.float32)]
    recorder.stop()
    # Nothing to assert on a hook that doesn't exist; the key behaviour is
    # that no queue/thread were ever created, and stop() still works.
    assert recorder.state == RecordingState.IDLE


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_hook_receives_chunks_in_order(mock_stream, mock_validate):
    """Chunks must reach the hook in the order the callback produced them."""
    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    received = []
    config = AudioConfig(sample_rate=16000)
    proc_config = AudioProcessingConfig(
        noise_gate_enabled=False, agc_enabled=False, denoising_enabled=False, limiter_enabled=False
    )
    recorder = AudioRecorder(
        config, processing_config=proc_config, on_processed_chunk=lambda c: received.append(c.copy())
    )
    recorder.start()

    assert recorder._chunk_queue is not None
    assert recorder._chunk_worker is not None
    assert recorder._streaming_pipeline is not None
    assert recorder._streaming_resampler is None  # already 16kHz, no resampling needed

    n = 5
    _feed_chunks(recorder, n)

    recorder._frames = [np.zeros(1600, dtype=np.float32)]
    recorder.stop()  # joins the worker thread, draining the queue first

    assert len(received) == n
    for i, chunk in enumerate(received):
        expected_value = (i + 1) * 0.01
        np.testing.assert_allclose(chunk, np.full_like(chunk, expected_value), atol=1e-6)


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_hook_exception_does_not_kill_recording(mock_stream, mock_validate):
    """An exception raised inside the hook must be swallowed, not propagate,
    and must not prevent subsequent chunks or stop() from working."""
    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    calls = []

    def flaky_hook(chunk):
        calls.append(chunk)
        if len(calls) == 2:
            raise RuntimeError("boom")

    config = AudioConfig()
    recorder = AudioRecorder(config, on_processed_chunk=flaky_hook)
    recorder.start()

    _feed_chunks(recorder, 4)

    recorder._frames = [np.zeros(1600, dtype=np.float32)]
    audio = recorder.stop()  # must not raise, worker thread must still be alive/joinable

    assert len(calls) == 4, "hook should still be called for all chunks despite one raising"
    assert isinstance(audio, np.ndarray)
    assert recorder.state == RecordingState.IDLE


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_streaming_pipeline_reset_between_sessions(mock_stream, mock_validate):
    """Each start() must build a fresh per-session streaming pipeline so AGC
    gain / gate state from a previous recording never leaks into the next."""
    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    config = AudioConfig()
    recorder = AudioRecorder(config, on_processed_chunk=lambda c: None)

    recorder.start()
    first_pipeline = recorder._streaming_pipeline
    assert first_pipeline is not None
    recorder._frames = [np.zeros(1600, dtype=np.float32)]
    recorder.stop()
    assert recorder._streaming_pipeline is None  # torn down after stop()

    recorder.start()
    second_pipeline = recorder._streaming_pipeline
    assert second_pipeline is not None
    assert second_pipeline is not first_pipeline
    recorder._frames = [np.zeros(1600, dtype=np.float32)]
    recorder.stop()


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_hook_resamples_when_device_rate_not_16k(mock_stream, mock_validate):
    """A device running at a non-16kHz rate must get a StreamingResampler;
    chunks delivered to the hook should already be at 16kHz."""
    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    received = []
    config = AudioConfig(sample_rate=48000)
    recorder = AudioRecorder(config, on_processed_chunk=lambda c: received.append(c))
    recorder.start()

    assert recorder._streaming_resampler is not None

    chunk_samples = int(0.1 * 48000)  # matches default chunk_duration at 48kHz
    _feed_chunks(recorder, 2, chunk_samples=chunk_samples)

    recorder._frames = [np.zeros(chunk_samples, dtype=np.float32)]
    recorder.stop()

    assert len(received) == 2
    for chunk in received:
        # Resampled 100ms chunk at 48kHz -> 16kHz should be ~1600 samples.
        assert abs(len(chunk) - 1600) <= 2


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_cancel_stops_chunk_worker(mock_stream, mock_validate):
    """cancel() must also tear down the chunk worker, not just stop()."""
    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    config = AudioConfig()
    recorder = AudioRecorder(config, on_processed_chunk=lambda c: None)
    recorder.start()
    assert recorder._chunk_worker is not None

    recorder.cancel()

    assert recorder._chunk_worker is None
    assert recorder._chunk_queue is None
    assert recorder.state == RecordingState.IDLE


# ── WHISP-65 audit: one-shot pipeline state leaking across recordings ───────


@patch('whisper_aloud.audio.device_manager.DeviceManager.validate_device')
@patch('sounddevice.InputStream')
def test_second_recording_matches_a_fresh_pipeline(mock_stream, mock_validate):
    """AudioRecorder reuses one AudioPipeline instance (self._pipeline) across
    many recording sessions (the daemon keeps one AudioRecorder alive for its
    whole lifetime). Before WHISP-65's fix, AGC's rolling RMS history and
    NoiseGate's envelope/hold state from recording N leaked into recording
    N+1's stop() processing (measured max abs diff ~0.281). start() must now
    reset self._pipeline, so recording N+1's output matches what a brand-new
    AudioPipeline would produce on the same audio.
    """
    from whisper_aloud.audio.audio_processor import AudioPipeline

    mock_validate.return_value = Mock()
    mock_stream.return_value = Mock()

    config = AudioConfig(vad_enabled=False)  # keep stop()'s processing deterministic
    proc_config = AudioProcessingConfig(
        noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False, limiter_enabled=True
    )
    recorder = AudioRecorder(config, processing_config=proc_config)

    sr = 16000
    t = np.arange(sr, dtype=np.float32) / sr
    loud_audio = (np.sin(2 * np.pi * 220 * t) * 0.5).astype(np.float32)
    quiet_audio = (np.sin(2 * np.pi * 440 * t) * 0.02).astype(np.float32)

    # First recording: leaves AGC/gate state behind on recorder._pipeline.
    recorder.start()
    recorder._frames = [loud_audio.copy()]
    recorder._start_time = 0.0
    recorder.stop()

    # Second recording on the SAME recorder instance.
    recorder.start()
    recorder._frames = [quiet_audio.copy()]
    recorder._start_time = 0.0
    second_output = recorder.stop()

    expected = AudioPipeline(proc_config).process(quiet_audio.copy(), sample_rate=16000)

    np.testing.assert_allclose(second_output, expected, atol=1e-6)
