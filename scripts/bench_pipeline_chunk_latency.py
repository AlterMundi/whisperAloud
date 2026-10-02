#!/usr/bin/env python3
"""WHISP-34: CPU time per 560ms chunk through AudioPipeline
(highpass -> gate -> AGC -> limiter).

Not a test -- a short, single-threaded timing probe on synthetic audio (no
microphone, no models, no network). Run manually:

    source ~/.venvs/whisper_aloud/bin/activate
    python scripts/bench_pipeline_chunk_latency.py

Prints median/p95 wall time per 560ms chunk, and the real-time ratio
(chunk duration / processing time) so it's obvious this is nowhere near a
bottleneck relative to the 80-1120ms Nemotron chunk sizes it would need to
keep up with in a streaming configuration.

WHISP-83 update: the pipeline can also run a leading HighPass stage
(AudioProcessingConfig.highpass_hz, explicitly enabled below at 80Hz so
this bench measures its cost -- it defaults to 0/off for live dictation,
see that field's docstring for why). Enabling it added roughly 0.03-0.05ms
per 560ms chunk on a dev machine (a single 2nd-order sosfilt call), nowhere
near enough to change the real-time ratio's order of magnitude.
"""

import time

import numpy as np

from whisper_aloud.audio.audio_processor import AudioPipeline
from whisper_aloud.config import AudioProcessingConfig

SR = 16000
CHUNK_MS = 560
N_CHUNKS = 50


def main() -> None:
    chunk_samples = int(SR * CHUNK_MS / 1000)
    rng = np.random.default_rng(0)

    config = AudioProcessingConfig(
        noise_gate_enabled=True, agc_enabled=True, denoising_enabled=False, limiter_enabled=True,
        highpass_hz=80.0,  # off by default for live dictation; enabled here to measure its cost
    )
    pipeline = AudioPipeline(config)

    # Warm up (first call may pay for lazy imports / numpy JIT-ish cache warming).
    warm_chunk = (0.1 * rng.standard_normal(chunk_samples)).astype(np.float32)
    pipeline.process(warm_chunk, SR)

    durations_s = []
    for _ in range(N_CHUNKS):
        chunk = (0.1 * rng.standard_normal(chunk_samples)).astype(np.float32)
        start = time.perf_counter()
        pipeline.process(chunk, SR)
        durations_s.append(time.perf_counter() - start)

    durations_ms = np.array(durations_s) * 1000.0
    median_ms = float(np.median(durations_ms))
    p95_ms = float(np.percentile(durations_ms, 95))
    rtf = CHUNK_MS / median_ms if median_ms > 0 else float("inf")

    print(f"AudioPipeline.process() on {CHUNK_MS}ms chunks ({chunk_samples} samples @ {SR}Hz)")
    print(f"  n={N_CHUNKS} chunks, single-threaded")
    print(f"  median: {median_ms:.3f} ms/chunk")
    print(f"  p95:    {p95_ms:.3f} ms/chunk")
    print(f"  real-time ratio (chunk_ms / median_ms): {rtf:.1f}x")


if __name__ == "__main__":
    main()
