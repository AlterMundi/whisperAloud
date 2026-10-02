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


class TestHighPass:
    """Tests for the causal high-pass stage (WHISP-83).

    A 2nd-order Butterworth high-pass via scipy's ``sosfilt``, with
    persistent ``zi`` state carried across ``process()`` calls -- the same
    "causal + stateful = exactly chunk-invariant" shape as ``PeakLimiter``
    (stateless) and the WHISP-65 ``AGC`` fix (stateful, exact). See
    ``tests/test_audio_pipeline_streaming.py`` for the chunk-invariance and
    no-lookahead coverage.
    """

    def _tone(self, freq_hz: float, sr: int = 16000, duration_s: float = 1.0) -> np.ndarray:
        t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
        return np.sin(2 * np.pi * freq_hz * t).astype(np.float32)

    def _rms(self, audio: np.ndarray) -> float:
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))

    def test_attenuates_30hz_rumble(self):
        """A 30Hz tone (well inside the WHISP-77 rumble band, 30-60Hz) must
        be strongly attenuated by an 80Hz high-pass."""
        from whisper_aloud.audio.audio_processor import HighPass

        hp = HighPass(cutoff_hz=80.0)
        tone = self._tone(30.0)
        out = hp.process(tone.copy(), 16000)
        # Settle past the filter's initial transient before measuring level.
        settled_in, settled_out = tone[4000:], out[4000:]
        ratio = self._rms(settled_out) / self._rms(settled_in)
        assert ratio < 0.25, f"expected 30Hz to be attenuated below -12dB, got ratio={ratio:.3f}"

    def test_near_unity_at_300hz_and_above(self):
        """Speech-range content (300Hz+) should pass through close to unity
        gain -- a 2nd-order Butterworth at 80Hz is well out of its stopband
        by 300Hz."""
        from whisper_aloud.audio.audio_processor import HighPass

        for freq in (300.0, 1000.0, 3000.0):
            hp = HighPass(cutoff_hz=80.0)
            tone = self._tone(freq)
            out = hp.process(tone.copy(), 16000)
            settled_in, settled_out = tone[2000:], out[2000:]
            ratio = self._rms(settled_out) / self._rms(settled_in)
            assert 0.9 < ratio <= 1.01, (
                f"expected near-unity gain at {freq}Hz, got ratio={ratio:.3f}"
            )

    def test_cutoff_zero_is_noop(self):
        """cutoff_hz=0 must be a pure passthrough (so callers never need to
        branch on whether the stage is enabled), matching
        stt.normalize.apply_highpass_filter's contract."""
        from whisper_aloud.audio.audio_processor import HighPass

        hp = HighPass(cutoff_hz=0)
        audio = np.random.default_rng(0).normal(size=1600).astype(np.float32)
        result = hp.process(audio.copy(), 16000)
        np.testing.assert_array_equal(result, audio)

    def test_state_continuity_matches_one_shot(self):
        """Processing in two back-to-back chunks (state carried via ``zi``)
        must match a single one-shot call over the concatenation, to
        floating-point precision -- a causal IIR filter's state-passing is
        exact, not approximate, when each call's ``zi`` continues the last."""
        from whisper_aloud.audio.audio_processor import HighPass

        rng = np.random.default_rng(1)
        audio = rng.normal(size=3200).astype(np.float32)

        one_shot = HighPass(cutoff_hz=80.0).process(audio.copy(), 16000)

        chunked_stage = HighPass(cutoff_hz=80.0)
        first = chunked_stage.process(audio[:1200].copy(), 16000)
        second = chunked_stage.process(audio[1200:].copy(), 16000)
        chunked = np.concatenate([first, second])

        np.testing.assert_allclose(one_shot, chunked, atol=1e-6)

    def test_reset_clears_state(self):
        """reset() must bring the filter back to its just-constructed state,
        so a reused instance behaves identically to a fresh one."""
        from whisper_aloud.audio.audio_processor import HighPass

        rng = np.random.default_rng(2)
        audio = rng.normal(size=1600).astype(np.float32)

        reused = HighPass(cutoff_hz=80.0)
        reused.process(audio.copy(), 16000)  # leaves zi state behind
        reused.reset()

        fresh = HighPass(cutoff_hz=80.0)

        probe = rng.normal(size=1600).astype(np.float32)
        out_reused = reused.process(probe.copy(), 16000)
        out_fresh = fresh.process(probe.copy(), 16000)
        np.testing.assert_array_equal(out_reused, out_fresh)

    def test_handles_empty_audio(self):
        from whisper_aloud.audio.audio_processor import HighPass

        hp = HighPass(cutoff_hz=80.0)
        empty = np.array([], dtype=np.float32)
        result = hp.process(empty, 16000)
        assert result.size == 0


