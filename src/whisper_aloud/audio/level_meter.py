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
