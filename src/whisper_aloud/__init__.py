"""WhisperAloud - Voice dictation with Whisper AI for Linux."""

__version__ = "0.1.0"

from .transcriber import Transcriber, TranscriptionResult
from .config import WhisperAloudConfig, ModelConfig, TranscriptionConfig, AudioConfig
from .exceptions import (
    WhisperAloudError,
    ModelLoadError,
    TranscriptionError,
    AudioFormatError,
    ConfigurationError,
    AudioDeviceError,
    AudioRecordingError,
    AudioProcessingError,
)
from .audio import (
    DeviceManager,
    AudioDevice,
    AudioRecorder,
    RecordingState,
    AudioProcessor,
    LevelMeter,
    AudioLevel,
)

__all__ = [
    "Transcriber",
    "TranscriptionResult",
    "WhisperAloudConfig",
    "ModelConfig",
    "TranscriptionConfig",
    "AudioConfig",
    "WhisperAloudError",
    "ModelLoadError",
    "TranscriptionError",
    "AudioFormatError",
    "ConfigurationError",
    "AudioDeviceError",
    "AudioRecordingError",
    "AudioProcessingError",
    "DeviceManager",
    "AudioDevice",
    "AudioRecorder",
    "RecordingState",
    "AudioProcessor",
    "LevelMeter",
    "AudioLevel",
]