"""Digital adaptive input normalization: the first stage of the live chain.

See docs/design/input-gain-loop.md. In short: estimate the *speech* level
from voiced 10 ms frames only (near-silence never moves the estimate, so
pauses can't pump noise up), then apply a bounded, amplify-only digital gain
toward ``InputGainConfig.target_dbfs``. Per-frame headroom cuts guarantee
the stage never pushes a sample above ``peak_ceiling_dbfs``. This is the
same idea as WebRTC APM AGC2's adaptive digital controller.

Two entry points share one estimator and one gain applier:

- ``normalize_buffer()`` (batch, ``AudioRecorder.stop()``): pass 1 measures
  the whole recording, pass 2 applies one constant gain with only the
  per-frame headroom cuts. It can't pump.
- ``process_chunk()`` (streaming, ``on_processed_chunk``): causal running
  estimate. The gain rises only on voiced frames, by at most
  ``max_gain_change_db_per_s``, and drops immediately when needed.

The device/source volume is never touched here.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from ..config import InputGainConfig

logger = logging.getLogger(__name__)

FRAME_S = 0.010

# Frames below this are digital silence (device warm-up zeros, muted input):
# ignored by every estimator so they can't drag the floor to -inf.
DIGITAL_SILENCE_DBFS = -90.0
# A frame is voiced once it clears the running floor by this margin...
VOICED_MARGIN_DB = 10.0
# ...and this absolute level (anything quieter is near-silence regardless).
VOICED_ABS_MIN_DBFS = -75.0
# Minimum-statistics floor: falls instantly, rises this fast (per audio second).
FLOOR_RISE_DB_PER_S = 3.0
# Speech-level estimate: cumulative mean for the first EST_INIT_S of voiced
# audio, then an EMA with time constant EST_TAU_S of voiced audio.
EST_INIT_S = 0.5
EST_TAU_S = 1.0
# The estimate drives the gain only once this much voiced audio was seen.
MIN_VOICED_S = 0.2
# After a headroom cut, the gain recovers no faster than this.
RELEASE_DB_PER_S = 60.0
# Batch noise floor: this percentile of non-silent frame levels.
BATCH_FLOOR_PERCENTILE = 10.0


def _lin_to_db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else -math.inf


def frame_levels_db(audio: np.ndarray, frame_len: int) -> Tuple[np.ndarray, np.ndarray]:
    """Per-frame RMS level (dBFS) and linear peak; the last frame may be short.

    Digital silence comes out as a very low but finite level (-400 dBFS) so
    downstream comparisons never see -inf/NaN.
    """
    x = np.asarray(audio, dtype=np.float64)
    n = len(x)
    if n == 0:
        return np.empty(0), np.empty(0)
    full = n // frame_len
    levels: List[np.ndarray] = []
    peaks: List[np.ndarray] = []
    if full:
        body = x[: full * frame_len].reshape(full, frame_len)
        levels.append(np.mean(body**2, axis=1))
        peaks.append(np.max(np.abs(body), axis=1))
    if n % frame_len:
        tail = x[full * frame_len :]
        levels.append(np.array([np.mean(tail**2)]))
        peaks.append(np.array([np.max(np.abs(tail))]))
    power = np.concatenate(levels)
    level_db = 10.0 * np.log10(np.maximum(power, 1e-40))
    return level_db, np.concatenate(peaks)


class _SpeechLevelTracker:
    """Running noise floor + voiced-only speech level (dB domain)."""

    def __init__(self) -> None:
        self.floor_db: Optional[float] = None
        self.voiced_s = 0.0
        self._n_voiced = 0
        self._sum_db = 0.0
        self.ema_db: Optional[float] = None

    @property
    def mean_db(self) -> Optional[float]:
        return self._sum_db / self._n_voiced if self._n_voiced else None

    @property
    def confident(self) -> bool:
        return self.voiced_s >= MIN_VOICED_S - 1e-9

    def update(self, level_db: float, dt: float) -> bool:
        """Feed one frame; returns whether it counts as voiced."""
        if level_db < DIGITAL_SILENCE_DBFS:
            return False
        if self.floor_db is None:
            self.floor_db = level_db
            return False
        voiced = level_db >= self.floor_db + VOICED_MARGIN_DB and level_db >= VOICED_ABS_MIN_DBFS
        self.floor_db = min(self.floor_db + FLOOR_RISE_DB_PER_S * dt, level_db)
        if voiced:
            self._n_voiced += 1
            self._sum_db += level_db
            self.voiced_s += dt
            if self.ema_db is None or self.voiced_s <= EST_INIT_S:
                self.ema_db = self._sum_db / self._n_voiced
            else:
                alpha = min(1.0, dt / EST_TAU_S)
                self.ema_db += alpha * (level_db - self.ema_db)
        return voiced


@dataclass
class NormalizationStats:
    """What one recording (batch) or session (streaming) did."""

    mode: str = "batch"
    speech_before_dbfs: float = math.nan
    noise_floor_dbfs: float = math.nan
    voiced_s: float = 0.0
    target_gain_db: float = 0.0
    min_gain_db: float = math.nan
    max_gain_db: float = math.nan
    final_gain_db: float = 0.0
    speech_after_dbfs: float = math.nan
    limited_by: str = "no_speech"
    _after_sum: float = field(default=0.0, repr=False)
    _after_n: int = field(default=0, repr=False)

    def summary(self) -> str:
        return (
            f"Input normalization ({self.mode}): "
            f"speech_before={self.speech_before_dbfs:.1f}dBFS "
            f"noise_floor={self.noise_floor_dbfs:.1f}dBFS voiced={self.voiced_s:.2f}s "
            f"gain_min={self.min_gain_db:.1f}dB gain_max={self.max_gain_db:.1f}dB "
            f"gain_final={self.final_gain_db:.1f}dB limited_by={self.limited_by} "
            f"speech_after={self.speech_after_dbfs:.1f}dBFS"
        )


class InputNormalizer:
    """Amplify-only, speech-level-driven digital gain (see module docstring).

    Stateful: build one per streaming session (``process_chunk``), or call
    ``normalize_buffer`` for one-shot use (which resets state itself).

    Args:
        config: Targets and bounds.
        sample_rate: Rate of the audio fed in (the pipeline runs at 16 kHz).
        seed_gain_db: Streaming start gain, e.g. the previous recording's
            gain; held until the running estimate becomes confident.
    """

    def __init__(
        self,
        config: InputGainConfig,
        sample_rate: int = 16000,
        seed_gain_db: Optional[float] = None,
    ):
        self.config = config
        self.sample_rate = sample_rate
        self.frame_len = max(1, int(round(sample_rate * FRAME_S)))
        self._seed = seed_gain_db
        self.reset()

    # ── state ───────────────────────────────────────────────────────────────

    def reset(self) -> None:
        seed = 0.0 if self._seed is None else float(self._seed)
        seed = min(max(seed, 0.0), self.config.max_gain_db)
        self._tracker = _SpeechLevelTracker()
        self._gain_db = seed  # slewed target G (streaming)
        self._applied_db = seed  # last frame's applied gain g
        self.gain_trace_db: List[float] = []
        self.stats = NormalizationStats(mode="stream")

    # ── gain law ────────────────────────────────────────────────────────────

    def _target_gain(self, speech_db: Optional[float], floor_db: Optional[float]):
        """Return (G*, limited_by); G* is None while there's no speech estimate."""
        if speech_db is None:
            return None, "no_speech"
        cfg = self.config
        gain, why = cfg.target_dbfs - speech_db, "target"
        if gain > cfg.max_gain_db:
            gain, why = cfg.max_gain_db, "max_gain"
        if floor_db is not None and cfg.max_output_noise_dbfs - floor_db < gain:
            gain, why = cfg.max_output_noise_dbfs - floor_db, "noise"
        if gain <= 0.0:
            gain, why = 0.0, "target" if why == "target" else why
        return gain, why

    def _frame_gain(self, wanted_db: float, peak: float, dt: float) -> float:
        """Headroom-cut, release-limited, amplify-only gain for one frame."""
        headroom = self.config.peak_ceiling_dbfs - _lin_to_db(peak) if peak > 0 else math.inf
        g = min(wanted_db, headroom, self._applied_db + RELEASE_DB_PER_S * dt)
        return max(0.0, g)

    def _apply_frame(self, frame: np.ndarray, g_db: float) -> np.ndarray:
        prev = self._applied_db
        n = len(frame)
        if g_db > prev:
            # Ramp up within the frame; every sample's gain stays <= g_db.
            ramp_db = prev + (g_db - prev) * (np.arange(1, n + 1) / n)
            gains = 10.0 ** (ramp_db / 20.0)
        else:
            gains = 10.0 ** (g_db / 20.0)
        self._applied_db = g_db
        self.gain_trace_db.append(g_db)
        return frame * gains

    def _record(self, g_db: float, level_db: float, voiced: bool) -> None:
        s = self.stats
        s.min_gain_db = g_db if math.isnan(s.min_gain_db) else min(s.min_gain_db, g_db)
        s.max_gain_db = g_db if math.isnan(s.max_gain_db) else max(s.max_gain_db, g_db)
        s.final_gain_db = g_db
        if voiced:
            s._after_sum += level_db + g_db
            s._after_n += 1
            s.speech_after_dbfs = s._after_sum / s._after_n

    # ── streaming ───────────────────────────────────────────────────────────

    def process_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """Normalize one streaming chunk causally, carrying state forward."""
        if chunk.size == 0:
            return chunk
        x = np.asarray(chunk, dtype=np.float64)
        levels, peaks = frame_levels_db(x, self.frame_len)
        out = np.empty_like(x)
        cfg = self.config
        for i, (level_db, peak) in enumerate(zip(levels, peaks)):
            start = i * self.frame_len
            frame = x[start : start + self.frame_len]
            dt = len(frame) / self.sample_rate
            voiced = self._tracker.update(float(level_db), dt)
            speech = self._tracker.ema_db if self._tracker.confident else None
            wanted, why = self._target_gain(speech, self._tracker.floor_db)
            if wanted is not None:
                if wanted < self._gain_db:
                    self._gain_db = wanted
                elif voiced:
                    # Rise only while speech is present: pauses never pump.
                    step = cfg.max_gain_change_db_per_s * dt
                    self._gain_db = min(wanted, self._gain_db + step)
                self.stats.limited_by = why
                self.stats.target_gain_db = wanted
            g = self._frame_gain(self._gain_db, float(peak), dt)
            out[start : start + len(frame)] = self._apply_frame(frame, g)
            self._record(g, float(level_db), voiced)
        t = self._tracker
        self.stats.speech_before_dbfs = t.ema_db if t.ema_db is not None else math.nan
        self.stats.noise_floor_dbfs = t.floor_db if t.floor_db is not None else math.nan
        self.stats.voiced_s = t.voiced_s
        return out.astype(np.float32)

    # ── batch ───────────────────────────────────────────────────────────────

    def normalize_buffer(self, audio: np.ndarray) -> np.ndarray:
        """Normalize a whole recording: measure first, then one constant gain."""
        self._seed = None
        self.reset()
        self.stats.mode = "batch"
        if audio.size == 0:
            return audio
        x = np.asarray(audio, dtype=np.float64)
        levels, peaks = frame_levels_db(x, self.frame_len)

        # Pass 1: voiced decision + speech level over the whole recording.
        tracker = self._tracker
        voiced = np.zeros(len(levels), dtype=bool)
        for i, level_db in enumerate(levels):
            start = i * self.frame_len
            dt = min(self.frame_len, len(x) - start) / self.sample_rate
            voiced[i] = tracker.update(float(level_db), dt)
        audible = levels[levels >= DIGITAL_SILENCE_DBFS]
        floor_db = float(np.percentile(audible, BATCH_FLOOR_PERCENTILE)) if audible.size else None
        speech_db = tracker.mean_db if tracker.confident else None
        wanted, why = self._target_gain(speech_db, floor_db)
        gain = wanted or 0.0

        s = self.stats
        s.speech_before_dbfs = speech_db if speech_db is not None else math.nan
        s.noise_floor_dbfs = floor_db if floor_db is not None else math.nan
        s.voiced_s = tracker.voiced_s
        s.target_gain_db = gain
        s.limited_by = why
        if gain == 0.0:
            s.min_gain_db = s.max_gain_db = s.final_gain_db = 0.0
            s.speech_after_dbfs = s.speech_before_dbfs
            self.gain_trace_db = [0.0] * len(levels)
            return np.asarray(audio, dtype=np.float32)

        # Pass 2: constant gain, cut per frame only where headroom demands.
        self._applied_db = gain
        out = np.empty_like(x)
        for i, (level_db, peak) in enumerate(zip(levels, peaks)):
            start = i * self.frame_len
            frame = x[start : start + self.frame_len]
            dt = len(frame) / self.sample_rate
            g = self._frame_gain(gain, float(peak), dt)
            out[start : start + len(frame)] = self._apply_frame(frame, g)
            self._record(g, float(level_db), bool(voiced[i]))
        return out.astype(np.float32)
