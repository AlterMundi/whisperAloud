import numpy as np
import pytest


class TestNoiseGate:
    """Tests for noise gate processing stage."""

    def test_gate_silences_below_threshold(self):
        """Audio below threshold should be silenced."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        # Quiet noise at ~-60 dBFS
        quiet = np.random.randn(1600).astype(np.float32) * 0.001
        result = gate.process(quiet, sample_rate=16000)
        rms = np.sqrt(np.mean(result ** 2))
        assert rms < 0.0005, f"Gate should silence quiet audio, got RMS {rms}"

    def test_gate_passes_above_threshold(self):
        """Audio above threshold should pass through after attack settles."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        sr = 16000
        # Loud 440 Hz sine at ~-10 dBFS, long enough for gate to open
        t = np.arange(4800, dtype=np.float32) / sr
        loud = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        result = gate.process(loud, sample_rate=sr)
        # Skip the first attack_ms samples where the gate is ramping open.
        # At attack_ms=5ms, the gate is fully open by sample 80; 10ms gives margin.
        settled = int(0.01 * sr)  # 10ms warmup
        np.testing.assert_allclose(result[settled:], loud[settled:], atol=0.05)

    def test_gate_handles_short_chunks(self):
        """Gate should not crash on very short audio chunks."""
        from whisper_aloud.audio.audio_processor import NoiseGate
        gate = NoiseGate(threshold_db=-40.0)
        short = np.ones(10, dtype=np.float32) * 0.3
        result = gate.process(short, sample_rate=16000)
        assert result.shape == short.shape

    def test_gate_maintains_state_across_chunks(self):
        """Gate envelope state should persist between process() calls."""
        from whisper_aloud.audio.audio_processor import NoiseGate
        gate = NoiseGate(threshold_db=-40.0)
        sr = 16000
        t = np.arange(1600, dtype=np.float32) / sr
        loud = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        gate.process(loud, sample_rate=sr)
        assert gate._envelope > 0.99, "Envelope should be fully open after loud chunk"
        gate.process(np.zeros(1600, dtype=np.float32), sample_rate=sr)
        assert gate._envelope < 0.99, "Envelope should decay after silence chunk"

    def test_gate_smooth_attack_release(self):
        """Gate should not produce clicks (smooth transitions)."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        sr = 16000
        # Silence then a proper 440 Hz tone (not aliased)
        silence = np.zeros(800, dtype=np.float32)
        t = np.arange(800, dtype=np.float32) / sr
        loud = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        audio = np.concatenate([silence, loud])
        result = gate.process(audio, sample_rate=sr)
        # Check for no sudden jumps > 0.1 between consecutive samples
        diffs = np.abs(np.diff(result))
        assert np.max(diffs) < 0.15, f"Click detected: max diff {np.max(diffs)}"


class TestAGC:
    """Tests for automatic gain control."""

    def test_agc_boosts_quiet_audio(self):
        """Quiet audio should be boosted toward target."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0, max_gain_db=30.0)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        # Very quiet signal at ~-50 dBFS
        quiet = (np.sin(2 * np.pi * 440 * t) * 0.003).astype(np.float32)
        result = agc.process(quiet, sample_rate=sr)
        input_rms = np.sqrt(np.mean(quiet ** 2))
        output_rms = np.sqrt(np.mean(result ** 2))
        assert output_rms > input_rms * 5, f"AGC should boost quiet audio: in={input_rms}, out={output_rms}"

    def test_agc_attenuates_loud_audio(self):
        """Loud audio should be reduced toward target."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        # Loud signal at ~-3 dBFS
        loud = (np.sin(2 * np.pi * 440 * t) * 0.7).astype(np.float32)
        result = agc.process(loud, sample_rate=sr)
        input_rms = np.sqrt(np.mean(loud ** 2))
        output_rms = np.sqrt(np.mean(result ** 2))
        assert output_rms < input_rms * 0.8, f"AGC should attenuate loud audio: in={input_rms}, out={output_rms}"

    def test_agc_respects_max_gain(self):
        """Gain should not exceed max_gain_db."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0, max_gain_db=10.0)
        # Near-silent signal
        silent = np.ones(16000, dtype=np.float32) * 0.0001
        result = agc.process(silent, sample_rate=16000)
        max_gain_linear = 10 ** (10.0 / 20.0)  # ~3.16x
        assert np.max(np.abs(result)) <= np.max(np.abs(silent)) * max_gain_linear * 1.1

    def test_agc_maintains_state_across_chunks(self):
        """AGC gain state should persist between process() calls."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0)
        sr = 16000
        t = np.arange(1600, dtype=np.float32) / sr
        quiet = (np.sin(2 * np.pi * 440 * t) * 0.01).astype(np.float32)
        agc.process(quiet, sample_rate=sr)
        # After processing quiet audio, gain should be > 1.0
        assert agc._current_gain > 1.0, "AGC should have boosted gain after quiet audio"

    def test_agc_handles_short_chunks(self):
        """AGC should not crash on chunks shorter than window_samples."""
        from whisper_aloud.audio.audio_processor import AGC
        agc = AGC(window_ms=300.0)
        short = np.ones(10, dtype=np.float32) * 0.3
        result = agc.process(short, sample_rate=16000)
        assert result.shape == short.shape

    def test_agc_respects_min_gain(self):
        """Gain should not go below min_gain_db (attenuation limit)."""
        from whisper_aloud.audio.audio_processor import AGC
        agc = AGC(target_db=-18.0, min_gain_db=-6.0)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        # Very loud signal
        loud = (np.sin(2 * np.pi * 440 * t) * 0.9).astype(np.float32)
        result = agc.process(loud, sample_rate=sr)
        min_gain_linear = 10 ** (-6.0 / 20.0)  # ~0.5x
        # Output should not be more attenuated than min_gain allows
        # At the settled portion, the gain should be at or above min_gain
        output_rms = np.sqrt(np.mean(result[sr // 2:] ** 2))
        input_rms = np.sqrt(np.mean(loud[sr // 2:] ** 2))
        effective_gain = output_rms / input_rms
        assert effective_gain >= min_gain_linear * 0.9, f"Gain {effective_gain} below min {min_gain_linear}"


class TestPeakLimiter:
    """Tests for peak limiter."""

    def test_limiter_clips_above_ceiling(self):
        """Audio above ceiling should be limited."""
        from whisper_aloud.audio.audio_processor import PeakLimiter

        limiter = PeakLimiter(ceiling_db=-1.0)
        ceiling_linear = 10 ** (-1.0 / 20.0)  # ~0.891
        loud = np.ones(1600, dtype=np.float32) * 1.0  # 0 dBFS
        result = limiter.process(loud)
        assert np.max(np.abs(result)) <= ceiling_linear + 0.001

    def test_limiter_passes_below_ceiling(self):
        """Audio below ceiling should pass unchanged."""
        from whisper_aloud.audio.audio_processor import PeakLimiter

        limiter = PeakLimiter(ceiling_db=-1.0)
        quiet = np.ones(1600, dtype=np.float32) * 0.5
        result = limiter.process(quiet)
        np.testing.assert_array_equal(result, quiet)

    def test_limiter_handles_empty_audio(self):
        """Empty audio should pass through."""
        from whisper_aloud.audio.audio_processor import PeakLimiter

        limiter = PeakLimiter()
        empty = np.array([], dtype=np.float32)
        result = limiter.process(empty)
        assert result.size == 0
