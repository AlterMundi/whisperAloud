"""WHISP-34 spike: can the stateful AudioPipeline feed incremental streaming ASR?

These tests probe two properties with synthetic signals (no network, no models):

1. Causality / no look-ahead: does a stage's output at sample i depend only on
   samples <= i within the current process() call? (``*_no_lookahead`` tests)
2. Chunk-invariance: does processing the same audio split into N small chunks
   (each a separate process() call, carrying state forward) produce the same
   output as a single one-shot process() call over the whole buffer -- which
   is how ``AudioRecorder.stop()`` invokes the pipeline today (see
   ``tests/test_audio_pipeline.py::test_recorder_stop_uses_pipeline``, which
   asserts ``pipeline.process`` is called exactly once per recording)?

Chunk sizes 80/160/560/1120 ms match the Nemotron 3.5 Streaming att_context_size
options evaluated in docs/research/2026-10-01-sota-stt-offline/report.md and
notes/streaming.md for WHISP-34/37.

Numbers quoted in test comments/xfail reasons come from a one-off run of this
exact signal (see scripts/bench_pipeline_chunk_latency.py's sibling probe) and
may shift slightly with numpy/scipy versions, hence the tolerance margins.
"""

import numpy as np
import pytest

from whisper_aloud.audio.audio_processor import (
    AGC,
    AudioPipeline,
    AudioProcessor,
    Denoiser,
    HighPass,
    NoiseGate,
    PeakLimiter,
    StreamingResampler,
)
from whisper_aloud.config import AudioProcessingConfig

SR = 16000
CHUNK_SIZES_MS = (80, 160, 560, 1120)


# ---------------------------------------------------------------------------
# Synthetic signal: silence / quiet tone+noise / loud tone / silence / noise burst.
# Duration (2.24s = 35840 samples @ 16kHz) divides evenly by every chunk size
# above (1280 / 2560 / 8960 / 17920 samples) so chunked processing never needs
# a ragged final chunk, which would otherwise confound the comparison.
# ---------------------------------------------------------------------------

def make_signal(duration_s: float = 2.24, sr: int = SR, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    sig = np.zeros(n, dtype=np.float64)

    def seg(start_s: float, end_s: float) -> slice:
        return slice(int(start_s * sr), int(end_s * sr))

    # 0.00-0.32s: silence
    quiet = seg(0.32, 0.96)
    sig[quiet] += 0.02 * np.sin(2 * np.pi * 440 * t[quiet])  # ~-34 dBFS tone + noise
    loud = seg(0.96, 1.60)
    sig[loud] += 0.5 * np.sin(2 * np.pi * 220 * t[loud])  # ~-6 dBFS tone
    # 1.60-1.92s: silence
    burst = seg(1.92, 2.24)
    sig[burst] += rng.normal(scale=0.08, size=burst.stop - burst.start)  # noise burst

    sig += rng.normal(scale=0.002, size=n)  # constant low-level noise floor
    assert n % 17920 == 0, "signal length must divide evenly by every tested chunk size"
    return sig.astype(np.float32)


def run_chunked(make_stage, call, audio: np.ndarray, chunk_ms: int):
    """Feed ``audio`` through a fresh stage instance in fixed-size chunks."""
    stage = make_stage()
    chunk_samples = int(SR * chunk_ms / 1000)
    assert len(audio) % chunk_samples == 0, "test signal must divide evenly by chunk size"
    out = [call(stage, audio[start:start + chunk_samples]) for start in range(0, len(audio), chunk_samples)]
    return np.concatenate(out)


def run_one_shot(make_stage, call, audio: np.ndarray):
    return call(make_stage(), audio)


def max_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))))


# ---------------------------------------------------------------------------
# 1. Causality: does changing the tail of the buffer change earlier output?
# ---------------------------------------------------------------------------

