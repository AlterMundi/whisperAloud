"""Loudness normalization for CLI `transcribe --normalize` (WHISP-76) and for
the faster-whisper file-transcription path (WHISP-77).

Reuses the daemon's own `AGC` (sliding-window RMS automatic gain control) from
`audio.audio_processor` instead of re-deriving a second gain algorithm -- it is
already exactly "a windowed RMS gain in numpy" and already unit-tested there.

WHISP-77 finding: on a recording with a loud local speaker followed by a much
quieter remote one (e.g. a meeting captured over speakers), AGC's own max gain
can push the overall peak well past +/-1.0 once it catches up to the quiet
section -- `AGC.process()` has no output ceiling by itself. A `PeakLimiter`
pass (the same soft-knee limiter the daemon's live pipeline already uses)
is added here to keep the result in range without re-deriving yet another
gain algorithm, while leaving genuine silence untouched (it is already at
unity gain -- see `AGC`'s "hold for silence" branch).
"""
from __future__ import annotations

import numpy as np

from ..audio.audio_processor import AGC, PeakLimiter


def normalize_loudness(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Apply a one-shot AGC + peak-limiter pass over pre-recorded samples
    before transcription.

    The AGC pass boosts quiet sections toward a target level; the limiter
    pass afterwards prevents that boost from pushing loud sections (or the
    transition between a loud and a quiet section) past full scale.
    """
    agc = AGC()
    limiter = PeakLimiter()
    boosted = agc.process(np.asarray(samples, dtype=np.float32), sample_rate)
    return limiter.process(boosted)
