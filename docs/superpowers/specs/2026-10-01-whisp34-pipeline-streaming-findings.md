# WHISP-34 spike: can AudioPipeline feed streaming ASR?

Status: spike, no production code changed. See `tests/test_audio_pipeline_streaming.py`
and `scripts/bench_pipeline_chunk_latency.py` for the evidence behind every claim
below. Context: `docs/research/2026-10-01-sota-stt-offline/notes/streaming.md`
(Nemotron 3.5 ASR Streaming 0.6B, chunks of 80/160/320/560/1120 ms).

## 1. How the pipeline is invoked today

`AudioRecorder` (`src/whisper_aloud/audio/recorder.py`):

- `start()` opens a `sounddevice.InputStream` with
  `blocksize=int(config.chunk_duration * config.sample_rate)`
  (`config.chunk_duration` default **0.1 s**, `config.sample_rate` default
  **16000 Hz** — `src/whisper_aloud/config.py:117,120`). The audio callback
  (`_audio_callback`, recorder.py:103) just appends each ~100 ms raw chunk to
  an in-memory list (`self._frames.append(audio_chunk)`, line 123) and updates
  a level meter. It never touches `AudioPipeline`.
- `stop()` (recorder.py ~189-260) concatenates every captured frame into one
  buffer, converts stereo→mono, resamples to 16 kHz if the device rate
  differs, optionally trims silence, and only then calls
  `self._pipeline.process(audio, sample_rate=16000)` **once**, on the entire
  recording (recorder.py:252). `AudioPipeline.process()` itself
  (`audio_processor.py:510-521`) just chains `gate → agc → denoiser →
  limiter`, each called once on the whole array.

So today there is exactly one `process()` call per recording, over the full
buffer, at 16 kHz. There is no per-callback (every ~100 ms) processing path;
"stateful, call `process()` repeatedly with chunks for streaming use" (the
class docstring, audio_processor.py:496) describes a capability the pipeline
has but that no caller currently exercises. `transcriber.py` and
`daemon.py` consume whatever `AudioRecorder.stop()` returns — they never see
individual chunks either.

## 2. Causality of each stage

Verified with `TestNoLookahead` (tests/test_audio_pipeline_streaming.py): mutate
everything after a cut point and check the stage's output before the cut is
unchanged.