class TestNoLookahead:
    """A stage is causal if sample i of its output never depends on input
    samples after i *within a single process() call*. We test this by
    mutating everything after a cut point and checking the output before
    the cut point is unaffected.
    """

    CUT_S = 1.5  # seconds into the 2.24s signal

    def _mutated_tail(self, audio: np.ndarray, seed: int = 99) -> np.ndarray:
        rng = np.random.default_rng(seed)
        cut = int(self.CUT_S * SR)
        mutated = audio.copy()
        mutated[cut:] = rng.normal(scale=0.3, size=len(audio) - cut).astype(np.float32)
        return mutated

    def test_noise_gate_no_lookahead(self):
        audio = make_signal()
        cut = int(self.CUT_S * SR)
        out1 = NoiseGate(threshold_db=-40.0).process(audio.copy(), SR)
        out2 = NoiseGate(threshold_db=-40.0).process(self._mutated_tail(audio), SR)
        np.testing.assert_allclose(out1[:cut], out2[:cut], atol=1e-9)

    def test_agc_no_lookahead(self):
        audio = make_signal()
        cut = int(self.CUT_S * SR)
        out1 = AGC(target_db=-18.0).process(audio.copy(), SR)
        out2 = AGC(target_db=-18.0).process(self._mutated_tail(audio), SR)
        np.testing.assert_allclose(out1[:cut], out2[:cut], atol=1e-9)

    def test_peak_limiter_no_lookahead(self):
        audio = make_signal()
        cut = int(self.CUT_S * SR)
        out1 = PeakLimiter().process(audio.copy())
        out2 = PeakLimiter().process(self._mutated_tail(audio))
        np.testing.assert_allclose(out1[:cut], out2[:cut], atol=1e-9)

    def test_highpass_no_lookahead(self):
        """HighPass (WHISP-83) is a causal IIR (sosfilt + persistent zi):
        mutating everything after a cut point must not change the output
        before the cut."""
        audio = make_signal()
        cut = int(self.CUT_S * SR)
        out1 = HighPass(cutoff_hz=80.0).process(audio.copy(), SR)
        out2 = HighPass(cutoff_hz=80.0).process(self._mutated_tail(audio), SR)
        np.testing.assert_allclose(out1[:cut], out2[:cut], atol=1e-9)

    def test_resample_has_lookahead_xfail(self):
        """scipy.signal.resample (used in AudioProcessor.resample, and by
        AudioRecorder.stop() whenever config.audio.sample_rate != 16000) is
        FFT-based over the *entire* input: every output sample depends on
        every input sample. Mutating the tail changes the head of the
        resampled output, which a causal/streamable resampler must not do.
        """
        audio48 = make_signal(sr=48000)
        cut_src = int(self.CUT_S * 48000)
        rng = np.random.default_rng(99)
        mutated = audio48.copy()
        mutated[cut_src:] = rng.normal(scale=0.3, size=len(audio48) - cut_src).astype(np.float32)

        out1 = AudioProcessor.resample(audio48, 48000, 16000)
        out2 = AudioProcessor.resample(mutated, 48000, 16000)

        head = int(1.0 * 16000)  # comfortably before the mutation point
        # A causal resampler would leave this head segment untouched. The
        # real one does not -- this assertion documents that non-causality
        # (it is expected to find a difference, i.e. "xfail" the causal claim).
        with pytest.raises(AssertionError):
            np.testing.assert_allclose(out1[:head], out2[:head], atol=1e-6)


# ---------------------------------------------------------------------------
# 2. Chunk-invariance: chunked (stateful, incremental) vs one-shot (today's usage)
# ---------------------------------------------------------------------------

