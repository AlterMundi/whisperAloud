"""InputNormalizer: digital adaptive input normalization (docs/design/input-gain-loop.md).

Synthetic signals only: "speech" is a harmonic tone with a 4 Hz syllable
envelope at a given voiced RMS level, "noise" is white noise at a given
floor. The field case is speech at -47 dBFS over a -66 dBFS floor.
"""

import numpy as np
import pytest

from whisper_aloud.audio.input_gain import InputNormalizer, frame_levels_db
from whisper_aloud.config import InputGainConfig

SR = 16000


def _db_to_lin(db):
    return 10.0 ** (db / 20.0)


def _rms_db(x):
    return 20.0 * np.log10(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)) + 1e-20)


def noise(seconds, floor_db, seed=0):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    return (rng.standard_normal(n) * _db_to_lin(floor_db)).astype(np.float32)


def speech(seconds, level_db, seed=1):
    """Harmonic 'voice' with a syllable envelope, scaled so its RMS is level_db."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    f0 = 140.0
    sig = sum(np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 6.28)) / k for k in range(1, 8))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)
    sig = sig * env
    sig = sig / np.sqrt(np.mean(sig**2)) * _db_to_lin(level_db)
    return sig.astype(np.float32)


def utterance(speech_db=-47.0, floor_db=-66.0, lead_s=0.5, words=5, word_s=0.6, gap_s=0.3):
    """Lead-in noise, then `words` speech bursts separated by noise-only gaps."""
    parts = [noise(lead_s, floor_db, seed=10)]
    for i in range(words):
        w = speech(word_s, speech_db, seed=20 + i) + noise(word_s, floor_db, seed=30 + i)
        parts.append(w)
        parts.append(noise(gap_s, floor_db, seed=40 + i))
    return np.concatenate(parts).astype(np.float32)


def voiced_mask(n_samples, lead_s=0.5, words=5, word_s=0.6, gap_s=0.3):
    mask = np.zeros(n_samples, dtype=bool)
    pos = int(lead_s * SR)
    for _ in range(words):
        mask[pos : pos + int(word_s * SR)] = True
        pos += int(word_s * SR) + int(gap_s * SR)
    return mask


def chunks(audio, chunk_s=0.1):
    step = int(chunk_s * SR)
    return [audio[i : i + step] for i in range(0, len(audio), step)]


# ── helpers ─────────────────────────────────────────────────────────────────


def test_frame_levels_db_handles_partial_frame_and_silence():
    audio = np.concatenate([np.zeros(160, np.float32), np.full(100, 0.1, np.float32)])
    levels, peaks = frame_levels_db(audio, frame_len=160)
    assert len(levels) == 2 and len(peaks) == 2
    assert levels[0] < -200  # digital silence -> very low, finite
    assert levels[1] == pytest.approx(-20.0, abs=0.01)
    assert peaks[1] == pytest.approx(0.1)


# ── batch path ──────────────────────────────────────────────────────────────


class TestBatch:
    def test_quiet_field_case_noise_limited(self):
        """Field case: -47 dBFS speech over a -66 dBFS floor (19 dB SNR).

        The default noise cap (-50 dBFS) binds before the -20 target: the
        floor is lifted exactly to the cap and speech by the same gain."""
        audio = utterance()
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(audio)
        stats = norm.stats

        assert stats.speech_before_dbfs == pytest.approx(-47.0, abs=2.5)
        assert stats.noise_floor_dbfs == pytest.approx(-66.0, abs=4.0)
        assert stats.limited_by == "noise"
        assert stats.final_gain_db == pytest.approx(-50.0 - stats.noise_floor_dbfs, abs=0.01)
        assert stats.final_gain_db > 12.0
        assert stats.speech_after_dbfs == pytest.approx(
            stats.speech_before_dbfs + stats.final_gain_db, abs=0.5
        )
        lead_in = out[: int(0.5 * SR)]
        assert _rms_db(lead_in) == pytest.approx(-50.0, abs=2.0)
        assert out.dtype == np.float32 and out.shape == audio.shape

    def test_quiet_clean_input_reaches_target(self):
        """Same quiet speech with a clean floor: nothing caps it, -20 is reached."""
        audio = utterance(speech_db=-47.0, floor_db=-85.0)
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(audio)
        assert norm.stats.limited_by == "target"
        assert norm.stats.speech_after_dbfs == pytest.approx(-20.0, abs=1.0)
        mask = voiced_mask(len(out))
        assert _rms_db(out[mask]) == pytest.approx(-20.0, abs=3.0)

    def test_raising_noise_cap_reaches_target_on_field_case(self):
        norm = InputNormalizer(InputGainConfig(max_output_noise_dbfs=-40.0))
        norm.normalize_buffer(utterance())
        assert norm.stats.speech_after_dbfs == pytest.approx(-20.0, abs=1.5)

    def test_noise_floor_cap_respected(self):
        cfg = InputGainConfig(max_output_noise_dbfs=-55.0)
        audio = utterance()
        norm = InputNormalizer(cfg)
        out = norm.normalize_buffer(audio)
        gap = out[: int(0.5 * SR)]  # lead-in, noise only
        assert _rms_db(gap) <= -55.0 + 1.0
        assert norm.stats.limited_by == "noise"

    def test_max_gain_bound(self):
        cfg = InputGainConfig(max_gain_db=12.0)
        norm = InputNormalizer(cfg)
        norm.normalize_buffer(utterance())
        assert norm.stats.max_gain_db <= 12.0 + 1e-9
        assert norm.stats.limited_by == "max_gain"

    def test_never_clips_and_never_exceeds_ceiling(self):
        """A loud transient inside quiet speech: output stays under the ceiling."""
        audio = utterance(floor_db=-85.0)  # ~+27 dB of gain to make clipping likely
        click_at = int(1.3 * SR)
        audio[click_at : click_at + 40] = 0.9  # -1 dBFS burst
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(audio)
        ceiling = _db_to_lin(-3.0)
        # The burst itself was already above the ceiling: passes at <= 0 dB gain.
        assert np.max(np.abs(out)) <= max(ceiling, 0.9) + 1e-6
        outside = np.ones(len(out), dtype=bool)
        outside[click_at - 160 : click_at + 200] = False
        assert np.max(np.abs(out[outside])) <= ceiling + 1e-6
        # The rest of the recording is still normalized (gain only cut near the burst).
        assert norm.stats.max_gain_db > 20.0
        assert norm.stats.min_gain_db == pytest.approx(0.0, abs=1e-9)

    def test_loud_input_passes_unchanged(self):
        """Already-hot speech: the stage only amplifies, so it's a passthrough."""
        audio = utterance(speech_db=-12.0, floor_db=-60.0)
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(audio)
        np.testing.assert_allclose(out, audio, atol=1e-7)
        assert norm.stats.final_gain_db == 0.0

    def test_no_speech_means_no_gain(self):
        audio = noise(3.0, -66.0)
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(audio)
        np.testing.assert_allclose(out, audio, atol=1e-9)
        assert norm.stats.limited_by == "no_speech"
        assert norm.stats.final_gain_db == 0.0

    def test_digital_silence_lead_in_does_not_break_floor(self):
        """Device warm-up zeros must not pull the floor estimate to -inf."""
        audio = np.concatenate([np.zeros(int(0.4 * SR), np.float32), utterance()])
        norm = InputNormalizer(InputGainConfig())
        norm.normalize_buffer(audio)
        reference = InputNormalizer(InputGainConfig())
        reference.normalize_buffer(utterance())
        assert norm.stats.noise_floor_dbfs == pytest.approx(-66.0, abs=4.0)
        assert norm.stats.final_gain_db == pytest.approx(reference.stats.final_gain_db, abs=0.5)

    def test_gain_constant_in_pauses(self):
        """Batch uses one gain for the whole buffer: pauses can't pump."""
        audio = utterance()
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(audio)
        mask = voiced_mask(len(out))
        ratio = np.abs(out[~mask]) / np.maximum(np.abs(audio[~mask]), 1e-12)
        ratio = ratio[np.abs(audio[~mask]) > 1e-6]
        assert np.ptp(20 * np.log10(ratio)) < 0.01

    def test_empty_input(self):
        norm = InputNormalizer(InputGainConfig())
        out = norm.normalize_buffer(np.array([], dtype=np.float32))
        assert out.size == 0


