"""WhisperAloud - Voice dictation with Whisper AI for Linux."""

__version__ = "0.1.0"

from .transcriber import Transcriber, TranscriptionResult
from .config import (
    WhisperAloudConfig,
    ModelConfig,
    TranscriptionConfig,
    AudioConfig,
    ClipboardConfig,
)
from .exceptions import (
    WhisperAloudError,
    ModelLoadError,
    TranscriptionError,
    AudioFormatError,
    ConfigurationError,
    AudioDeviceError,
    AudioRecordingError,
    AudioProcessingError,
    ClipboardError,
    ClipboardNotAvailableError,
    ClipboardPermissionError,
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
from .clipboard import (
    ClipboardManager,
    PasteSimulator,
)

__all__ = [
    # Core components
    "Transcriber",
    "TranscriptionResult",
    # Configuration
    "WhisperAloudConfig",
    "ModelConfig",
    "TranscriptionConfig",
    "AudioConfig",
    "ClipboardConfig",
    # Exceptions
    "WhisperAloudError",
    "ModelLoadError",
    "TranscriptionError",
    "AudioFormatError",
    "ConfigurationError",
    "AudioDeviceError",
    "AudioRecordingError",
    "AudioProcessingError",
    "ClipboardError",
    "ClipboardNotAvailableError",
    "ClipboardPermissionError",
    # Audio subsystem
    "DeviceManager",
    "AudioDevice",
    "AudioRecorder",
    "RecordingState",
    "AudioProcessor",
    "LevelMeter",
    "AudioLevel",
    # Clipboard subsystem
    "ClipboardManager",
    "PasteSimulator",
]