"""Audio device management."""

import logging
from dataclasses import dataclass
from typing import List, Optional

import sounddevice as sd

from ..exceptions import AudioDeviceError

logger = logging.getLogger(__name__)


@dataclass
class AudioDevice:
    """Represents an audio input device."""
    id: int
    name: str
    channels: int
    sample_rate: float
    is_default: bool
    hostapi: str


class DeviceManager:
    """Manages audio device enumeration and selection."""

    @staticmethod
    def list_input_devices() -> List[AudioDevice]:
        """
        List all available audio input devices.

        Returns:
            List of AudioDevice objects for input-capable devices

        Raises:
            AudioDeviceError: If device enumeration fails
        """
        try:
            devices = sd.query_devices()
            input_devices = []

            for idx, device in enumerate(devices):
                # Filter for devices with input channels
                if device['max_input_channels'] > 0:
                    is_default = (idx == sd.default.device[0])

                    input_devices.append(AudioDevice(
                        id=idx,
                        name=device['name'],
                        channels=device['max_input_channels'],
                        sample_rate=device['default_samplerate'],
                        is_default=is_default,
                        hostapi=sd.query_hostapis(device['hostapi'])['name'],
                    ))

            if not input_devices:
                raise AudioDeviceError(
                    "No audio input devices found. "
                    "Please connect a microphone and ensure it's enabled."
                )

            logger.info(f"Found {len(input_devices)} input device(s)")
            return input_devices

        except sd.PortAudioError as e:
            raise AudioDeviceError(f"Failed to enumerate audio devices: {e}") from e
        except Exception as e:
            raise AudioDeviceError(f"Unexpected error listing devices: {e}") from e

    @staticmethod
    def get_default_input_device() -> AudioDevice:
        """
        Get the system default input device.

        Returns:
            Default AudioDevice

        Raises:
            AudioDeviceError: If no default device found
        """
        devices = DeviceManager.list_input_devices()
        default_devices = [d for d in devices if d.is_default]

        if not default_devices:
            # Fallback to first available device
            logger.warning("No default device, using first available")
            return devices[0]

        return default_devices[0]

    @staticmethod
    def get_device_by_id(device_id: int) -> AudioDevice:
        """
        Get device by ID.

        Args:
            device_id: Device index

        Returns:
            AudioDevice for the specified ID

        Raises:
            AudioDeviceError: If device not found or invalid
        """
        devices = DeviceManager.list_input_devices()
        matching = [d for d in devices if d.id == device_id]

        if not matching:
            raise AudioDeviceError(
                f"Audio device {device_id} not found. "
                f"Available devices: {[d.id for d in devices]}"
            )

        return matching[0]

    @staticmethod
    def validate_device(device_id: Optional[int], sample_rate: int, channels: int) -> AudioDevice:
        """
        Validate that a device supports the required format.

        Args:
            device_id: Device ID (None for default)
            sample_rate: Required sample rate
            channels: Required channels

        Returns:
            Validated AudioDevice

        Raises:
            AudioDeviceError: If device doesn't support format
        """
        if device_id is None:
            device = DeviceManager.get_default_input_device()
        else:
            device = DeviceManager.get_device_by_id(device_id)

        # Check channel support
        if device.channels < channels:
            raise AudioDeviceError(
                f"Device '{device.name}' has only {device.channels} channel(s), "
                f"but {channels} required"
            )

        # Try to open stream with requested settings (test only)
        try:
            test_stream = sd.InputStream(
                device=device.id,
                samplerate=sample_rate,
                channels=channels,
                dtype='float32',
            )
            test_stream.close()
            logger.info(f"Device '{device.name}' validated for {sample_rate}Hz, {channels}ch")
        except sd.PortAudioError as e:
            raise AudioDeviceError(
                f"Device '{device.name}' doesn't support {sample_rate}Hz/{channels}ch: {e}"
            ) from e

        return device
