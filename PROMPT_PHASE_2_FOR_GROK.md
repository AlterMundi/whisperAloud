# WhisperAloud Phase 2: Audio Recording Module - Code Generation Prompt for Grok

## Context & Foundation

**Phase 1 Status**: ✅ COMPLETE (Core transcription engine working perfectly)
**Current Phase**: Phase 2 - Audio Recording Module
**Target System**: Debian 12, Python 3.13, Wayland session
**Existing Code**: Phase 1 provides transcription capabilities via `Transcriber` class

### Phase 1 Integration Points

You will integrate with these existing modules:
- `whisper_aloud.config`: Configuration system (extend it)
- `whisper_aloud.transcriber`: Transcriber class for numpy arrays
- `whisper_aloud.exceptions`: Custom exception hierarchy (extend it)

**Existing Phase 1 Files** (DO NOT MODIFY):
```
src/whisper_aloud/
├── __init__.py          # Will need to export new classes
├── config.py            # Will need AudioConfig dataclass added
├── exceptions.py        # Will need new audio exceptions
├── transcriber.py       # Uses numpy arrays - compatible!
└── __main__.py          # CLI (may add audio recording commands)
```

---

## Phase 2 Objectives

Build a professional audio recording subsystem that:
1. **Captures microphone input** reliably on Linux
2. **Lists available audio devices** for user selection
3. **Provides real-time audio level feedback** (RMS/peak)
4. **Handles device errors gracefully** (busy, missing, permission issues)
5. **Outputs numpy arrays** compatible with Phase 1 Transcriber
6. **Supports voice activity detection (VAD)** for silence trimming
7. **Integrates with existing configuration system**

---

## Technical Requirements

### Core Dependencies

Add to `requirements.txt`:
```python
sounddevice>=0.4.6    # PortAudio wrapper for audio I/O
scipy>=1.10.0         # Signal processing, resampling
```

### System Dependencies (User must install)
```bash
sudo apt install portaudio19-dev libportaudio2
```

---

## Detailed Implementation Specifications

### Directory Structure

Create new `audio/` subpackage:

```
src/whisper_aloud/audio/
├── __init__.py              # Public API exports
├── device_manager.py        # Audio device enumeration
├── recorder.py              # Main recording class
├── audio_processor.py       # VAD, normalization, resampling
└── level_meter.py           # Real-time audio level calculation
```

---

## File 1: `src/whisper_aloud/audio/__init__.py`

Public API exports for the audio subpackage.

```python
"""Audio recording subsystem for WhisperAloud."""

from .device_manager import DeviceManager, AudioDevice
from .recorder import AudioRecorder, RecordingState
from .audio_processor import AudioProcessor
from .level_meter import LevelMeter, AudioLevel

__all__ = [
    "DeviceManager",
    "AudioDevice",
    "AudioRecorder",
    "RecordingState",
    "AudioProcessor",
    "LevelMeter",
    "AudioLevel",
]
```

---

## File 2: `src/whisper_aloud/exceptions.py` (EXTEND)

Add new audio-related exceptions to existing file:

```python
# ADD TO EXISTING exceptions.py (after ConfigurationError)

class AudioDeviceError(WhisperAloudError):
    """Raised when audio device issues occur."""
    pass

class AudioRecordingError(WhisperAloudError):
    """Raised when recording fails."""
    pass

class AudioProcessingError(WhisperAloudError):
    """Raised when audio processing fails."""
    pass
```

**Update `__init__.py`** to export these new exceptions.

---

## File 3: `src/whisper_aloud/config.py` (EXTEND)

Add audio configuration dataclass:

