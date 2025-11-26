"""Custom exceptions for WhisperAloud."""


class WhisperAloudError(Exception):
    """Base exception for all WhisperAloud errors."""
    pass


class ModelLoadError(WhisperAloudError):
    """Raised when model fails to load."""
    pass


class TranscriptionError(WhisperAloudError):
    """Raised when transcription fails."""
    pass


class AudioFormatError(WhisperAloudError):
    """Raised when audio format is invalid."""
    pass


class ConfigurationError(WhisperAloudError):
    """Raised when configuration is invalid."""
    pass


class AudioDeviceError(WhisperAloudError):
    """Raised when audio device issues occur."""
    pass


class AudioRecordingError(WhisperAloudError):
    """Raised when recording fails."""
    pass


class AudioProcessingError(WhisperAloudError):
    """Raised when audio processing fails."""
    pass


class ClipboardError(WhisperAloudError):
    """Base exception for clipboard operations."""
    pass


class ClipboardNotAvailableError(ClipboardError):
    """Raised when clipboard tools are not available."""
    pass


class ClipboardPermissionError(ClipboardError):
    """Raised when clipboard operations lack permissions."""
    pass