| Stage | Causal? | Why |
|---|---|---|
| `NoiseGate` | **Yes** | RMS uses `np.cumsum` (running sum up to the sample), gate/hold state and the attack/release envelope all carry forward only from the past (`audio_processor.py:67-131`). |
| `AGC` | **Yes** | Same cumsum-based causal RMS, plus a `self._current_gain` instance variable carried across calls (`audio_processor.py:178-217`). |
| `PeakLimiter` | **Yes** | Pure per-sample `tanh` saturation, no history at all (`audio_processor.py:238-259`). |
| `Denoiser` (`noisereduce.reduce_noise`) | **No** | Whole-array spectral-gate operation; estimates a noise profile from, and processes, the entire buffer passed to one call, with no state kept between calls (`audio_processor.py:279-291`). Not exercised in this environment (`noisereduce` isn't installed — degrades to passthrough, `test_denoiser_is_noop_without_noisereduce`), but structurally non-causal regardless. |
| `AudioProcessor.resample` (`scipy.signal.resample`) | **No** | FFT-based over the full input; `test_resample_has_lookahead_xfail` confirms mutating the tail changes the head of the output. Only hit when `config.audio.sample_rate != 16000` (recorder.py:244-245). |

No stage does whole-buffer *normalization* (no global peak/RMS scan feeding
back into early samples) outside of `AudioProcessor.normalize()`/`trim_silence()`,
neither of which is part of `AudioPipeline` itself.

## 3. Chunk invariance (quantified)

Signal: 2.24 s synthetic mix of silence, a quiet tone, a loud tone, a noise
burst, and a noise floor (`make_signal()`), chosen so 80/160/560/1120 ms
chunks all divide it evenly. Compared chunked (N calls, state carried) vs.
one-shot (today's usage) with `max_abs_diff`. Full numbers and causes are in
the test file's docstrings/xfail reasons; summary:

| Stage | 80ms | 160ms | 560ms | 1120ms | Holds? |
|---|---|---|---|---|---|
| `PeakLimiter` | 0 | 0 | 0 | 0 | **Yes**, exactly (stateless, per-sample). |
| `NoiseGate` | ~0.023 | ~0.023 | 0 | 0 | **Approximately** — bounded by one hysteresis-zone's gain error, signal/boundary-dependent, not a guarantee (asserted `< 0.03`, not xfailed). |
| `AGC` | ~0.86 | ~0.83 | ~1.15 | ~0.045 | **No** at every tested size (all `xfail`). The window reset dominates even where chunk ≫ `window_ms`. |
| Full pipeline (gate→AGC→limiter) | — | — | — | ~0.045–0.54 | **No** (xfail) — inherits AGC's failure. |
| `Denoiser` (noisereduce) | — | — | n/a (skipped, not installed) | — | **No**, structurally (own test documents why; skipped here for lack of the optional dep). |

Root cause of the AGC failure: `AGC.process()` rebuilds its RMS estimate from
`np.cumsum` of *only the samples in the current call* (audio_processor.py:178-188)
instead of carrying a rolling sample history across `process()` calls. With
`window_ms=300` default, every chunk's first ~300 ms of RMS estimate is wrong
relative to one-shot processing, and the attack/release smoother (which does
carry `self._current_gain` correctly) chases that wrong target. This is a
missing few lines of state (a short rolling buffer of past samples/their
squared sum), not an architectural rewrite.

## 4. CPU time

`scripts/bench_pipeline_chunk_latency.py`, single-threaded, 50 chunks of 560 ms
synthetic noise through the full pipeline (gate+AGC+limiter, denoiser off):

```
median: 0.471 ms/chunk
p95:    0.497 ms/chunk
real-time ratio: ~1189x
```

CPU cost of the DSP pipeline itself is not a concern for streaming at any of
the Nemotron chunk sizes (80-1120 ms) — it is ~3 orders of magnitude faster
than real time on this machine. Whatever latency budget streaming ASR has
will be spent in the model, not in NoiseGate/AGC/PeakLimiter.

## 5. Recommendation

**Minimal changes to make the pipeline streaming-safe:**

1. Give `AGC` a rolling-RMS buffer that persists across `process()` calls
   (e.g. keep the trailing `window_samples` of audio, or just the trailing
   sum-of-squares and count, in `self`) instead of recomputing from an
   expanding/resetting `cumsum` each call. This is the one actual bug
   relative to streaming use; everything else (`NoiseGate`, `PeakLimiter`)
   is already acceptable or only mildly chunk-size-sensitive.
2. Do not call `Denoiser` (noisereduce) per-chunk — it is structurally a
   whole-buffer operation. Either drop it from the streaming path entirely,
   or replace it with a genuinely streaming/causal noise suppressor later;
   out of scope here.
3. Never resample per-chunk with `scipy.signal.resample`. If the input device
   isn't already 16 kHz, resampling must happen with a causal/stateful
   resampler (e.g. `scipy.signal.lfilter`-based polyphase or `samplerate`'s
   streaming API), not the current FFT-based call. Simplest fix: constrain
   the streaming path to devices/configs already at 16 kHz and keep today's
   `resample()` only for the non-streaming (offline) path.
4. `AudioRecorder` needs a second entry point that calls
   `self._pipeline.process(chunk, 16000)` per ~80-1120 ms chunk as audio
   arrives (from `_audio_callback` or a buffering layer above it), instead of
   only at `stop()`. This is new wiring, not a pipeline change, and is where
   WHISP-37 (partial-transcript signal) would hook in.

**Streaming should consume the *processed* signal, not raw**, once (1)-(3)
above are done: NoiseGate and PeakLimiter are already causal and cheap
(section 4), and gating out non-speech / limiting peaks before ASR should
only help a streaming recognizer's accuracy and stability, matching how the
non-streaming path already feeds Whisper processed audio today. Until AGC's
window bug is fixed, skip AGC in the streaming config (`agc_enabled=False`)
rather than feed it mid-chunk, since table 2 shows it is the only stage that
meaningfully diverges from one-shot behavior.

**Proposed A/B for the WHISP-54 re-bench:** transcribe the same held-out audio
set through Nemotron 3.5 Streaming twice — (A) raw 16 kHz chunks straight from
the device, (B) chunks through `AudioPipeline` with the fixed streaming-safe
AGC, gate, and limiter enabled, denoiser off — and compare WER and partial-
transcript stability (how often an early partial gets revised) between the
two. This isolates whether the DSP pipeline helps or hurts the *streaming*
model specifically (it already helps the batch path, per existing pipeline
tests), separately from the chunk-invariance findings above which are about
pipeline internals, not transcription quality.
