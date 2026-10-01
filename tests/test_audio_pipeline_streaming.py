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
    NoiseGate,
    PeakLimiter,
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

    @pytest.mark.parametrize(
        "chunk_ms",
        [
            pytest.param(
                80,
                marks=pytest.mark.xfail(
                    reason="AGC window_ms=300 > chunk; RMS falls into the n<=window_samples "
                    "branch (audio_processor.py AGC.process ~lines 182-183) and is recomputed "
                    "from scratch every chunk with zero memory of prior audio. Observed max "
                    "abs diff vs one-shot ~0.86 on this signal.",
                    strict=False,
                ),
            ),
            pytest.param(
                160,
                marks=pytest.mark.xfail(
                    reason="Same cause as the 80ms case (chunk < window_ms=300). Observed max "
                    "abs diff vs one-shot ~0.83.",
                    strict=False,
                ),
            ),
            pytest.param(
                560,
                marks=pytest.mark.xfail(
                    reason="chunk > window_ms=300 so part of each chunk uses a real sliding "
                    "window, but the first ~300ms of every chunk still ignores the previous "
                    "chunk's tail, and the 10ms-block attack/release smoothing compounds the "
                    "error at the boundary. Observed max abs diff vs one-shot ~1.15 (worse than "
                    "80/160ms here -- boundary alignment with the loud-tone transient matters "
                    "more than raw chunk/window ratio).",
                    strict=False,
                ),
            ),
            pytest.param(
                1120,
                marks=pytest.mark.xfail(
                    reason="Best case tested (chunk >> window_ms): still not invariant, "
                    "observed max abs diff vs one-shot ~0.045 (vs <1e-6 tolerance asserted "
                    "here) because the RMS window still resets at the top of every chunk.",
                    strict=False,
                ),
            ),
        ],
    )
    def test_agc_is_not_chunk_invariant(self, chunk_ms):
        """Documents that AGC chunk-invariance does NOT hold at any tested
        chunk size, including 1120ms. Root cause: AGC.process() rebuilds its
        RMS estimate from the cumsum of only the samples it was just given
        (audio_processor.py ~lines 178-188) instead of carrying a rolling
        history buffer across calls, so the "desired_gain" target the
        attack/release smoother chases is wrong for roughly the first
        window_ms of every chunk after the first.
        """
        audio = make_signal()
        make_stage = lambda: AGC(target_db=-18.0)
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(make_stage, call, audio)
        chunked = run_chunked(make_stage, call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 1e-6

    @pytest.mark.parametrize(
        "chunk_ms",
        [
            pytest.param(80, marks=pytest.mark.xfail(reason="AGC stage dominates; see test_agc_is_not_chunk_invariant.", strict=False)),
            pytest.param(160, marks=pytest.mark.xfail(reason="AGC stage dominates; see test_agc_is_not_chunk_invariant.", strict=False)),
            pytest.param(560, marks=pytest.mark.xfail(reason="AGC stage dominates; see test_agc_is_not_chunk_invariant.", strict=False)),
            pytest.param(1120, marks=pytest.mark.xfail(reason="AGC stage dominates; see test_agc_is_not_chunk_invariant.", strict=False)),
        ],
    )
    def test_full_pipeline_is_not_chunk_invariant(self, chunk_ms):
        """End-to-end AudioPipeline (gate -> AGC -> limiter, denoiser off since
        the optional noisereduce dependency is not installed here) inherits
        AGC's non-invariance: observed max abs diff ranges ~0.045 (1120ms) to
        ~0.54 (560ms) against a <1e-6 tolerance. This is the configuration
        that would actually feed a streaming ASR engine if the pipeline were
        called per-chunk instead of once at stop() as it is today.
        """
        audio = make_signal()
        config = AudioProcessingConfig(
            noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False, limiter_enabled=True
        )
        call = lambda stage, chunk: stage.process(chunk, SR)
        one_shot = run_one_shot(lambda: AudioPipeline(config), call, audio)
        chunked = run_chunked(lambda: AudioPipeline(config), call, audio, chunk_ms)
        assert max_abs_diff(one_shot, chunked) < 1e-6

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
