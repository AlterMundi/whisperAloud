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


def test_loud_then_quiet_does_not_clip():
    """WHISP-77 regression: a loud section followed by a much quieter one (a
    local speaker then a quiet remote one) must not push AGC's boost of the
    quiet section past full scale once it catches up."""
    sr = 16000
    t_loud = np.arange(sr * 2) / sr
    loud = (0.5 * np.sin(2 * np.pi * 220 * t_loud)).astype(np.float32)
    t_quiet = np.arange(sr * 2) / sr
    quiet = (0.01 * np.sin(2 * np.pi * 220 * t_quiet)).astype(np.float32)
    mixed = np.concatenate([loud, quiet])

    out = normalize_loudness(mixed, sr)

    assert np.max(np.abs(out)) <= 1.0 + 1e-6
