"""Tests for audio device management."""

import pytest
from unittest.mock import Mock, patch

import sounddevice as sd

from whisper_aloud.audio import DeviceManager, AudioDevice
from whisper_aloud.exceptions import AudioDeviceError


def test_list_input_devices():
    """Test listing input devices."""
    devices = DeviceManager.list_input_devices()
    assert isinstance(devices, list)
    # May be empty if no audio hardware in CI
    if devices:
        assert all(isinstance(d, AudioDevice) for d in devices)


@patch('sounddevice.query_devices')
def test_list_devices_failure(mock_query):
    """Test device listing failure."""
    mock_query.side_effect = Exception("Hardware error")

    with pytest.raises(AudioDeviceError, match="Unexpected error"):
        DeviceManager.list_input_devices()


def test_get_default_device():
    """Test getting default device."""
    try:
        device = DeviceManager.get_default_input_device()
        assert isinstance(device, AudioDevice)
    except AudioDeviceError:
        pytest.skip("No audio devices available")


@patch('sounddevice.query_devices')
@patch('sounddevice.default')
def test_get_device_by_id(mock_default, mock_query):
    """Test getting device by ID."""
    # Mock devices as dicts (sounddevice returns dict-like DeviceList)
    mock_query.return_value = [
        {'max_input_channels': 2, 'name': 'Test Mic', 'default_samplerate': 44100.0, 'hostapi': 0},
        {'max_input_channels': 0, 'name': 'Output Only', 'default_samplerate': 44100.0, 'hostapi': 0},
    ]
    mock_default.device = [0, 1]

    # Mock hostapis - query_hostapis(index) returns a single dict
    with patch('sounddevice.query_hostapis', return_value={"name": "ALSA"}):
        device = DeviceManager.get_device_by_id(0)
        assert isinstance(device, AudioDevice)
        assert device.id == 0
        assert device.name == "Test Mic"


def test_get_device_by_id_not_found():
    """Test getting non-existent device."""
    with pytest.raises(AudioDeviceError, match="not found"):
        DeviceManager.get_device_by_id(999)


@patch('sounddevice.InputStream')
@patch('whisper_aloud.audio.device_manager.DeviceManager.get_device_by_id')
def test_validate_device(mock_get_device, mock_stream):
    """Test device validation."""
    # Mock device
    mock_device = Mock()
    mock_device.id = 0
    mock_device.name = "Test Mic"
    mock_device.channels = 2
    mock_get_device.return_value = mock_device

    # Mock stream creation (should succeed)
    mock_stream_instance = Mock()
    mock_stream.return_value = mock_stream_instance

    device = DeviceManager.validate_device(0, 16000, 1)
    assert device == mock_device

    # Verify stream was created for validation
    mock_stream.assert_called_once()


@patch('sounddevice.InputStream')
@patch('whisper_aloud.audio.device_manager.DeviceManager.get_device_by_id')
def test_validate_device_insufficient_channels(mock_get_device, mock_stream):
    """Test validation fails with insufficient channels."""
    mock_device = Mock()
    mock_device.channels = 1
    mock_get_device.return_value = mock_device

    with pytest.raises(AudioDeviceError, match="channel"):
        DeviceManager.validate_device(0, 16000, 2)


@patch('sounddevice.InputStream')
@patch('whisper_aloud.audio.device_manager.DeviceManager.get_device_by_id')
def test_validate_device_stream_failure(mock_get_device, mock_stream):
    """Test validation fails when stream creation fails."""
    mock_device = Mock()
    mock_device.name = "Bad Device"
    mock_device.channels = 2
    mock_get_device.return_value = mock_device

    # Mock stream failure with PortAudioError (what sounddevice raises)
    mock_stream.side_effect = sd.PortAudioError("Stream error")

    with pytest.raises(AudioDeviceError, match="doesn't support"):
        DeviceManager.validate_device(0, 16000, 1)