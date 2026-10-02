"""Tests for the --normalize loudness pass (WHISP-76)."""
import numpy as np

from whisper_aloud.stt.normalize import normalize_loudness


def test_quiet_signal_is_boosted_toward_target_level():
    sr = 16000
    t = np.arange(sr * 2) / sr
    quiet = (0.01 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    out = normalize_loudness(quiet, sr)

    assert out.dtype == np.float32
    assert out.shape == quiet.shape
    # AGC should raise the RMS substantially above the original quiet level.
    assert np.sqrt(np.mean(out[sr:] ** 2)) > np.sqrt(np.mean(quiet[sr:] ** 2)) * 2


def test_silence_stays_silence():
    sr = 16000
    silence = np.zeros(sr, dtype=np.float32)
    out = normalize_loudness(silence, sr)
    assert np.allclose(out, 0.0)


def test_empty_array_is_handled():
    out = normalize_loudness(np.array([], dtype=np.float32), 16000)
    assert len(out) == 0
