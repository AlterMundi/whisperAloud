"""WhisperAloud - Voice dictation with Whisper AI for Linux."""

from importlib import import_module

__version__ = "0.1.0"

from .config import (
    AudioConfig,
    AudioProcessingConfig,
    ClipboardConfig,
    ModelConfig,
    TranscriptionConfig,
    WhisperAloudConfig,
)
from .exceptions import (
    AudioDeviceError,
    AudioFormatError,
    AudioProcessingError,
    AudioRecordingError,
    ClipboardError,
    ClipboardNotAvailableError,
    ClipboardPermissionError,
    ConfigurationError,
    ModelLoadError,
    TranscriptionError,
    WhisperAloudError,
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
    "AudioProcessingConfig",
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

_LAZY_EXPORTS = {
    "Transcriber": ("transcriber", "Transcriber"),
    "TranscriptionResult": ("transcriber", "TranscriptionResult"),
    "DeviceManager": ("audio", "DeviceManager"),
    "AudioDevice": ("audio", "AudioDevice"),
    "AudioRecorder": ("audio", "AudioRecorder"),
    "RecordingState": ("audio", "RecordingState"),
    "AudioProcessor": ("audio", "AudioProcessor"),
    "LevelMeter": ("audio", "LevelMeter"),
    "AudioLevel": ("audio", "AudioLevel"),
    "ClipboardManager": ("clipboard", "ClipboardManager"),
    "PasteSimulator": ("clipboard", "PasteSimulator"),
}

_LAZY_SUBMODULES = {
    "audio",
    "clipboard",
    "persistence",
    "service",
    "ui",
    "utils",
}


def __getattr__(name):
    """Lazily import heavy modules to keep optional dependencies optional."""
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = import_module(f".{module_name}", __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    if name in _LAZY_SUBMODULES:
        module = import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
