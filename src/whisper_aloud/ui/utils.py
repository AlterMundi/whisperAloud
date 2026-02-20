"""UI utilities and helpers."""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class AppState(Enum):
    """Application state machine."""

    IDLE = "idle"                    # Ready to start recording
    RECORDING = "recording"          # Currently recording audio
    TRANSCRIBING = "transcribing"    # Processing audio (model inference)
    CANCELLING = "cancelling"        # User requested cancellation of transcription
    READY = "ready"                  # Transcription complete, ready for next recording
    ERROR = "error"                  # Error state


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "0:05", "1:23", "1:02:34")

    Examples:
        >>> format_duration(0)
        '0:00'
        >>> format_duration(65)
        '1:05'
        >>> format_duration(3661)
        '1:01:01'
    """
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_confidence(value: float) -> str:
    """
    Format confidence value as percentage.

    Args:
        value: Confidence value between 0.0 and 1.0

    Returns:
        Formatted percentage string (e.g., "95%", "87%")

    Examples:
        >>> format_confidence(0.95)
        '95%'
        >>> format_confidence(0.8912)
        '89%'
    """
    return f"{int(value * 100)}%"


def format_file_size(bytes_size: int) -> str:
    """
    Format file size in bytes to human-readable string.

    Args:
        bytes_size: Size in bytes

    Returns:
        Formatted size string (e.g., "1.5 MB", "523 KB")

    Examples:
        >>> format_file_size(1024)
        '1.0 KB'
        >>> format_file_size(1572864)
        '1.5 MB'
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"