class TestChunkInvariance:
    """Compare N-chunk incremental processing against the single-call
    processing AudioRecorder.stop() does today. A property "holds" when
    chunked output matches one-shot output within a tight tolerance.
    """

    @pytest.mark.parametrize("chunk_ms", CHUNK_SIZES_MS)
    def test_peak_limiter_is_chunk_invariant(self, chunk_ms):
        """Stateless, per-sample, no window -> must be exactly chunk-invariant."""
        audio = make_signal()
        call = lambda stage, chunk: stage.process(chunk)
        one_shot = run_one_shot(PeakLimiter, call, audio)
        chunked = run_chunked(PeakLimiter, call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 1e-6

    @pytest.mark.parametrize("chunk_ms", CHUNK_SIZES_MS)
    def test_noise_gate_is_approximately_chunk_invariant(self, chunk_ms):
        """NoiseGate carries hysteresis/hold/envelope state across calls (good),
        but its 25ms RMS window (audio_processor.py NoiseGate.process, the
        cumsum-based smoothing ~lines 67-77) is recomputed from an *expanding*
        window at the start of every process() call instead of continuing the
        previous call's tail. The effect only shows up near a gate-state
        transition that happens to fall close to a chunk boundary: for this
        signal that happens at 80/160ms chunks (max abs diff ~0.023, bounded by
        one hysteresis-zone's worth of gain error) and not at 560/1120ms
        (diff == 0, because no transition lands near those boundaries for this
        particular signal). The property "holds" only approximately and is
        signal/boundary-alignment dependent -- not a correctness guarantee.
        """
        audio = make_signal()
        make_stage = lambda: NoiseGate(threshold_db=-40.0)
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(make_stage, call, audio)
        chunked = run_chunked(make_stage, call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 0.03

    @pytest.mark.parametrize("chunk_ms", CHUNK_SIZES_MS)
    def test_highpass_is_chunk_invariant(self, chunk_ms):
        """HighPass (WHISP-83) carries its sosfilt ``zi`` state exactly
        across process() calls -- like PeakLimiter and the fixed AGC, this
        makes chunked and one-shot output match to floating-point precision
        at every tested chunk size, not just approximately.
        """
        audio = make_signal()
        make_stage = lambda: HighPass(cutoff_hz=80.0)
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(make_stage, call, audio)
        chunked = run_chunked(make_stage, call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 1e-6

    @pytest.mark.parametrize("chunk_ms", CHUNK_SIZES_MS)
    def test_agc_is_chunk_invariant(self, chunk_ms):
        """WHISP-65 fix: AGC now carries a rolling history of raw samples
        across process() calls (``AGC._history``, seeded with up to
        ``window_samples - 1`` trailing samples from the previous call) so
        the windowed RMS it computes at the start of a chunk matches what a
        one-shot call would have seen at that same position. Reconstructing
        the exact windowed RMS this way makes chunked and one-shot output
        match to floating-point precision (observed max abs diff is exactly
        0.0 on this signal at every tested chunk size) rather than the
        ~0.045-0.86 divergence from before the fix (see git history /
        WHISP-34 findings for the previous xfail numbers). Tolerance is set
        at 1e-6 — tighter than the signal's own float32 precision — rather
        than 0.0 to allow for summation-order floating point noise.
        """
        audio = make_signal()
        make_stage = lambda: AGC(target_db=-18.0)
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(make_stage, call, audio)
        chunked = run_chunked(make_stage, call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 1e-6

    @pytest.mark.parametrize("chunk_ms", CHUNK_SIZES_MS)
    def test_full_streaming_pipeline_is_chunk_invariant(self, chunk_ms):
        """End-to-end streaming AudioPipeline (highpass -> gate -> AGC ->
        limiter, no denoiser) is now chunk-invariant within a tolerance
        dominated entirely by NoiseGate's own boundary-dependent
        approximation (see ``test_noise_gate_is_approximately_chunk_invariant``,
        bounded at 0.03 for the gate alone) — now that AGC is exact (previous
        test) and HighPass is exact (WHISP-83, its own test above), the
        gate's small hysteresis-boundary error is the only remaining source
        of divergence, amplified by subsequent AGC gain and the limiter's
        tanh saturation to an observed max abs diff of ~0.056 at 80/160ms
        chunk sizes on this signal (0.0 at 560/1120ms, where no gate
        transition lands near a chunk boundary). Uses the new
        ``AudioPipeline(..., streaming=True)`` / ``process_chunk()`` entry
        point (WHISP-65), which is what a streaming ASR caller would use
        instead of ``process()``. ``highpass_hz=80`` is passed explicitly so
        this test's intent (and tolerance) stays correct even if the
        config's own default ever changes.
        """
        audio = make_signal()
        config = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=True,
            limiter_enabled=True, highpass_hz=80.0,
        )
        call = lambda stage, chunk: stage.process_chunk(chunk, SR)
        one_shot = run_one_shot(lambda: AudioPipeline(config, streaming=True), call, audio)
        chunked = run_chunked(lambda: AudioPipeline(config, streaming=True), call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 0.1

    def test_denoiser_not_chunk_invariant_xfail(self):
        """noisereduce.reduce_noise(y=audio, sr=sr, stationary=True) (Denoiser.process,
        audio_processor.py) estimates its noise profile from -- and spectrally
        gates -- the *entire* array passed to a single call, with no persisted
        state between calls. Calling it once per small chunk means every chunk
        gets its own, statistically unstable noise-floor estimate from ~tens
        of ms of audio, plus STFT edge/padding artifacts at every chunk
        boundary. This is structurally a whole-buffer, non-causal operation,
        not a streaming one.
        """
        pytest.importorskip("noisereduce", reason="optional dependency not installed in this environment")
        audio = make_signal()
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(lambda: Denoiser(strength=0.5), call, audio)
        chunked = run_chunked(lambda: Denoiser(strength=0.5), call, audio, 560)
        assert max_abs_diff(one_shot, chunked) < 1e-6, (
            "expected this to fail -- Denoiser is not chunk-invariant (see docstring)"
        )

    def test_denoiser_is_noop_without_noisereduce(self):
        """Sanity check for *this* environment: noisereduce is an optional
        dependency and is not installed here, so Denoiser degrades to a
        passthrough (already covered by test_audio_pipeline.py). A passthrough
        is trivially chunk-invariant, which is why the full-pipeline tests
        above leave denoising_enabled=False rather than relying on this
        incidental fact.
        """
        denoiser = Denoiser(strength=0.5)
        assert denoiser._noisereduce is None, "test assumes noisereduce is not installed"
        audio = make_signal()
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(lambda: Denoiser(strength=0.5), call, audio)
        chunked = run_chunked(lambda: Denoiser(strength=0.5), call, audio, 80)
        np.testing.assert_array_equal(one_shot, chunked)


# ---------------------------------------------------------------------------
# 3. Explicit streaming mode (WHISP-65)
# ---------------------------------------------------------------------------

class TestExplicitStreamingMode:
    """AudioPipeline(..., streaming=True) / process_chunk() give callers an
    explicit, documented streaming entry point distinct from the one-shot
    process() path used by AudioRecorder.stop() today.
    """

    def test_streaming_pipeline_never_builds_denoiser(self):
        """streaming=True must skip the Denoiser even if config enables it --
        Denoiser is structurally a whole-buffer operation (see module
        docstring / WHISP-34 findings) and must never be reachable per-chunk.
        """
        config = AudioProcessingConfig(denoising_enabled=True)
        pipeline = AudioPipeline(config, streaming=True)
        assert pipeline._denoiser is None

    def test_non_streaming_pipeline_keeps_denoiser_when_enabled(self):
        """Default (streaming=False) one-shot behaviour is unchanged."""
        config = AudioProcessingConfig(denoising_enabled=True)
        pipeline = AudioPipeline(config)
        assert pipeline.streaming is False
        assert pipeline._denoiser is not None

    def test_process_chunk_requires_streaming_mode(self):
        """Calling process_chunk() on a non-streaming pipeline is a usage
        error -- it must fail loudly rather than silently behave like
        process().
        """
        config = AudioProcessingConfig()
        pipeline = AudioPipeline(config, streaming=False)
        chunk = np.zeros(1600, dtype=np.float32)
        with pytest.raises(ValueError, match="streaming=True"):
            pipeline.process_chunk(chunk, SR)

    def test_process_chunk_matches_process_in_streaming_mode(self):
        """process_chunk() is a thin wrapper: on a streaming pipeline it must
        behave exactly like process() (same stages, same output).
        """
        config = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False, limiter_enabled=True
        )
        audio = make_signal()
        out_via_process = AudioPipeline(config, streaming=True).process(audio.copy(), SR)
        out_via_chunk = AudioPipeline(config, streaming=True).process_chunk(audio.copy(), SR)
        np.testing.assert_array_equal(out_via_process, out_via_chunk)

    def test_one_shot_process_output_unchanged_by_agc_history_fix(self):
        """Regression guard: the AGC rolling-history fix must not change the
        output of a single one-shot process() call (the path
        AudioRecorder.stop() uses today) relative to the pre-fix algorithm.
        A fresh AGC/AudioPipeline instance has empty history on its first
        call, so the windowed RMS computation is mathematically identical to
        the old "recompute cumsum over just this call's audio" behaviour.
        This pins that down with a fixed signal and a fixed expected output
        checksum-style comparison against an independently reimplemented
        "old" one-shot AGC.
        """

        def old_agc_process(audio, sample_rate, target_db=-18.0):
            target_linear = 10 ** (target_db / 20.0)
            max_gain = 10 ** (20.0 / 20.0)
            min_gain = 10 ** (-10.0 / 20.0)
            attack_ms, release_ms, window_ms = 10.0, 100.0, 300.0
            n = len(audio)
            window_samples = max(1, int(window_ms * sample_rate / 1000.0))
            sq = audio.astype(np.float64) ** 2
            cumsum = np.cumsum(sq)
            rms_arr = np.empty(n, dtype=np.float64)
            if n <= window_samples:
                rms_arr[:] = np.sqrt(cumsum / np.arange(1, n + 1))
            else:
                rms_arr[:window_samples] = np.sqrt(cumsum[:window_samples] / np.arange(1, window_samples + 1))
                rms_arr[window_samples:] = np.sqrt(
                    (cumsum[window_samples:] - cumsum[:-window_samples]) / window_samples
                )
            desired_gain = np.where(
                rms_arr > 1e-8,
                np.clip(target_linear / np.maximum(rms_arr, 1e-8), min_gain, max_gain),
                1.0,
            )
            block_size = max(1, int(sample_rate * 0.010))
            tau_attack = max(1.0, attack_ms * sample_rate / 1000.0)
            tau_release = max(1.0, release_ms * sample_rate / 1000.0)
            block_attack_coeff = np.exp(-block_size / tau_attack)
            block_release_coeff = np.exp(-block_size / tau_release)
            gain_arr = np.empty(n, dtype=np.float64)
            gain = 1.0
            for start in range(0, n, block_size):
                end = min(start + block_size, n)
                block_target = float(desired_gain[start:end].mean())
                coeff = block_attack_coeff if block_target < gain else block_release_coeff
                gain = coeff * gain + (1 - coeff) * block_target
                gain_arr[start:end] = gain
            return (audio * gain_arr).astype(np.float32)

        audio = make_signal()
        old_output = old_agc_process(audio, SR)
        new_output = AGC(target_db=-18.0).process(audio.copy(), SR)
        np.testing.assert_allclose(old_output, new_output, atol=1e-6)


# ---------------------------------------------------------------------------
# 4. Streaming resampler continuity (WHISP-65)
# ---------------------------------------------------------------------------

class TestStreamingResamplerContinuity:
    """StreamingResampler must converge to one-shot resample_poly output when
    fed the same signal in chunks, unlike AudioProcessor.resample() (FFT-based,
    non-causal -- see TestNoLookahead.test_resample_has_lookahead_xfail).
    """

    SR_IN = 48000
    SR_OUT = 16000

    def _make_tone(self, duration_s: float = 2.24, seed: int = 7) -> np.ndarray:
        rng = np.random.default_rng(seed)
        n = int(duration_s * self.SR_IN)
        t = np.arange(n) / self.SR_IN
        sig = 0.3 * np.sin(2 * np.pi * 220 * t) + 0.05 * rng.normal(size=n)
        return sig.astype(np.float32)

    @pytest.mark.parametrize("chunk_ms", CHUNK_SIZES_MS)
    def test_chunked_resampling_matches_one_shot(self, chunk_ms):
        from scipy import signal as scipy_signal

        audio = self._make_tone()
        one_shot = scipy_signal.resample_poly(audio, self.SR_OUT, self.SR_IN).astype(np.float32)

        chunk_samples = int(self.SR_IN * chunk_ms / 1000)
        resampler = StreamingResampler(self.SR_IN, self.SR_OUT)
        pieces = [
            resampler.process(audio[start:start + chunk_samples])
            for start in range(0, len(audio), chunk_samples)
        ]
        chunked = np.concatenate(pieces)

        assert len(chunked) == len(one_shot)
        # FIR-overlap approximation, not bit-exact: bounded well under the
        # signal's own amplitude (observed max abs diff ~0.013-0.029 on this
        # signal across the tested chunk sizes).
        assert max_abs_diff(one_shot, chunked) < 0.05

    def test_passthrough_when_rate_already_16k(self):
        """No resampling work (identity) is expected to be skipped by callers
        when orig_rate == target_rate; StreamingResampler itself still works
        correctly (up=down=1 is a no-op resample_poly call).
        """
        resampler = StreamingResampler(16000, 16000)
        chunk = self._make_tone(duration_s=0.1).astype(np.float32)
        out = resampler.process(chunk)
        np.testing.assert_allclose(out, chunk, atol=1e-5)