```python
# ADD TO EXISTING config.py (after TranscriptionConfig)

@dataclass
class AudioConfig:
    """Configuration for audio recording."""
    sample_rate: int = 16000           # Hz (Whisper native rate)
    channels: int = 1                  # Mono
    device_id: Optional[int] = None    # None = default device
    chunk_duration: float = 0.1        # Seconds per audio chunk
    vad_enabled: bool = True           # Voice activity detection
    vad_threshold: float = 0.02        # RMS threshold for VAD
    silence_duration: float = 1.0      # Seconds of silence to trim
    normalize_audio: bool = True       # Normalize to [-1, 1]
    max_recording_duration: float = 300.0  # 5 minutes max

# UPDATE WhisperAloudConfig dataclass
@dataclass
class WhisperAloudConfig:
    """Main configuration for WhisperAloud."""
    model: ModelConfig
    transcription: TranscriptionConfig
    audio: AudioConfig  # ADD THIS

    @classmethod
    def load(cls) -> 'WhisperAloudConfig':
        """Load configuration from environment variables."""
        # ... existing model and transcription config ...

        # ADD THIS
        audio_config = AudioConfig(
            sample_rate=int(os.getenv('WHISPER_ALOUD_SAMPLE_RATE', '16000')),
            channels=int(os.getenv('WHISPER_ALOUD_CHANNELS', '1')),
            device_id=int(os.getenv('WHISPER_ALOUD_DEVICE_ID')) if os.getenv('WHISPER_ALOUD_DEVICE_ID') else None,
            chunk_duration=float(os.getenv('WHISPER_ALOUD_CHUNK_DURATION', '0.1')),
            vad_enabled=os.getenv('WHISPER_ALOUD_VAD_ENABLED', 'true').lower() == 'true',
            vad_threshold=float(os.getenv('WHISPER_ALOUD_VAD_THRESHOLD', '0.02')),
            silence_duration=float(os.getenv('WHISPER_ALOUD_SILENCE_DURATION', '1.0')),
            normalize_audio=os.getenv('WHISPER_ALOUD_NORMALIZE_AUDIO', 'true').lower() == 'true',
            max_recording_duration=float(os.getenv('WHISPER_ALOUD_MAX_RECORDING_DURATION', '300.0')),
        )

        config = cls(model=model_config, transcription=transcription_config, audio=audio_config)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate configuration values."""
        # ... existing validation ...

        # ADD AUDIO VALIDATION
        if not (8000 <= self.audio.sample_rate <= 48000):
            raise ConfigurationError(
                f"Invalid sample rate {self.audio.sample_rate}. "
                "Must be between 8000 and 48000 Hz"
            )

        if self.audio.channels not in (1, 2):
            raise ConfigurationError(
                f"Invalid channels {self.audio.channels}. "
                "Must be 1 (mono) or 2 (stereo)"
            )

        if not (0.0 < self.audio.vad_threshold < 1.0):
            raise ConfigurationError(
                f"Invalid VAD threshold {self.audio.vad_threshold}. "
                "Must be between 0.0 and 1.0"
            )

        if not (0.01 <= self.audio.chunk_duration <= 1.0):
            raise ConfigurationError(
                f"Invalid chunk duration {self.audio.chunk_duration}. "
                "Must be between 0.01 and 1.0 seconds"
            )
```

---

## File 4: `src/whisper_aloud/audio/device_manager.py`

Audio device enumeration and management.

**Requirements**:
- List all available input devices
- Get default input device
- Validate device exists and supports required format
- Handle permission errors gracefully
- Filter out output-only devices

```python
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
```

---

## File 5: `src/whisper_aloud/audio/level_meter.py`

Real-time audio level calculation.

**Requirements**:
- Calculate RMS (root mean square) level
- Calculate peak level
- Smooth levels over time (avoid jitter)
- Normalize to 0.0-1.0 range
- Thread-safe for callback usage

