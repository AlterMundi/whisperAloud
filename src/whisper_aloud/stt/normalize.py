"""Loudness normalization and high-pass filtering for CLI `transcribe
--normalize` (WHISP-76) and the faster-whisper file-transcription path
(WHISP-77).

WHISP-77 investigation summary (see config.py's TranscriptionConfig for the
settings this backs): on a real 36-minute meeting recording, the lever that
actually improved Silero VAD retention was removing low-frequency rumble
(HVAC, handling noise, room hum) with a simple high-pass filter -- NOT
loudness normalization:

    measured on the real file (threshold 0.35 / 0.2 / 0.1):
      raw                    71% / 87% / 93%
      highpass(80Hz) only    75% / 96% / 99%   <- the actual fix
      dynaudnorm only        65% / 77% / 82%   <- makes it WORSE
      highpass + dynaudnorm  68% / 85% / 92%   <- dynaudnorm still hurts

`normalize_loudness()` (below) went through two implementations during this
investigation -- first an AGC wrapper, then a non-causal dynaudnorm-style
frame normalizer -- and *both* measured as a net negative for VAD retention
on real audio, despite each looking like a clear win on short synthetic
fixtures (a clean quiet speaker against silence). The underlying lesson:
loudness-domain fixes validated only on clean synthetic clips do not
necessarily transfer to real, reverberant recordings, where a meaningful
fraction of what is quantitatively "quiet" is room tone/low-frequency rumble
rather than quiet speech, and boosting it doesn't help. The function and
CLI's `--normalize` flag are kept for anyone who wants to opt in (and it
still helps the narrower "clean quiet speaker" case), but it defaults to off
for file transcription -- see `TranscriptionConfig.normalize_loudness`.
`apply_highpass_filter()` is the function file transcription actually uses by
default (`TranscriptionConfig.highpass_hz`).
"""
from __future__ import annotations

import numpy as np
import scipy.signal as ss


