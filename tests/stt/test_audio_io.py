"""Tests for audio loading, including non-WAV containers (WHISP-76)."""
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from whisper_aloud.stt.audio_io import TARGET_SR, load_audio_16k_mono

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _write_wav(path, seconds=1.0, sr=44100, freq=440.0):
    t = np.arange(int(seconds * sr)) / sr
    samples = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), samples, sr)
    return samples, sr


def test_loads_wav_via_soundfile_and_resamples_to_16k(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_wav(wav, seconds=1.0, sr=44100)

    out = load_audio_16k_mono(str(wav))

    assert out.dtype == np.float32
    assert out.ndim == 1
    # Within a few samples of 1s @ 16kHz after resampling.
    assert abs(len(out) - TARGET_SR) < 50


def test_stereo_wav_is_mixed_to_mono(tmp_path):
    wav = tmp_path / "stereo.wav"
    sr = 16000
    t = np.arange(sr) / sr
    left = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    right = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(wav), np.stack([left, right], axis=1), sr)

    out = load_audio_16k_mono(str(wav))
    assert out.ndim == 1
    assert len(out) == sr


def test_unreadable_file_raises_runtime_error(tmp_path):
    bogus = tmp_path / "not_audio.wav"
    bogus.write_bytes(b"this is not an audio file at all")

    with pytest.raises(RuntimeError):
        load_audio_16k_mono(str(bogus))


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
def test_decodes_m4a_fixture_generated_at_test_time(tmp_path):
    wav = tmp_path / "source.wav"
    _write_wav(wav, seconds=1.0, sr=44100)

    m4a = tmp_path / "source.m4a"
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(wav), str(m4a)],
        capture_output=True,
    )
    if result.returncode != 0 or not m4a.exists():
        pytest.skip(f"ffmpeg could not produce an m4a fixture: {result.stderr!r}")

    out = load_audio_16k_mono(str(m4a))

    assert out.dtype == np.float32
    assert out.ndim == 1
    # Lossy re-encode + resample: allow a generous tolerance on duration.
    assert abs(len(out) - TARGET_SR) < TARGET_SR * 0.1