```python
"""Real-time audio level metering."""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioLevel:
    """Audio level measurements."""
    rms: float      # RMS level (0.0 to 1.0)
    peak: float     # Peak level (0.0 to 1.0)
    db: float       # Decibel level (negative values)


class LevelMeter:
    """Calculates and smooths audio levels in real-time."""

    def __init__(self, smoothing: float = 0.3):
        """
        Initialize level meter.

        Args:
            smoothing: Smoothing factor (0.0 = no smoothing, 1.0 = maximum smoothing)
        """
        self.smoothing = max(0.0, min(1.0, smoothing))
        self._last_rms: Optional[float] = None
        self._last_peak: Optional[float] = None

    def calculate_level(self, audio_chunk: np.ndarray) -> AudioLevel:
        """
        Calculate audio levels from a chunk.

        Args:
            audio_chunk: Float32 audio data [-1.0, 1.0]

        Returns:
            AudioLevel with RMS, peak, and dB measurements
        """
        if audio_chunk.size == 0:
            return AudioLevel(rms=0.0, peak=0.0, db=-100.0)

        # Calculate RMS (root mean square)
        rms = float(np.sqrt(np.mean(np.square(audio_chunk))))

        # Calculate peak
        peak = float(np.max(np.abs(audio_chunk)))

        # Apply smoothing
        if self._last_rms is not None:
            rms = self.smoothing * self._last_rms + (1.0 - self.smoothing) * rms
        if self._last_peak is not None:
            peak = self.smoothing * self._last_peak + (1.0 - self.smoothing) * peak

        self._last_rms = rms
        self._last_peak = peak

        # Calculate decibels (avoid log(0))
        db = 20 * np.log10(max(rms, 1e-10))

        return AudioLevel(
            rms=min(1.0, rms),
            peak=min(1.0, peak),
            db=float(db),
        )

    def reset(self) -> None:
        """Reset smoothing history."""
        self._last_rms = None
        self._last_peak = None
```

---

## File 6: `src/whisper_aloud/audio/audio_processor.py`

Audio processing utilities (VAD, normalization, resampling).

**Requirements**:
- Voice activity detection (energy-based)
- Trim silence from start/end
- Normalize audio levels
- Resample to target rate if needed
- Convert stereo to mono