class TestAudioPipeline:
    """Tests for the full audio processing pipeline."""

    def test_pipeline_reset_clears_agc_and_gate_state(self):
        """AudioPipeline.reset() must clear AGC's rolling RMS history/gain and
        NoiseGate's envelope/hold state, so a pipeline instance reused across
        recording sessions behaves identically to a freshly constructed one
        (WHISP-65 audit finding #1: a reused self._pipeline leaked AGC
        history between recordings, max abs diff ~0.281 before this fix).
        """
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False, limiter_enabled=True
        )
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        # A loud tone, which leaves non-trivial gate/AGC state behind.
        priming_audio = (np.sin(2 * np.pi * 220 * t) * 0.5).astype(np.float32)
        probe_audio = (np.sin(2 * np.pi * 440 * t) * 0.02).astype(np.float32)

        reused = AudioPipeline(config)
        reused.process(priming_audio.copy(), sample_rate=sr)  # leaves state behind
        reused.reset()

        fresh = AudioPipeline(config)

        out_reused = reused.process(probe_audio.copy(), sample_rate=sr)
        out_fresh = fresh.process(probe_audio.copy(), sample_rate=sr)
        np.testing.assert_allclose(out_reused, out_fresh, atol=1e-6)

    def test_pipeline_reset_on_instance_matches_a_separate_fresh_instance(self):
        """A single AGC/AudioPipeline instance, used once then reset(), must
        produce the exact same output as a brand-new instance given the same
        input -- i.e. reset() leaves no residue at all.
        """
        from whisper_aloud.audio.audio_processor import AGC, AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        # AGC in isolation.
        agc = AGC(target_db=-18.0)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        audio = (np.sin(2 * np.pi * 220 * t) * 0.3).astype(np.float32)
        agc.process(audio.copy(), sample_rate=sr)
        agc.reset()
        assert agc._current_gain == 1.0
        assert agc._history.size == 0

        fresh_agc = AGC(target_db=-18.0)
        out_reset = agc.process(audio.copy(), sample_rate=sr)
        out_fresh = fresh_agc.process(audio.copy(), sample_rate=sr)
        np.testing.assert_array_equal(out_reset, out_fresh)

        # Full pipeline.
        config = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False, limiter_enabled=True
        )
        pipeline = AudioPipeline(config)
        pipeline.process(audio.copy(), sample_rate=sr)
        pipeline.reset()
        fresh_pipeline = AudioPipeline(config)
        np.testing.assert_array_equal(
            pipeline.process(audio.copy(), sample_rate=sr),
            fresh_pipeline.process(audio.copy(), sample_rate=sr),
        )

    def test_pipeline_processes_all_stages(self):
        """Pipeline should run gate -> AGC -> limiter in order."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=True,
            agc_enabled=True,
            denoising_enabled=False,
            limiter_enabled=True,
        )
        pipeline = AudioPipeline(config)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        audio = (np.sin(2 * np.pi * 440 * t) * 0.1).astype(np.float32)
        result = pipeline.process(audio, sample_rate=sr)
        assert result.shape == audio.shape
        assert result.dtype == np.float32

    def test_pipeline_all_disabled_passthrough(self):
        """With all stages disabled (including the WHISP-83 high-pass, which
        must also be explicitly off here via highpass_hz=0), output equals
        input."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=False,
            agc_enabled=False,
            denoising_enabled=False,
            limiter_enabled=False,
            highpass_hz=0,
        )
        pipeline = AudioPipeline(config)
        audio = np.random.randn(1600).astype(np.float32) * 0.5
        result = pipeline.process(audio, sample_rate=16000)
        np.testing.assert_array_almost_equal(result, audio)

    def test_pipeline_is_stateful_across_chunks(self):
        """Pipeline should maintain state between process() calls."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(agc_enabled=True, noise_gate_enabled=False, limiter_enabled=False)
        pipeline = AudioPipeline(config)
        sr = 16000
        t = np.arange(1600, dtype=np.float32) / sr
        chunk = (np.sin(2 * np.pi * 440 * t) * 0.01).astype(np.float32)
        result1 = pipeline.process(chunk, sample_rate=sr)
        result2 = pipeline.process(chunk, sample_rate=sr)
        rms1 = np.sqrt(np.mean(result1 ** 2))
        rms2 = np.sqrt(np.mean(result2 ** 2))
        assert rms2 >= rms1 * 0.8, "AGC should maintain/increase gain across chunks"

    def test_pipeline_handles_empty_audio(self):
        """Empty audio should pass through without error."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig()
        pipeline = AudioPipeline(config)
        empty = np.array([], dtype=np.float32)
        result = pipeline.process(empty, sample_rate=16000)
        assert result.size == 0

    def test_pipeline_limiter_caps_agc_output(self):
        """Limiter should prevent AGC from producing clipping."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=False,
            agc_enabled=True,
            agc_target_db=-6.0,  # Aggressive target
            agc_max_gain_db=40.0,
            limiter_enabled=True,
            limiter_ceiling_db=-1.0,
        )
        pipeline = AudioPipeline(config)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        quiet = (np.sin(2 * np.pi * 440 * t) * 0.01).astype(np.float32)
        result = pipeline.process(quiet, sample_rate=sr)
        ceiling = 10 ** (-1.0 / 20.0)
        assert np.max(np.abs(result)) <= ceiling + 0.001, "Limiter should cap AGC output"

    def test_pipeline_with_denoiser_enabled(self):
        """Pipeline should include denoiser when enabled."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=False,
            agc_enabled=False,
            denoising_enabled=True,
            denoising_strength=0.3,
            limiter_enabled=False,
        )
        pipeline = AudioPipeline(config)
        assert pipeline._denoiser is not None
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        audio = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)
        result = pipeline.process(audio, sample_rate=sr)
        assert result.shape == audio.shape

    def test_pipeline_runs_highpass_before_gate(self):
        """WHISP-83: the high-pass stage must run first, ahead of NoiseGate,
        in both one-shot and streaming (process_chunk) modes -- so it sees
        raw audio, and NoiseGate/AGC/limiter all see the filtered signal.
        Verified by disabling every other stage and checking pipeline output
        matches HighPass run standalone.
        """
        from whisper_aloud.audio.audio_processor import AudioPipeline, HighPass
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=False,
            agc_enabled=False,
            denoising_enabled=False,
            limiter_enabled=False,
            highpass_hz=80.0,
        )
        sr = 16000
        rng = np.random.default_rng(3)
        audio = rng.normal(size=1600).astype(np.float32)

        expected = HighPass(cutoff_hz=80.0).process(audio.copy(), sr)

        one_shot = AudioPipeline(config).process(audio.copy(), sample_rate=sr)
        np.testing.assert_allclose(one_shot, expected, atol=1e-6)

        streaming = AudioPipeline(config, streaming=True).process_chunk(
            audio.copy(), sample_rate=sr
        )
        np.testing.assert_allclose(streaming, expected, atol=1e-6)

    def test_pipeline_highpass_default_does_not_regress_clean_audio(self):
        """The default 80Hz high-pass (default highpass_hz=80) must not
        meaningfully change pipeline *loudness* behaviour on audio *without*
        low-frequency rumble -- e.g. the existing speech-range synthetic test
        signal -- versus the pre-WHISP-83 pipeline (highpass_hz=0).

        Compared by RMS level (not raw sample-for-sample diff): a 2nd-order
        Butterworth highpass is near-unity *magnitude* at 440Hz (5.5x the
        80Hz cutoff) but still introduces a real phase shift there, which by
        itself can produce a large sample-wise difference between two
        otherwise-equal-energy sine waves (and gets amplified further by the
        gate's hysteresis/attack timing and AGC's asymmetric attack/release
        tracking) without actually changing the audio's audible loudness or
        the pipeline's effectiveness. RMS is insensitive to that phase shift
        and is what AGC itself targets, so it is the right level to compare
        "no regression" at.
        """
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        audio = (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32)

        config_with_hp = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False,
            limiter_enabled=True, highpass_hz=80.0,
        )
        config_without_hp = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False,
            limiter_enabled=True, highpass_hz=0,
        )

        with_hp = AudioPipeline(config_with_hp).process(audio.copy(), sample_rate=sr)
        without_hp = AudioPipeline(config_without_hp).process(audio.copy(), sample_rate=sr)

        def rms(x):
            return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))

        rms_with, rms_without = rms(with_hp), rms(without_hp)
        assert rms_with == pytest.approx(rms_without, rel=0.05), (
            f"expected near-equal RMS loudness with/without highpass on clean audio: "
            f"with={rms_with:.4f}, without={rms_without:.4f}"
        )


