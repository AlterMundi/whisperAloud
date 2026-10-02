"""Loudness normalization for CLI `transcribe --normalize` (WHISP-76).

Reuses the daemon's own `AGC` (sliding-window RMS automatic gain control) from
`audio.audio_processor` instead of re-deriving a second gain algorithm -- it is
already exactly "a windowed RMS gain in numpy" and already unit-tested there.
"""
from __future__ import annotations

import numpy as np

from ..audio.audio_processor import AGC


def normalize_loudness(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Apply a one-shot AGC pass over pre-recorded samples before transcription."""
    agc = AGC()
    return agc.process(np.asarray(samples, dtype=np.float32), sample_rate)