```python
"""Audio processing utilities."""

import logging
from typing import Tuple

import numpy as np
from scipy import signal

from ..exceptions import AudioProcessingError

logger = logging.getLogger(__name__)


class AudioProcessor:
    """Audio processing operations."""

    @staticmethod
    def normalize(audio: np.ndarray, target_level: float = 0.95) -> np.ndarray:
        """
        Normalize audio to target peak level.

        Args:
            audio: Input audio array
            target_level: Target peak level (0.0 to 1.0)

        Returns:
            Normalized audio array
        """
        if audio.size == 0:
            return audio

        peak = np.max(np.abs(audio))
        if peak > 0:
            return audio * (target_level / peak)
        return audio

    @staticmethod
    def stereo_to_mono(audio: np.ndarray) -> np.ndarray:
        """
        Convert stereo audio to mono.

        Args:
            audio: Stereo audio array (2D)

        Returns:
            Mono audio array (1D)
        """
        if audio.ndim == 1:
            return audio  # Already mono
        elif audio.ndim == 2:
            # Average channels
            return np.mean(audio, axis=1).astype(audio.dtype)
        else:
            raise AudioProcessingError(f"Invalid audio shape: {audio.shape}")

    @staticmethod
    def resample(audio: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
        """
        Resample audio to target sample rate.

        Args:
            audio: Input audio array
            original_rate: Original sample rate
            target_rate: Target sample rate

        Returns:
            Resampled audio array
        """
        if original_rate == target_rate:
            return audio

        if audio.size == 0:
            return audio

        try:
            num_samples = int(len(audio) * target_rate / original_rate)
            resampled = signal.resample(audio, num_samples)
            logger.debug(f"Resampled {original_rate}Hz -> {target_rate}Hz")
            return resampled.astype(np.float32)
        except Exception as e:
            raise AudioProcessingError(f"Resampling failed: {e}") from e

    @staticmethod
    def detect_voice_activity(audio: np.ndarray, threshold: float = 0.02) -> np.ndarray:
        """
        Detect voice activity (energy-based VAD).

        Args:
            audio: Input audio array
            threshold: RMS threshold for voice detection

        Returns:
            Boolean array (True = voice, False = silence)
        """
        if audio.size == 0:
            return np.array([], dtype=bool)

        # Calculate RMS in sliding windows
        window_size = 400  # ~25ms at 16kHz
        hop_size = 160     # ~10ms at 16kHz

        activity = np.zeros(len(audio), dtype=bool)

        for i in range(0, len(audio) - window_size, hop_size):
            window = audio[i:i + window_size]
            rms = np.sqrt(np.mean(np.square(window)))

            if rms > threshold:
                activity[i:i + window_size] = True

        return activity

    @staticmethod
    def trim_silence(
        audio: np.ndarray,
        sample_rate: int,
        threshold: float = 0.02,
        min_silence_duration: float = 0.3
    ) -> Tuple[np.ndarray, int, int]:
        """
        Trim silence from start and end of audio.

        Args:
            audio: Input audio array
            sample_rate: Sample rate in Hz
            threshold: RMS threshold for voice detection
            min_silence_duration: Minimum silence duration to trim (seconds)

        Returns:
            Tuple of (trimmed_audio, start_sample, end_sample)
        """
        if audio.size == 0:
            return audio, 0, 0

        # Detect voice activity
        activity = AudioProcessor.detect_voice_activity(audio, threshold)

        # Find first and last voice activity
        voice_indices = np.where(activity)[0]

        if len(voice_indices) == 0:
            # No voice detected, return empty or original
            logger.warning("No voice activity detected in audio")
            return audio, 0, len(audio)

        start_idx = voice_indices[0]
        end_idx = voice_indices[-1]

        # Add small padding
        padding_samples = int(0.1 * sample_rate)  # 100ms padding
        start_idx = max(0, start_idx - padding_samples)
        end_idx = min(len(audio), end_idx + padding_samples)

        trimmed = audio[start_idx:end_idx]
        logger.debug(f"Trimmed {start_idx / sample_rate:.2f}s from start, "
                    f"{(len(audio) - end_idx) / sample_rate:.2f}s from end")

        return trimmed, start_idx, end_idx

    @staticmethod
    def process_recording(
        audio: np.ndarray,
        sample_rate: int,
        target_rate: int = 16000,
        normalize: bool = True,
        trim_silence_enabled: bool = True,
        vad_threshold: float = 0.02
    ) -> np.ndarray:
        """
        Complete audio processing pipeline.

        Args:
            audio: Input audio array
            sample_rate: Original sample rate
            target_rate: Target sample rate (Whisper uses 16kHz)
            normalize: Whether to normalize levels
            trim_silence_enabled: Whether to trim silence
            vad_threshold: VAD threshold for silence trimming

        Returns:
            Processed audio array ready for transcription
        """
        if audio.size == 0:
            logger.warning("Empty audio provided to processor")
            return audio

        # Convert to mono if stereo
        if audio.ndim > 1:
            audio = AudioProcessor.stereo_to_mono(audio)

        # Resample if needed
        if sample_rate != target_rate:
            audio = AudioProcessor.resample(audio, sample_rate, target_rate)

        # Trim silence
        if trim_silence_enabled:
            audio, _, _ = AudioProcessor.trim_silence(audio, target_rate, vad_threshold)

        # Normalize
        if normalize and audio.size > 0:
            audio = AudioProcessor.normalize(audio)

        logger.info(f"Processed audio: {len(audio) / target_rate:.2f}s duration")
        return audio
```

---

## File 7: `src/whisper_aloud/audio/recorder.py`

Main audio recorder class with threading and state management.

**Requirements**:
- Non-blocking recording (runs in background)
- Start/stop/pause controls
- Real-time level callbacks
- Thread-safe state management
- Maximum duration enforcement
- Automatic device selection
- Error recovery