class TestDenoiser:
    """Tests for spectral denoising."""

    def test_denoiser_processes_audio(self):
        """Denoiser should process audio and return same shape."""
        from whisper_aloud.audio.audio_processor import Denoiser

        denoiser = Denoiser(strength=0.5)
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        audio = (np.sin(2 * np.pi * 440 * t) * 0.3 + np.random.randn(sr).astype(np.float32) * 0.05).astype(np.float32)
        result = denoiser.process(audio, sample_rate=sr)
        assert result.shape == audio.shape
        assert result.dtype == np.float32

    def test_denoiser_graceful_without_noisereduce(self):
        """If noisereduce not available, should pass through unchanged."""
        from whisper_aloud.audio.audio_processor import Denoiser

        denoiser = Denoiser(strength=0.5)
        audio = np.random.randn(1600).astype(np.float32) * 0.3
        # Simulate noisereduce not being available
        denoiser._noisereduce = None
        result = denoiser.process(audio, sample_rate=16000)
        np.testing.assert_array_equal(result, audio)

    def test_denoiser_handles_empty_audio(self):
        """Empty audio should pass through."""
        from whisper_aloud.audio.audio_processor import Denoiser

        denoiser = Denoiser()
        empty = np.array([], dtype=np.float32)
        result = denoiser.process(empty, sample_rate=16000)
        assert result.size == 0