def apply_highpass_filter(
    samples: np.ndarray, sample_rate: int, cutoff_hz: float = 80.0, order: int = 2
) -> np.ndarray:
    """Remove content below `cutoff_hz` with a zero-phase Butterworth high-pass.

    Offline file transcription can afford a non-causal filter (`sosfiltfilt`,
    forward-backward, zero phase-distortion) -- there is no streaming/latency
    constraint here, unlike the daemon's live pipeline. This is what measurably
    improved Silero VAD retention on a real recording (see module docstring):
    low-frequency rumble (HVAC, handling noise, room hum) sits well below
    speech's fundamental frequency range and otherwise raises the broadband
    noise floor VAD scores frames against.

    `cutoff_hz <= 0` is a no-op (returns `samples` unchanged) so callers don't
    need to branch on whether filtering is enabled.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if cutoff_hz <= 0 or samples.size == 0:
        return samples

    nyquist = sample_rate / 2.0
    if cutoff_hz >= nyquist:
        return samples

    sos = ss.butter(order, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    filtered = ss.sosfiltfilt(sos, samples)
    return filtered.astype(np.float32)

# Modeled on ffmpeg's dynaudnorm filter (f=150:g=15:p=0.95:m=20 -- frame
# length ms, Gaussian smoothing window in frames, target peak, max linear gain
# factor). WHISP-77 real-recording finding: on a real 36-minute, reverberant
# meeting recording, BOTH this implementation and ffmpeg's own dynaudnorm
# filter (run directly, same f/g/p/m) measured roughly *neutral to slightly
# negative* for Silero VAD retention versus doing no normalization at all,
# across a wide m/noise-floor sweep -- unlike on a clean synthetic fixture
# (a single quiet speaker against silence), where normalization is the
# dominant fix. On real audio, a meaningful amount of what's quantitatively
# "quiet" is room tone/reverb rather than quiet speech, and boosting it
# doesn't help (and can mildly hurt) VAD. max_gain is kept modest (10x
# instead of dynaudnorm's m=20 default) and noise_floor_db conservative
# (-45dB) to limit how much of that ambient noise gets boosted, while still
# meaningfully helping the "loud local speaker then quiet remote one"
# scenario this function exists for.
_DEFAULT_FRAME_MS = 150.0
_DEFAULT_TARGET_PEAK = 0.95
_DEFAULT_MAX_GAIN = 10.0
_DEFAULT_SMOOTHING_FRAMES = 15
_DEFAULT_NOISE_FLOOR_DB = -45.0


def _gaussian_kernel(size: int) -> np.ndarray:
    """A normalized Gaussian kernel `size` samples wide (size forced odd)."""
    if size < 2:
        return np.array([1.0])
    if size % 2 == 0:
        size += 1
    sigma = size / 6.0 if size > 1 else 1.0
    x = np.arange(size) - size // 2
    kernel = np.exp(-(x.astype(np.float64) ** 2) / (2.0 * sigma * sigma))
    return kernel / kernel.sum()


def normalize_loudness(
    samples: np.ndarray,
    sample_rate: int,
    frame_ms: float = _DEFAULT_FRAME_MS,
    target_peak: float = _DEFAULT_TARGET_PEAK,
    max_gain: float = _DEFAULT_MAX_GAIN,
    smoothing_frames: int = _DEFAULT_SMOOTHING_FRAMES,
    noise_floor_db: float = _DEFAULT_NOISE_FLOOR_DB,
) -> np.ndarray:
    """Dynamic, frame-based peak normalization (dynaudnorm-style).

    For each `frame_ms` frame, computes the gain that would bring that
    frame's peak up to `target_peak`, capped at `max_gain`x. Frames at or
    below `noise_floor_db` are left at unity gain (not boosted) so a quiet
    background noise floor -- or true silence -- does not get amplified into
    audible noise. The per-frame gain curve is then smoothed with a Gaussian
    window (`smoothing_frames` wide) so adjacent frames don't jump abruptly,
    and a final safety clamp prevents clipping from residual overshoot at
    frame boundaries.

    Unlike a causal AGC, this looks at the whole (pre-recorded) signal at
    once, which is what makes it effective on real recordings: a quiet
    passage later in the file is boosted independently of how loud an
    earlier passage was.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if samples.size == 0:
        return samples

    n = len(samples)
    frame_len = max(1, int(sample_rate * frame_ms / 1000.0))
    n_frames = int(np.ceil(n / frame_len))
    noise_floor = 10 ** (noise_floor_db / 20.0)

    frame_gains = np.ones(n_frames, dtype=np.float64)
    for i in range(n_frames):
        start = i * frame_len
        end = min(start + frame_len, n)
        frame = samples[start:end]
        if frame.size == 0:
            continue
        # Gate on RMS (the frame's overall energy) rather than peak: broadband
        # noise has a much higher crest factor than speech, so gating on peak
        # alone would let an occasional noise spike drag a genuinely-silent
        # frame's noise floor up along with it. The boost target itself still
        # uses peak (matching dynaudnorm's "p" -- bring the frame's loudest
        # sample up to target_peak) so voiced frames aren't over-boosted.
        frame_rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        if frame_rms > noise_floor:
            peak = float(np.max(np.abs(frame)))
            frame_gains[i] = min(target_peak / max(peak, 1e-9), max_gain)
        # else: frame is at/below the noise floor -- leave at unity gain.

    if smoothing_frames > 1 and n_frames > 1:
        kernel = _gaussian_kernel(min(smoothing_frames, n_frames))
        pad = len(kernel) // 2
        padded = np.pad(frame_gains, (pad, pad), mode="edge")
        frame_gains = np.convolve(padded, kernel, mode="valid")[:n_frames]

    gain_per_sample = np.repeat(frame_gains, frame_len)[:n]
    if len(gain_per_sample) < n:
        gain_per_sample = np.pad(gain_per_sample, (0, n - len(gain_per_sample)), mode="edge")

    out = samples.astype(np.float64) * gain_per_sample

    # Safety clamp: the Gaussian smoothing above can let a sample-level peak
    # creep slightly past what its own frame's gain alone would have given,
    # right at a loud/quiet boundary.
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.98:
        out *= 0.98 / peak

    return out.astype(np.float32)