```python
"""Audio recording with state management."""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

import numpy as np
import sounddevice as sd

from ..config import AudioConfig
from ..exceptions import AudioRecordingError, AudioDeviceError
from .device_manager import DeviceManager, AudioDevice
from .level_meter import LevelMeter, AudioLevel
from .audio_processor import AudioProcessor

logger = logging.getLogger(__name__)


class RecordingState(Enum):
    """Recording state machine."""
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class AudioRecorder:
    """
    Professional audio recorder with state management.

    Features:
    - Non-blocking background recording
    - Real-time level monitoring
    - Voice activity detection
    - Automatic format conversion
    - Thread-safe state management
    """

    def __init__(
        self,
        config: AudioConfig,
        level_callback: Optional[Callable[[AudioLevel], None]] = None
    ):
        """
        Initialize recorder.

        Args:
            config: Audio configuration
            level_callback: Optional callback for real-time level updates
        """
        self.config = config
        self.level_callback = level_callback

        # State
        self._state = RecordingState.IDLE
        self._state_lock = threading.Lock()

        # Recording data
        self._frames: List[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._device: Optional[AudioDevice] = None
        self._start_time: Optional[float] = None

        # Components
        self._level_meter = LevelMeter(smoothing=0.3)
        self._processor = AudioProcessor()

        logger.info("AudioRecorder initialized")

    @property
    def state(self) -> RecordingState:
        """Get current recording state."""
        with self._state_lock:
            return self._state

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.state == RecordingState.RECORDING

    @property
    def recording_duration(self) -> float:
        """Get current recording duration in seconds."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def _set_state(self, new_state: RecordingState) -> None:
        """Set recording state (thread-safe)."""
        with self._state_lock:
            logger.debug(f"State: {self._state.value} -> {new_state.value}")
            self._state = new_state

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """
        Callback for audio stream (runs in audio thread).

        Args:
            indata: Input audio data
            frames: Number of frames
            time_info: Timing info
            status: PortAudio status
        """
        if status:
            logger.warning(f"Audio callback status: {status}")

        if self.state != RecordingState.RECORDING:
            return

        # Copy audio data (indata is read-only)
        audio_chunk = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()

        # Store frame
        self._frames.append(audio_chunk)

        # Calculate and report level
        if self.level_callback:
            level = self._level_meter.calculate_level(audio_chunk)
            try:
                self.level_callback(level)
            except Exception as e:
                logger.error(f"Level callback error: {e}")

        # Check max duration
        if self.recording_duration >= self.config.max_recording_duration:
            logger.warning(f"Max recording duration reached: {self.config.max_recording_duration}s")
            self._set_state(RecordingState.STOPPED)

    def start(self, device_id: Optional[int] = None) -> None:
        """
        Start recording.

        Args:
            device_id: Optional device ID (uses config default if None)

        Raises:
            AudioRecordingError: If recording fails to start
            AudioDeviceError: If device validation fails
        """
        if self.state == RecordingState.RECORDING:
            logger.warning("Already recording, ignoring start request")
            return

        try:
            # Validate device
            device_id = device_id or self.config.device_id
            self._device = DeviceManager.validate_device(
                device_id,
                self.config.sample_rate,
                self.config.channels
            )

            logger.info(f"Starting recording on device: {self._device.name}")

            # Reset state
            self._frames = []
            self._level_meter.reset()
            self._start_time = time.time()

            # Open stream
            self._stream = sd.InputStream(
                device=self._device.id,
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype='float32',
                callback=self._audio_callback,
                blocksize=int(self.config.chunk_duration * self.config.sample_rate),
            )

            self._stream.start()
            self._set_state(RecordingState.RECORDING)
            logger.info("Recording started")

        except AudioDeviceError:
            self._set_state(RecordingState.ERROR)
            raise
        except Exception as e:
            self._set_state(RecordingState.ERROR)
            raise AudioRecordingError(f"Failed to start recording: {e}") from e

    def stop(self) -> np.ndarray:
        """
        Stop recording and return audio data.

        Returns:
            Recorded audio as float32 numpy array (mono, normalized)

        Raises:
            AudioRecordingError: If stop fails or no data recorded
        """
        if self.state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            raise AudioRecordingError(f"Cannot stop recording in state: {self.state.value}")

        try:
            logger.info("Stopping recording...")
            self._set_state(RecordingState.STOPPED)

            # Stop and close stream
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            # Concatenate frames
            if not self._frames:
                logger.warning("No audio frames recorded")
                return np.array([], dtype=np.float32)

            raw_audio = np.concatenate(self._frames)
            logger.info(f"Recorded {len(raw_audio) / self.config.sample_rate:.2f}s of audio")

            # Process audio
            processed_audio = self._processor.process_recording(
                raw_audio,
                sample_rate=self.config.sample_rate,
                target_rate=16000,  # Whisper's native rate
                normalize=self.config.normalize_audio,
                trim_silence_enabled=self.config.vad_enabled,
                vad_threshold=self.config.vad_threshold
            )

            # Reset state
            self._set_state(RecordingState.IDLE)
            logger.info(f"Recording complete: {len(processed_audio) / 16000:.2f}s (processed)")

            return processed_audio

        except Exception as e:
            self._set_state(RecordingState.ERROR)
            raise AudioRecordingError(f"Failed to stop recording: {e}") from e

    def cancel(self) -> None:
        """Cancel recording and discard audio data."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._frames = []
        self._set_state(RecordingState.IDLE)
        logger.info("Recording cancelled")

    def pause(self) -> None:
        """Pause recording (keeps stream open)."""
        if self.state != RecordingState.RECORDING:
            return

        self._set_state(RecordingState.PAUSED)
        logger.info("Recording paused")

    def resume(self) -> None:
        """Resume paused recording."""
        if self.state != RecordingState.PAUSED:
            return

        self._set_state(RecordingState.RECORDING)
        logger.info("Recording resumed")

    def __del__(self):
        """Cleanup on deletion."""
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
```

