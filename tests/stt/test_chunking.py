"""Tests for silence-aware chunking (WHISP-76)."""
import numpy as np
import pytest

from whisper_aloud.stt.chunking import cut_points, iter_chunks

SR = 16000


def _tone(seconds: float, freq: float = 220.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def test_short_audio_is_a_single_chunk():
    audio = _tone(10.0)
    chunks = iter_chunks(audio, SR, chunk_s=25.0, tail_s=5.0)
    assert chunks == [(0, len(audio))]


def test_no_gaps_or_overlaps_across_many_chunks():
    audio = _tone(61.0)
    points = cut_points(audio, SR, chunk_s=25.0, tail_s=5.0)
    chunks = iter_chunks(audio, SR, chunk_s=25.0, tail_s=5.0)

    assert points[0] == 0
    assert points[-1] == len(audio)
    for (a1, b1), (a2, b2) in zip(chunks, chunks[1:]):
        assert b1 == a2  # no gap, no overlap
    assert chunks[0][0] == 0
    assert chunks[-1][1] == len(audio)


def test_last_chunk_covers_the_remainder():
    audio = _tone(61.0)
    chunks = iter_chunks(audio, SR, chunk_s=25.0, tail_s=5.0)
    # 61s of audio at chunk_s=25 cannot fully fill a 3rd 25s window, so the
    # last chunk is the (shorter) remainder, not a dropped tail.
    assert chunks[-1][1] - chunks[-1][0] < 25.0 * SR
    assert chunks[-1][1] == len(audio)


def test_cuts_land_in_the_quiet_window():
    # 20s tone, 2s silence, then 8s tone: a 25s-window/5s-tail cut must land
    # inside [20s, 25s) -- which contains the 2s silent stretch -- so the cut
    # falls in or very near that near-silent patch rather than mid-word.
    audio = np.concatenate([_tone(20.0), _silence(2.0), _tone(8.0)])
    points = cut_points(audio, SR, chunk_s=25.0, tail_s=5.0)

    assert len(points) >= 2
    cut = points[1]
    assert 20.0 * SR <= cut <= 22.0 * SR + int(0.1 * SR)


def test_cut_points_always_start_at_zero_and_end_at_length():
    audio = _tone(5.0)
    points = cut_points(audio, SR, chunk_s=25.0, tail_s=5.0)
    assert points[0] == 0
    assert points[-1] == len(audio)


def test_chunk_s_must_exceed_tail_s():
    with pytest.raises(ValueError):
        cut_points(_tone(1.0), SR, chunk_s=5.0, tail_s=5.0)


def test_invalid_sample_rate_rejected():
    with pytest.raises(ValueError):
        cut_points(_tone(1.0), 0, chunk_s=25.0, tail_s=5.0)
