"""Silence-aware chunk boundaries for engines without native long-form support (WHISP-76).

Pure, stateless, numpy-only -- no model/IO dependency, so it is cheap to unit test
in isolation. Mirrors the quietest-frame-in-the-tail heuristic validated by the
prototype script ``~/REPOS/scripts/transcribe_meeting_parakeet.py``: each cut (after
the first) lands at the quietest ``frame_s`` frame within the last ``tail_s``
seconds of its window, so a cut is unlikely to land mid-word.

faster-whisper decodes long audio natively and never goes through this module;
it exists for engines (Parakeet and other sherpa-onnx backends) whose offline
recognizer takes one bounded array per call.
"""
from __future__ import annotations

import numpy as np


def cut_points(
    samples: np.ndarray,
    sample_rate: int,
    chunk_s: float = 25.0,
    tail_s: float = 5.0,
    frame_s: float = 0.1,
) -> list[int]:
    """Sample-index cut points splitting `samples` into ~chunk_s windows.

    Always starts with 0 and ends with ``len(samples)``. A recording shorter
    than ``chunk_s`` yields exactly ``[0, len(samples)]`` (a single chunk).
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if chunk_s <= tail_s:
        raise ValueError("chunk_s must be greater than tail_s")

    frame = max(1, int(sample_rate * frame_s))
    total = len(samples)
    points = [0]
    start = 0
    while total - start > chunk_s * sample_rate:
        lo = start + int((chunk_s - tail_s) * sample_rate)
        hi = start + int(chunk_s * sample_rate)
        window = samples[lo:hi]
        n = len(window) // frame
        if n == 0:
            # Window too short to frame (pathological chunk_s/tail_s/sr combo).
            start = hi
        else:
            energy = (window[: n * frame].astype(np.float64).reshape(n, frame) ** 2).mean(axis=1)
            start = lo + int(np.argmin(energy)) * frame + frame // 2
        points.append(start)
    points.append(total)
    return points


def iter_chunks(
    samples: np.ndarray,
    sample_rate: int,
    chunk_s: float = 25.0,
    tail_s: float = 5.0,
    frame_s: float = 0.1,
) -> list[tuple[int, int]]:
    """(start, end) sample-index pairs covering `samples` with no gaps or overlaps."""
    points = cut_points(samples, sample_rate, chunk_s=chunk_s, tail_s=tail_s, frame_s=frame_s)
    return list(zip(points, points[1:]))