---

## Testing Requirements

### File: `tests/test_audio_device_manager.py`

```python
"""Tests for audio device management."""

import pytest
from unittest.mock import Mock, patch

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
```

### File: `tests/test_audio_processor.py`

```python
"""Tests for audio processing."""

import numpy as np
import pytest

from whisper_aloud.audio import AudioProcessor


def test_normalize():
    """Test audio normalization."""
    audio = np.array([0.5, -0.5, 0.25], dtype=np.float32)
    normalized = AudioProcessor.normalize(audio, target_level=0.95)

    assert np.max(np.abs(normalized)) == pytest.approx(0.95, abs=0.01)


def test_stereo_to_mono():
    """Test stereo to mono conversion."""
    stereo = np.array([[0.5, 0.3], [0.2, 0.4]], dtype=np.float32)
    mono = AudioProcessor.stereo_to_mono(stereo)

    assert mono.ndim == 1
    assert len(mono) == 2
    assert mono[0] == pytest.approx(0.4, abs=0.01)  # Average of 0.5 and 0.3


def test_resample():
    """Test audio resampling."""
    audio = np.sin(2 * np.pi * 440 * np.linspace(0, 1, 44100)).astype(np.float32)
    resampled = AudioProcessor.resample(audio, 44100, 16000)

    expected_length = int(len(audio) * 16000 / 44100)
    assert len(resampled) == expected_length


def test_detect_voice_activity():
    """Test VAD."""
    # Silent audio
    silent = np.zeros(16000, dtype=np.float32)
    activity = AudioProcessor.detect_voice_activity(silent, threshold=0.02)
    assert not activity.any()

    # Loud audio
    loud = np.ones(16000, dtype=np.float32) * 0.5
    activity = AudioProcessor.detect_voice_activity(loud, threshold=0.02)
    assert activity.any()
```

