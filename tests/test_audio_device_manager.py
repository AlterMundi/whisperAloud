"""Deterministic tests for audio device management (no real hardware required)."""

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from whisper_aloud.exceptions import AudioDeviceError


def _import_device_manager_with_fake_sounddevice(fake_sd):
    """Import device_manager with a fake sounddevice module injected."""
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        sys.modules.pop("whisper_aloud.audio.device_manager", None)
        module = importlib.import_module("whisper_aloud.audio.device_manager")
    return module


def _build_fake_sounddevice():
    """Create a fake sounddevice module-like object for deterministic tests."""
    portaudio_error = type("FakePortAudioError", (Exception,), {})
    return SimpleNamespace(
        query_devices=Mock(return_value=[]),
        query_hostapis=Mock(return_value={"name": "ALSA"}),
        default=SimpleNamespace(device=[0, 1]),
        InputStream=Mock(),
        PortAudioError=portaudio_error,
    )


def test_list_input_devices():
    """list_input_devices returns only input-capable devices with hostapi metadata."""
    fake_sd = _build_fake_sounddevice()
    fake_sd.query_devices.return_value = [
        {"max_input_channels": 0, "name": "Output Only", "default_samplerate": 44100.0, "hostapi": 0},
        {"max_input_channels": 2, "name": "USB Mic", "default_samplerate": 48000.0, "hostapi": 1},
    ]
    fake_sd.default.device = [1, 0]
    fake_sd.query_hostapis.return_value = {"name": "PipeWire"}

    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    devices = module.DeviceManager.list_input_devices()

    assert len(devices) == 1
    assert isinstance(devices[0], module.AudioDevice)
    assert devices[0].id == 1
    assert devices[0].name == "USB Mic"
    assert devices[0].channels == 2
    assert devices[0].sample_rate == 48000.0
    assert devices[0].is_default is True
    assert devices[0].hostapi == "PipeWire"


def test_list_devices_failure():
    """list_input_devices wraps unexpected errors as AudioDeviceError."""
    fake_sd = _build_fake_sounddevice()
    fake_sd.query_devices.side_effect = Exception("Hardware error")

    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    with pytest.raises(AudioDeviceError, match="Unexpected error"):
        module.DeviceManager.list_input_devices()


def test_get_default_device():
    """get_default_input_device returns the one marked as default."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    module.DeviceManager.list_input_devices = Mock(return_value=[
        module.AudioDevice(1, "Mic 1", 1, 16000.0, False, "ALSA"),
        module.AudioDevice(2, "Mic 2", 2, 48000.0, True, "PipeWire"),
    ])

    device = module.DeviceManager.get_default_input_device()
    assert device.id == 2
    assert device.name == "Mic 2"


def test_get_default_device_fallback_to_first():
    """get_default_input_device falls back to first when no default flag exists."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    module.DeviceManager.list_input_devices = Mock(return_value=[
        module.AudioDevice(5, "Mic A", 1, 16000.0, False, "ALSA"),
        module.AudioDevice(6, "Mic B", 2, 48000.0, False, "PipeWire"),
    ])

    device = module.DeviceManager.get_default_input_device()
    assert device.id == 5
    assert device.name == "Mic A"


def test_get_device_by_id():
    """get_device_by_id returns expected device."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    module.DeviceManager.list_input_devices = Mock(return_value=[
        module.AudioDevice(1, "Mic 1", 1, 16000.0, True, "ALSA"),
        module.AudioDevice(2, "Mic 2", 2, 48000.0, False, "PipeWire"),
    ])

    device = module.DeviceManager.get_device_by_id(2)
    assert device.id == 2
    assert device.name == "Mic 2"


def test_get_device_by_id_not_found():
    """get_device_by_id raises with available ids when missing."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    module.DeviceManager.list_input_devices = Mock(return_value=[
        module.AudioDevice(1, "Mic 1", 1, 16000.0, True, "ALSA"),
        module.AudioDevice(2, "Mic 2", 2, 48000.0, False, "PipeWire"),
    ])

    with pytest.raises(AudioDeviceError, match="not found"):
        module.DeviceManager.get_device_by_id(999)


def test_validate_device():
    """validate_device accepts supported format and closes test stream."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    device = module.AudioDevice(0, "Test Mic", 2, 48000.0, True, "ALSA")
    module.DeviceManager.get_device_by_id = Mock(return_value=device)

    stream_instance = Mock()
    fake_sd.InputStream.return_value = stream_instance

    validated = module.DeviceManager.validate_device(0, 16000, 1)
    assert validated == device
    fake_sd.InputStream.assert_called_once_with(
        device=0,
        samplerate=16000,
        channels=1,
        dtype="float32",
    )
    stream_instance.close.assert_called_once()


def test_validate_device_insufficient_channels():
    """validate_device rejects when requested channels exceed device capability."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    module.DeviceManager.get_device_by_id = Mock(
        return_value=module.AudioDevice(0, "Mono Mic", 1, 16000.0, True, "ALSA")
    )

    with pytest.raises(AudioDeviceError, match="channel"):
        module.DeviceManager.validate_device(0, 16000, 2)


def test_validate_device_stream_failure():
    """validate_device maps PortAudioError to AudioDeviceError."""
    fake_sd = _build_fake_sounddevice()
    module = _import_device_manager_with_fake_sounddevice(fake_sd)
    module.DeviceManager.get_device_by_id = Mock(
        return_value=module.AudioDevice(0, "Bad Device", 2, 16000.0, True, "ALSA")
    )
    fake_sd.InputStream.side_effect = fake_sd.PortAudioError("Stream error")

    with pytest.raises(AudioDeviceError, match="doesn't support"):
        module.DeviceManager.validate_device(0, 16000, 1)
