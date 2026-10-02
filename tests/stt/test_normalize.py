"""Tests for the --normalize loudness pass (WHISP-76)."""
import numpy as np
import pytest

from whisper_aloud.stt.normalize import normalize_loudness


def test_quiet_signal_is_boosted_toward_target_level():
    sr = 16000
    t = np.arange(sr * 2) / sr
    quiet = (0.01 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    out = normalize_loudness(quiet, sr)

    assert out.dtype == np.float32
    assert out.shape == quiet.shape
    # Frame-based normalization should raise the RMS substantially above the
    # original quiet level.
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
    local speaker then a quiet remote one) must not push the quiet section's
    boost past full scale."""
    sr = 16000
    t_loud = np.arange(sr * 2) / sr
    loud = (0.5 * np.sin(2 * np.pi * 220 * t_loud)).astype(np.float32)
    t_quiet = np.arange(sr * 2) / sr
    quiet = (0.01 * np.sin(2 * np.pi * 220 * t_quiet)).astype(np.float32)
    mixed = np.concatenate([loud, quiet])

    out = normalize_loudness(mixed, sr)

    assert np.max(np.abs(out)) <= 1.0 + 1e-6


def test_noise_floor_is_not_boosted():
    """WHISP-77: a frame at/below the noise floor must stay at unity gain --
    otherwise a quiet background hiss (or near-silence) gets amplified into
    audible, VAD/decoder-confusing noise."""
    sr = 16000
    rng = np.random.default_rng(7)
    # -55dB noise is below the -50dB default noise floor.
    noise = (rng.normal(0, 1, size=sr * 2).astype(np.float32) * (10 ** (-55.0 / 20.0)))

    out = normalize_loudness(noise, sr)

    in_rms = np.sqrt(np.mean(noise.astype(np.float64) ** 2))
    out_rms = np.sqrt(np.mean(out.astype(np.float64) ** 2))
    assert out_rms == pytest.approx(in_rms, rel=0.05)


def test_max_gain_is_capped():
    """A very quiet (but above the noise floor) frame should not be boosted
    past `max_gain`."""
    sr = 16000
    t = np.arange(sr) / sr
    very_quiet = (0.0005 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)  # well above noise floor

    out = normalize_loudness(very_quiet, sr, max_gain=5.0)

    peak_in = np.max(np.abs(very_quiet))
    peak_out = np.max(np.abs(out))
    assert peak_out <= peak_in * 5.0 * 1.05  # small tolerance for Gaussian smoothing