### File: `tests/test_audio_recorder.py`

```python
"""Tests for audio recorder."""

import numpy as np
import pytest
from unittest.mock import Mock, patch

from whisper_aloud.config import AudioConfig
from whisper_aloud.audio import AudioRecorder, RecordingState
from whisper_aloud.exceptions import AudioRecordingError


def test_recorder_initialization():
    """Test recorder initializes correctly."""
    config = AudioConfig()
    recorder = AudioRecorder(config)

    assert recorder.state == RecordingState.IDLE
    assert recorder.recording_duration == 0.0
    assert not recorder.is_recording


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
```

---

## CLI Integration (Optional)

Add recording test command to `__main__.py`:

```python
# Add new subcommand for testing recording
parser.add_argument(
    '--record-test',
    action='store_true',
    help='Test audio recording (5 seconds)'
)

# In main():
if args.record_test:
    from .audio import AudioRecorder

    config = WhisperAloudConfig.load()
    recorder = AudioRecorder(config)

    print("Recording 5 seconds... Speak now!")
    recorder.start()
    time.sleep(5)
    audio = recorder.stop()

    print(f"Recorded {len(audio) / 16000:.2f}s of audio")
    print(f"Peak level: {np.max(np.abs(audio)):.3f}")
    return 0
```

---

## Success Criteria

Phase 2 is complete when:

- [ ] All 7 new files created with correct structure
- [ ] `pip install -e .` succeeds (may need `sudo apt install portaudio19-dev`)
- [ ] Can list audio devices: `python -c "from whisper_aloud.audio import DeviceManager; print(DeviceManager.list_input_devices())"`
- [ ] Can record audio: Recorder starts, captures data, stops cleanly
- [ ] Audio output is compatible with Phase 1 Transcriber
- [ ] All unit tests pass
- [ ] Real-time level feedback works (callbacks fire)
- [ ] VAD trims silence correctly
- [ ] Thread-safe (no race conditions in state management)
- [ ] Error messages are clear and actionable
- [ ] Documentation updated in README

---

## Integration Example

After Phase 2, users should be able to:

```python
from whisper_aloud import WhisperAloudConfig, Transcriber
from whisper_aloud.audio import AudioRecorder

# Load config
config = WhisperAloudConfig.load()

# Initialize components
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)

# Record audio
print("Recording... (press Ctrl+C to stop)")
recorder.start()
input("Press Enter to stop")
audio = recorder.stop()

# Transcribe
result = transcriber.transcribe_numpy(audio, sample_rate=16000)
print(f"Transcription: {result.text}")
```

---

## Important Notes for Code Generation

1. **Thread Safety**: Recorder runs in audio thread, use locks for shared state
2. **Error Recovery**: Handle device busy, permission denied, disconnected gracefully
3. **Memory Management**: Don't accumulate unbounded audio (enforce max duration)
4. **Sample Rate**: Always output 16kHz for Whisper compatibility
5. **Type Safety**: Full type hints including numpy array shapes in comments
6. **Logging**: Use logging module (inherited from Phase 1)
7. **Testing**: Mock `sounddevice` to avoid requiring real audio hardware in tests
8. **Documentation**: Docstrings for all public methods with Args/Returns/Raises
9. **Dependencies**: Update `requirements.txt` with sounddevice and scipy
10. **Integration**: Audio output must be compatible with `Transcriber.transcribe_numpy()`

---

## Estimated Complexity

- **Files to create**: 7 new Python modules + 3 test files
- **Lines of code**: ~800 lines (source) + ~200 lines (tests)
- **Integration points**: 3 files to modify (config.py, exceptions.py, __init__.py)
- **Dependencies**: 2 new packages (sounddevice, scipy)
- **Time estimate**: 45-60 minutes for code generation

Generate production-ready, well-tested, thread-safe audio recording code that integrates seamlessly with Phase 1.
