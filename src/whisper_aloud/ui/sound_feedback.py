"""Sound feedback for WhisperAloud using GNOME sounds."""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import GSound for playing system sounds
try:
    import gi
    gi.require_version('GSound', '1.0')
    from gi.repository import GSound
    GSOUND_AVAILABLE = True
except (ImportError, ValueError) as e:
    logger.debug(f"GSound not available: {e}")
    GSOUND_AVAILABLE = False


class SoundEvent(Enum):
    """Sound events for feedback."""
    RECORDING_START = "dialog-warning"  # Click sound
    RECORDING_STOP = "dialog-warning"  # Click sound
    TRANSCRIPTION_COMPLETE = "dialog-information"  # Success sound
    ERROR = "dialog-information"  # Error sound
    CANCEL = "dialog-information"  # Cancel sound


class SoundFeedback:
    """Manages sound feedback for the application."""

    # Map of events to freedesktop sound theme IDs
    SOUND_IDS = {
        SoundEvent.RECORDING_START: "dialog-warning",
        SoundEvent.RECORDING_STOP: "dialog-warning",
        SoundEvent.TRANSCRIPTION_COMPLETE: "dialog-information",
        SoundEvent.ERROR: "dialog-information",
        SoundEvent.CANCEL: "dialog-information",
    }

    def __init__(self, enabled: bool = True):
        """
        Initialize sound feedback.

        Args:
            enabled: Whether sounds are enabled
        """
        self._enabled = enabled
        self._context: Optional[GSound.Context] = None

        if GSOUND_AVAILABLE:
            try:
                self._context = GSound.Context()
                self._context.init()
                logger.info("Sound feedback initialized with GSound")
            except Exception as e:
                logger.warning(f"Failed to initialize GSound context: {e}")
                self._context = None
        else:
            logger.info("Sound feedback disabled (GSound not available)")

    @property
    def enabled(self) -> bool:
        """Check if sounds are enabled."""
        return self._enabled and self._context is not None

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Set sound enabled state."""
        self._enabled = value

    @property
    def available(self) -> bool:
        """Check if sound system is available."""
        return self._context is not None

    def play(self, event: SoundEvent) -> None:
        """
        Play a sound for the given event.

        Args:
            event: The sound event to play
        """
        if not self.enabled:
            return

        sound_id = self.SOUND_IDS.get(event)
        if not sound_id:
            logger.warning(f"Unknown sound event: {event}")
            return

        try:
            # Play the sound asynchronously (non-blocking)
            self._context.play_simple(
                {GSound.ATTR_EVENT_ID: sound_id}
            )
            logger.debug(f"Played sound: {sound_id}")
        except Exception as e:
            logger.debug(f"Failed to play sound {sound_id}: {e}")

    def play_recording_start(self) -> None:
        """Play sound when recording starts."""
        self.play(SoundEvent.RECORDING_START)

    def play_recording_stop(self) -> None:
        """Play sound when recording stops."""
        self.play(SoundEvent.RECORDING_STOP)

    def play_transcription_complete(self) -> None:
        """Play sound when transcription completes."""
        self.play(SoundEvent.TRANSCRIPTION_COMPLETE)

    def play_error(self) -> None:
        """Play sound on error."""
        self.play(SoundEvent.ERROR)

    def play_cancel(self) -> None:
        """Play sound when operation is cancelled."""
        self.play(SoundEvent.CANCEL)