# ── streaming path ──────────────────────────────────────────────────────────


def _stream(norm, audio, chunk_s=0.1):
    return np.concatenate([norm.process_chunk(c) for c in chunks(audio, chunk_s)])


class TestStreaming:
    def test_converges_toward_batch_gain(self):
        audio = np.concatenate([utterance(words=8) for _ in range(2)])
        batch = InputNormalizer(InputGainConfig())
        batch.normalize_buffer(audio)

        live = InputNormalizer(InputGainConfig())
        _stream(live, audio)
        assert live.stats.final_gain_db == pytest.approx(batch.stats.final_gain_db, abs=2.0)

    def test_upward_slew_is_bounded(self):
        cfg = InputGainConfig(max_gain_change_db_per_s=10.0)
        live = InputNormalizer(cfg)
        _stream(live, utterance(words=8))
        trace = np.asarray(live.gain_trace_db)
        steps = np.diff(trace)
        # 10 ms frames at 10 dB/s -> at most 0.1 dB per frame (no transients here).
        assert steps.max() <= 0.1 + 1e-9

    def test_silence_from_start_never_raises_gain(self):
        live = InputNormalizer(InputGainConfig())
        _stream(live, noise(4.0, -66.0))
        assert max(live.gain_trace_db) == 0.0

    def test_no_pumping_in_pause(self):
        """After speech, a long noise-only pause holds the gain steady."""
        speech_part = np.concatenate([utterance(words=8) for _ in range(2)])
        pause = noise(3.0, -66.0, seed=99)
        live = InputNormalizer(InputGainConfig())
        _stream(live, speech_part)
        before = len(live.gain_trace_db)
        _stream(live, pause)
        pause_trace = np.asarray(live.gain_trace_db[before:])
        assert np.ptp(pause_trace) < 0.5

    def test_stream_output_never_exceeds_ceiling(self):
        audio = utterance(words=8)
        audio[int(2.0 * SR) : int(2.0 * SR) + 30] = 0.6  # transient below 0 dBFS
        live = InputNormalizer(InputGainConfig(), seed_gain_db=25.0)
        out = _stream(live, audio)
        assert np.max(np.abs(out)) <= max(_db_to_lin(-3.0), 0.6) + 1e-6
        mask = np.ones(len(out), dtype=bool)
        mask[int(2.0 * SR) - 160 : int(2.0 * SR) + 200] = False
        assert np.max(np.abs(out[mask])) <= _db_to_lin(-3.0) + 1e-6

    def test_seed_gain_applies_from_first_chunk(self):
        live = InputNormalizer(InputGainConfig(), seed_gain_db=20.0)
        audio = noise(0.3, -66.0)
        out = _stream(live, audio)
        assert _rms_db(out) == pytest.approx(-46.0, abs=1.0)
        assert live.gain_trace_db[0] == pytest.approx(20.0)

    def test_seed_is_clamped_to_max_gain(self):
        live = InputNormalizer(InputGainConfig(max_gain_db=10.0), seed_gain_db=40.0)
        live.process_chunk(noise(0.1, -66.0))
        assert live.gain_trace_db[0] == pytest.approx(10.0)

    def test_chunk_sizes_dont_matter_much(self):
        audio = utterance(words=8)
        a = InputNormalizer(InputGainConfig())
        b = InputNormalizer(InputGainConfig())
        _stream(a, audio, chunk_s=0.1)
        _stream(b, audio, chunk_s=0.037)
        assert a.stats.final_gain_db == pytest.approx(b.stats.final_gain_db, abs=1.0)


def test_summary_mentions_before_after_and_gains():
    norm = InputNormalizer(InputGainConfig())
    norm.normalize_buffer(utterance())
    text = norm.stats.summary()
    for needle in (
        "speech_before=",
        "speech_after=",
        "gain_min=",
        "gain_max=",
        "gain_final=",
        "noise_floor=",
        "limited_by=",
    ):
        assert needle in text
