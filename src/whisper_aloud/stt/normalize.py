"""Loudness normalization for CLI `transcribe --normalize` (WHISP-76) and for
the faster-whisper file-transcription path (WHISP-77).

WHISP-77 real-recording finding: the first implementation of this function
wrapped the daemon's own `AGC` (a *causal* sliding-window RMS automatic gain
control meant for live, streaming audio). On a real 36-minute meeting
recording, that made Silero VAD retention *worse* than doing nothing at every
threshold tested (e.g. 55% vs 58% raw at threshold=0.5) -- AGC's attack/release
dynamics pump the noise floor and compress transients in a way that confuses
VAD on real (reverberant, "captured over speakers") audio, even though it
looked correct on short synthetic fixtures.

This is reimplemented as a non-causal, frame-based "dynaudnorm-style" window
normalizer -- the same algorithm family as ffmpeg's `dynaudnorm` filter
(`dynaudnorm=f=150:g=15:p=0.95:m=20`, which was used to validate this fix
against the real recording): peak-normalize each ~150ms frame toward a target
level, smooth the per-frame gain curve with a Gaussian window so gain doesn't
jump abruptly between adjacent frames, cap the maximum boost, and leave
frames at/below the noise floor alone so silence does not get amplified into
audible (and VAD-confusing, or decoder-hallucinated) noise.
"""
from __future__ import annotations

import numpy as np

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
