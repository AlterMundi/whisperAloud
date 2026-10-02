"""Audio loading for engines that need a flat 16 kHz mono float32 array (WHISP-76).

`soundfile` (libsndfile) covers WAV/FLAC/OGG but not every container a real
recording shows up in (m4a/aac, mp3, opus). For those this falls back to PyAV via
faster-whisper's own `decode_audio` helper -- already a hard dependency (see
pyproject.toml) -- and finally to an `ffmpeg` subprocess if even PyAV can't open
it. No new hard dependency, no network access; everything degrades gracefully to
a clear error if nothing can decode the file.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

TARGET_SR = 16000


def _resample(samples: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return samples.astype(np.float32)
    import scipy.signal as ss

    duration = len(samples) / orig_sr
    target_len = max(1, int(round(duration * target_sr)))
    return ss.resample(samples, target_len).astype(np.float32)


def _to_mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim > 1:
        return samples.mean(axis=1).astype(np.float32)
    return samples.astype(np.float32)


def _load_with_soundfile(path: str) -> np.ndarray:
    samples, sr = sf.read(path, dtype="float32", always_2d=False)
    return _resample(_to_mono(samples), sr, TARGET_SR)


def _load_with_pyav(path: str) -> np.ndarray:
    from faster_whisper.audio import decode_audio

    samples = decode_audio(path, sampling_rate=TARGET_SR)
    return np.asarray(samples, dtype=np.float32)


def _load_with_ffmpeg(path: str) -> np.ndarray:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not on PATH")

    with tempfile.TemporaryDirectory() as tmp:
        out_wav = Path(tmp) / "decoded.wav"
        cmd = [
            ffmpeg, "-y", "-v", "error", "-i", str(path),
            "-ac", "1", "-ar", str(TARGET_SR), "-f", "wav", str(out_wav),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0 or not out_wav.exists():
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(f"ffmpeg failed to decode '{path}': {stderr}")
        samples, sr = sf.read(str(out_wav), dtype="float32", always_2d=False)
        return _resample(_to_mono(samples), sr, TARGET_SR)


def load_audio_16k_mono(path: str) -> np.ndarray:
    """Load `path` as float32 mono samples at 16 kHz.

    Tries soundfile first (fast path for WAV/FLAC/OGG), then PyAV
    (`faster_whisper.audio.decode_audio`, which already resamples to mono
    16 kHz), then an `ffmpeg` subprocess. Raises RuntimeError if none of the
    three backends can decode the file.
    """
    errors = []
    for name, loader in (
        ("soundfile", _load_with_soundfile),
        ("PyAV", _load_with_pyav),
        ("ffmpeg", _load_with_ffmpeg),
    ):
        try:
            return loader(path)
        except Exception as e:  # noqa: BLE001 - fall through to next backend
            logger.debug("%s could not decode %s (%s)", name, path, e)
            errors.append(f"{name}: {e}")

    raise RuntimeError(
        f"Could not decode audio file '{path}' with any available backend:\n"
        + "\n".join(errors)
    )
