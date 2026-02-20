"""Tests for audio recorder."""

from unittest.mock import Mock, patch

import numpy as np
import pytest

from whisper_aloud.audio import AudioRecorder, RecordingState
from whisper_aloud.config import AudioConfig
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
        # Call audio callback which should check duration
        recorder._audio_callback(np.zeros((1, 160), dtype=np.float32), 160, None, None)

        assert recorder.state == RecordingState.STOPPED


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