class TestAudioProcessingConfig:
    """Tests for AudioProcessingConfig in WhisperAloudConfig."""

    def test_config_includes_audio_processing(self):
        """WhisperAloudConfig should have audio_processing section."""
        from whisper_aloud.config import AudioProcessingConfig, WhisperAloudConfig

        config = WhisperAloudConfig()
        assert isinstance(config.audio_processing, AudioProcessingConfig)
        assert config.audio_processing.agc_enabled is True

    def test_config_round_trip(self):
        """audio_processing should survive to_dict/from_dict round trip."""
        from whisper_aloud.config import WhisperAloudConfig

        config = WhisperAloudConfig()
        config.audio_processing.agc_target_db = -12.0
        config.audio_processing.noise_gate_enabled = False
        d = config.to_dict()
        restored = WhisperAloudConfig.from_dict(d)
        assert restored.audio_processing.agc_target_db == -12.0
        assert restored.audio_processing.noise_gate_enabled is False


class TestRecorderPipelineIntegration:
    """Tests for AudioPipeline integration in AudioRecorder."""

    def test_recorder_accepts_processing_config(self):
        """AudioRecorder should accept AudioProcessingConfig."""

        from whisper_aloud.audio.recorder import AudioRecorder
        from whisper_aloud.config import AudioConfig, AudioProcessingConfig

        config = AudioConfig()
        proc_config = AudioProcessingConfig(agc_enabled=True)
        recorder = AudioRecorder(config, processing_config=proc_config)
        assert recorder._pipeline is not None

    def test_recorder_creates_pipeline_from_config(self):
        """Recorder should create AudioPipeline with given config."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.audio.recorder import AudioRecorder
        from whisper_aloud.config import AudioConfig, AudioProcessingConfig

        config = AudioConfig()
        proc_config = AudioProcessingConfig(agc_enabled=False, noise_gate_enabled=False)
        recorder = AudioRecorder(config, processing_config=proc_config)
        assert isinstance(recorder._pipeline, AudioPipeline)
        assert recorder._pipeline._agc is None
        assert recorder._pipeline._gate is None

    def test_recorder_works_without_processing_config(self):
        """Recorder should work with default pipeline if no config given."""
        from whisper_aloud.audio.recorder import AudioRecorder
        from whisper_aloud.config import AudioConfig

        config = AudioConfig()
        recorder = AudioRecorder(config)
        assert recorder._pipeline is not None

    def test_recorder_stop_uses_pipeline(self):
        """Recorder.stop() should use AudioPipeline.process() instead of process_recording()."""
        from unittest.mock import MagicMock

        from whisper_aloud.audio.recorder import AudioRecorder, RecordingState
        from whisper_aloud.config import AudioConfig, AudioProcessingConfig

        config = AudioConfig()
        proc_config = AudioProcessingConfig(
            noise_gate_enabled=False, agc_enabled=False,
            denoising_enabled=False, limiter_enabled=False,
        )
        recorder = AudioRecorder(config, processing_config=proc_config)

        # Mock pipeline.process to verify it gets called
        recorder._pipeline = MagicMock()
        recorder._pipeline.process.return_value = np.zeros(1600, dtype=np.float32)

        # Put recorder into recording state and add frames
        recorder._set_state(RecordingState.RECORDING)
        recorder._frames = [np.zeros(1600, dtype=np.float32)]
        recorder._start_time = 0.0

        recorder.stop()
        recorder._pipeline.process.assert_called_once()

    def test_recorder_stop_applies_mono_and_resample(self):
        """Recorder.stop() should convert stereo to mono and resample before pipeline."""
        from unittest.mock import MagicMock

        from whisper_aloud.audio.recorder import AudioRecorder, RecordingState
        from whisper_aloud.config import AudioConfig, AudioProcessingConfig

        # Use 48kHz stereo config
        config = AudioConfig(sample_rate=48000, channels=2)
        proc_config = AudioProcessingConfig(
            noise_gate_enabled=False, agc_enabled=False,
            denoising_enabled=False, limiter_enabled=False,
        )
        recorder = AudioRecorder(config, processing_config=proc_config)

        # Mock pipeline
        recorder._pipeline = MagicMock()
        recorder._pipeline.process.side_effect = lambda audio, sample_rate: audio

        # Put recorder into recording state with stereo frames
        recorder._set_state(RecordingState.RECORDING)
        stereo_frame = np.zeros((4800, 2), dtype=np.float32)
        recorder._frames = [stereo_frame]
        recorder._start_time = 0.0

        recorder.stop()

        # Pipeline should have been called with mono, 16kHz audio
        call_args = recorder._pipeline.process.call_args
        passed_audio = call_args[0][0] if call_args[0] else call_args[1]['audio']
        assert passed_audio.ndim == 1, "Audio should be mono"
        assert call_args[1].get('sample_rate', call_args[0][1] if len(call_args[0]) > 1 else None) == 16000
