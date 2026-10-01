# Streaming / low-latency local ASR for desktop dictation (state as of 2026-10-01)

Scope: open-weight models running locally on Linux that emit incremental partial transcripts, for WhisperAloud WHISP-37 (partial-transcript D-Bus signal) and WHISP-34 (whether the stateful AudioPipeline can feed streaming). Notes cover ES + EN. Research budget was about 15 tool calls, so some numbers come from a single source and are flagged.

## 1. Which open-weight models stream natively (latency, accuracy cost, Spanish)

### Takeaway
The big change since about March 2026 is **NVIDIA Nemotron 3.5 ASR Streaming 0.6B** (released 2026-06-04/05). It is a cache-aware FastConformer-RNNT with 40 language-locales, Spanish included (es-ES, es-US), built-in punctuation and casing, and chunk sizes from 80 ms to 1.12 s. It already runs on CPU through ONNX, sherpa-onnx and parakeet-rs, and it is the most natural fit for WhisperAloud's existing sherpa-onnx/Parakeet path. **Voxtral Realtime 4B** (Feb 2026, Apache-2.0) is more accurate in Spanish but GPU/vLLM-class. **Moonshine v2** (Feb 2026) is the edge/latency leader. **Kyutai STT** has no Spanish.

### Cited Findings
**Nemotron 3.5 ASR Streaming 0.6B (NVIDIA)**
- Released on HF 2026-06-04. Architecture is "FastConformer-CacheAware-RNNT with Prompt": a 24-layer cache-aware encoder plus RNNT decoder, 600M params, with language-ID prompt conditioning — [HF model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Languages: 40 locales in 3 tiers. "Transcription-ready" includes Spanish (es-US, es-ES), English, FR, DE, IT, PT, NL, TR, RU, AR, HI, JA, KO, VI, UK — [HF model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Chunk size is chosen at inference time via `att_context_size`: 80, 160, 320, 560 or 1120 ms — [HF model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Spanish (es-US + es-ES) averages 4.11% WER on FLEURS at 1.12 s chunks with the language given — [HF model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Licence is OpenMDW-1.1 (not Apache/CC-BY). Runtimes: NeMo, Transformers, NeMo-Speech.cpp. Built-in punctuation and capitalization. The card does not mention end-of-utterance detection — [HF model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Cache-aware design keeps caches for all encoder self-attention and conv layers and processes only new audio "deltas". NVIDIA claims up to 3x efficiency vs buffered streaming (560 vs 180 concurrent streams on H100 at 320 ms) — [MarkTechPost](https://www.marktechpost.com/2026/06/06/nvidia-releases-nemotron-3-5-asr-a-600m-parameter-cache-aware-streaming-model-transcribing-40-language-locales-in-real-time/); [HF/NVIDIA blog](https://huggingface.co/blog/nvidia/nemotron-speech-asr-scaling-voice-agents)
- There is an earlier **English-only Nemotron Speech Streaming EN 0.6B**. Microsoft CoreAI (arXiv 2604.14493, Apr 2026) found it lost only **0.21% absolute WER going from batch to streaming**, "substantially outperforming alternatives". It averaged 8.20% WER over 8 benchmarks with int4 k-quant ONNX (0.67 GB, down from 2.47 GB), ran at 7.2x real time on an AMD EPYC CPU, and had 0.56 s algorithmic delay. Recommended config: 560 ms chunks with a 5.6 s left history — [arXiv 2604.14493](https://arxiv.org/html/2604.14493v1)
- Same paper: on CPU at batch 1, Nemotron-0.6B ran faster than Parakeet TDT-0.6B-v3 (search snippet: RTFx 2.46 vs 1.38), because small cache-efficient chunks suit the CPU memory hierarchy. On GPU, Parakeet TDT has higher throughput — [arXiv 2604.14493](https://arxiv.org/pdf/2604.14493) (numbers come from a search snippet; I did not check them against the paper table)

**Voxtral Realtime (Mistral) — Voxtral-Mini-4B-Realtime-2602**
- Released 2026-02-04 with Voxtral Transcribe 2, under Apache 2.0 — [Mistral news](https://mistral.ai/news/voxtral-transcribe-2/); [MarkTechPost](https://www.marktechpost.com/2026/02/04/mistral-ai-launches-voxtral-transcribe-2-pairing-batch-diarization-and-open-realtime-asr-for-multilingual-production-workloads-at-scale/)
- 4.4B params (970M causal audio encoder, 25M adapter, 3.4B decoder) using a Delayed-Streams-Modeling framework. Delay is configurable from 80 ms to 2400 ms in 80 ms steps — [arXiv 2602.11298](https://arxiv.org/html/2602.11298v2)
- WER: English short-form 8.47% at 480 ms, 7.94% at 960 ms, 7.72% at 2400 ms. **Spanish FLEURS 3.31% at 480 ms, 2.98% at 960 ms, 2.71% at 2400 ms**. At 480 ms it is "on par with Whisper" — [arXiv 2602.11298](https://arxiv.org/html/2602.11298v2)
- 13 languages, ES and EN included. Reference runtime is vLLM with a WebSocket realtime API — [arXiv 2602.11298](https://arxiv.org/html/2602.11298v2); [Mistral](https://mistral.ai/news/voxtral-transcribe-2/)

**Moonshine v2 (Moonshine AI)**
- arXiv 2602.12241 (Feb 2026). Ergodic streaming encoder with sliding-window self-attention, so time-to-first-token stays bounded and does not grow with utterance length. Targets edge devices (0.1–1 TOPS, <1 GB) — [arXiv](https://arxiv.org/html/2602.12241v1)
- Response latency on Apple M3: Tiny 50 ms, Small 148 ms, Medium 258 ms (claimed 43.7x faster than Whisper large-v3) — [arXiv](https://arxiv.org/abs/2602.12241v1)

**Kyutai STT**
- `stt-1b-en_fr` (500 ms delay, EN and FR) and `stt-2.6b-en` (2.5 s delay). No Spanish. It predicts the probability that the user has finished talking (a built-in semantic VAD), but only in the Rust implementation. Runtimes: PyTorch, Rust/Candle, MLX. The "flush trick" cuts end latency to about 125 ms — [Kyutai STT](https://kyutai.org/stt/)

**Parakeet "EOU" streaming model**
- parakeet-rs supports an EOU (end-of-utterance) streaming model on 160 ms chunks that segments automatically on end of speech, plus Nemotron EN and Nemotron 3.5 multilingual on 560 ms chunks — [parakeet-rs](https://github.com/altunenes/parakeet-rs)

### Inferences
- For WhisperAloud (ES + EN, CPU laptop, already on sherpa-onnx with Parakeet TDT v3), **Nemotron 3.5 streaming 0.6B** is the obvious native-streaming candidate. Spanish quality is good (4.11% FLEURS at 1.12 s), it is about the same size as Parakeet, and the runtime stack overlaps. Check the OpenMDW licence before shipping it in the Debian engine package.
- Voxtral Realtime is the Spanish-accuracy leader, but 4.4B params plus vLLM is not realistic on a CPU laptop. Treat it as a GPU-only option.
- Moonshine v2 is attractive on latency. I did not confirm Spanish coverage for the v2 streaming models (see Gaps).

### Gaps
- I did not verify Nemotron 3.5 WER at 80/160/560 ms chunks, or its streaming-vs-offline delta for Spanish specifically. Only the 1.12 s Spanish figure was found.
- Moonshine v2 language list (Spanish?) and licence not verified.
- I found no 2026 sherpa-onnx streaming Zipformer for Spanish. I did not search exhaustively.
- I found no Kyutai STT models with Spanish as of Oct 2026.

## 2. Pseudo-streaming for offline models (LocalAgreement, SimulStreaming, WhisperLive(Kit), chunked Parakeet)

### Takeaway
For Whisper, **SimulStreaming (AlignAtt policy)** has replaced whisper_streaming/LocalAgreement as the UFAL reference, at about 5x the speed. WhisperLiveKit packages it, plus LocalAgreement, Voxtral and NeMo backends, as a local server. Chunked or sliding-window Parakeet TDT works (parakeet-rs, achetronic/parakeet), but each step re-decodes a buffer, so it costs more CPU than native cache-aware streaming.

### Cited Findings
- SimulStreaming implements the AlignAtt policy on Whisper: it uses decoder cross-attention to cut off the hypothesis suffix once a token attends to frames near the end of the audio. Supports large-v3, beam search, prompts and context. It is "approximately 5 times faster" than the previous WhisperStreaming and works in real time — [ufal/SimulStreaming](https://github.com/ufal/SimulStreaming)
- UFAL used SimulStreaming in the IWSLT 2026 simultaneous track, and AlignAtt4LLM (2026) extends AlignAtt to decoder-only LLMs — [arXiv 2606.03948](https://arxiv.org/html/2606.03948v1); [arXiv 2606.03967](https://arxiv.org/pdf/2606.03967)
- WhisperLiveKit uses SimulStreaming (AlignAtt) by default, with LocalAgreement as an alternative. It also has Voxtral and NeMo/Canary-1B backends, a VAC/VAD front end (docs advise against disabling it), CPU and GPU Docker images and MLX on Apple. Its own benchmarks are on H100 — [WhisperLiveKit](https://github.com/quentinfuxa/whisperlivekit)
- Parakeet TDT 0.6B v3 can stream in chunks: an acoustic buffer receives fixed-length chunks and grows until it reaches a maximum size, after which new chunks push out the oldest. achetronic/parakeet exposes OpenAI-style `transcript.text.delta` / `.done` SSE events on ONNX Runtime — [achetronic/parakeet via search](https://github.com/achetronic/parakeet)
- parakeet-rs offers streaming "even in CPU" with stateful chunk processing — [parakeet-rs](https://github.com/altunenes/parakeet-rs)

### Inferences
- Pseudo-streaming a Whisper or Parakeet-TDT model means repeatedly re-encoding a growing or sliding buffer. CPU cost therefore scales with update rate times window length. The Microsoft finding that cache-aware Nemotron beats Parakeet TDT on CPU latency points the same way.
- For dictation, the partials themselves (not the commits) can flicker under LocalAgreement and AlignAtt. The UI should show a "committed" plus "tentative" split.

### Gaps
- I found no recent CPU-only measurements (RTF or % core) for SimulStreaming with small or medium Whisper on a laptop.
- Not checked: WhisperLive (Collabora) 2026 status, and NeMo's own buffered-RNNT streaming script for Parakeet.

## 3. VAD and endpointing (push-to-talk vs hands-free)

### Takeaway
Use an acoustic VAD (**Silero VAD v6**: most accurate per its own benchmarks; **TEN VAD**: faster speech→silence transitions and lower RTF) for segmentation. For hands-free mode, optionally add a semantic end-of-turn model: **Smart Turn v3.2** (8 MB, Spanish supported, about 10–100 ms on CPU), or the model's own end-of-utterance signal (Parakeet EOU, Kyutai). Push-to-talk needs no endpointing; the key-release is the final flush.

### Cited Findings
- Silero v6 beats TEN VAD on ROC-AUC across 8 datasets (e.g. 0.96 AliMeeting, 0.95 Earnings21, 0.97 LibriParty). This is Silero's own wiki benchmark (vendor source) — [Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- TEN VAD's README says it detects speech-to-non-speech transitions faster than Silero, whose transitions lag by "several hundred milliseconds", and that its RTF is about 32% lower (vendor claim). Silero uses 512-sample (32 ms) chunks — [TEN VAD](https://github.com/ten-framework/ten-vad)
- Silero v6 is about 1.2 MB / 309K params — [soniqo guide](https://soniqo.audio/guides/vad)
- Smart Turn v3.2 (pipecat-ai): 8 MB quantized for CPU, "as little as 10 ms on some CPUs, under 100 ms on most cloud instances". 23 languages including Spanish, up to 8 s of input, BSD-2. Meant to run after Silero detects silence, on the whole turn — [smart-turn](https://github.com/pipecat-ai/smart-turn)
- One 2026 comparison reports TEN Turn Detection at 0.930/0.829 accuracy (complete/incomplete) vs Smart Turn v3 at 0.704/0.623, and Silero VAD alone with a 22.5% false-stop rate. I could not identify the original study from the search snippet — [arXiv 2609.35791 (FD-VAD), via search](https://arxiv.org/html/2609.35791)
- Kyutai STT has built-in end-of-speech probability (Rust only) — [Kyutai](https://kyutai.org/stt/). parakeet-rs ships a Parakeet EOU streaming model (160 ms chunks) — [parakeet-rs](https://github.com/altunenes/parakeet-rs)

### Inferences
- Push-to-talk (WhisperAloud's current mode): VAD is optional. Its main use is trimming leading and trailing silence and skipping silent chunks to save CPU.
- Hands-free: Silero/TEN VAD gates audio into the streaming recognizer, and a Smart Turn check on silence decides when to commit or paste. This avoids cutting off mid-thought pauses, which matter in dictation.

### Gaps
- The VAD benchmarks are vendor-authored on both sides. I found no independent Spanish-specific VAD benchmark.

## 4. Architectural patterns (two-pass, how apps do it)

### Takeaway
Two patterns coexist in 2026. **Two-pass** uses a streaming model for live partials and an offline model (e.g. Parakeet TDT v3) for the authoritative final text. **Single-pass** uses the streaming model for both, flushing its tail on stop. OpenWhispr (July 2026) moved from two-pass to single-pass Nemotron, and keeps the batch path only as a fallback.

### Cited Findings
- OpenWhispr 1.7.6 (2026-07-18) runs Nemotron Speech Streaming EN 0.6B (632 MB INT8 ONNX) or Nemotron 3.5 Streaming 0.6B (650 MB, 15 languages with auto-detect, Spanish included) via pinned sherpa-onnx 1.13.4. It uses a local WebSocket on 127.0.0.1 with float32 16 kHz frames — [OpenWhispr blog](https://openwhispr.com/blog/local-streaming-speech-to-text)
- OpenWhispr: "the app flushes the model's tail and commits the streamed text directly — no second decode". The earlier design ran stream plus full re-decode, and the batch pass now runs only if the stream fails. Partial text lands "well under a second" behind speech. English streaming WER is 6.93% at 1.12 s vs 5.91% for NVIDIA's best batch model — [OpenWhispr blog](https://openwhispr.com/blog/local-streaming-speech-to-text)
- A sherpa-onnx-oriented recommendation (search snippet) pairs `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8` for live hypotheses with `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` for the final transcript — [search result, source page unclear; see HF](https://huggingface.co/Masterx/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-2026-06-11)
- sherpa-onnx support: English Nemotron streaming has been supported since PR #3044. A request for multilingual Nemotron 3.5 (6th encoder input `prompt_index`; value 101 means auto language detection) was opened 2026-06-06 (#3664) and closed alongside PR #3671. Merge was not explicitly confirmed. Community ONNX exports exist (pantinor/…-onnx, Masterx/sherpa-onnx-…-560ms-2026-06-11) — [sherpa-onnx #3664](https://github.com/k2-fsa/sherpa-onnx/issues/3664); [HF Masterx](https://huggingface.co/Masterx/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-2026-06-11)
- Standalone numpy + onnxruntime engine for Nemotron 3.5 with `get_partial()` / `get_final()` API — [codavidgarcia/nemotron-3.5-asr-streaming-onnx](https://github.com/codavidgarcia/nemotron-3.5-asr-streaming-onnx)
- Other Linux dictation apps advertising local streaming or real-time: Spokenly Linux (Sept 2026), Vocalinux (tray app with push-to-talk and real-time transcription), Handy (Parakeet v3). Their streaming internals were not verified — [Spokenly](https://spokenly.app/linux); [Vocalinux](https://vocalinux.com/); [Handy](https://github.com/cjpais/handy)

### Inferences
- Suggested WhisperAloud design for WHISP-37: the daemon emits `PartialTranscript(session_id, committed_text, tentative_text, seq)` signals from a streaming engine. On stop, flush to get the final text. Optionally (config) re-decode with Parakeet TDT v3 or Whisper for the history/clipboard copy (two-pass), because offline models are still about 1 pp WER better. Throttle signals to around 3–10 per second to keep D-Bus traffic small.
- A streaming engine should be a separate engine type in `create_engine` with a stateful `accept_chunk()` / `get_partial()` / `finalize()` API, not a bolt-on to the batch interface.

### Gaps
- I did not verify which sherpa-onnx release (if any) officially ships the multilingual Nemotron 3.5 streaming model. Check the sherpa-onnx changelog or releases before planning.

## 5. Practical CPU/GPU cost on a laptop, and AudioPipeline compatibility (WHISP-34)

### Takeaway
Native cache-aware streaming at 0.6B runs comfortably in real time on a modern laptop CPU. Indicative fp32 numbers for Nemotron 3.5: RTF about 0.26 at 320 ms chunks, 0.11 at 1.12 s, 0.65 at 80 ms. Smaller chunks cost much more CPU, so 560 ms is the common sweet spot. Int8 roughly halves cost again, but naive dynamic int8 can hurt accuracy.

### Cited Findings
- codavidgarcia ONNX engine (onnxruntime 1.27, fp32): RTF 0.653 at 80 ms chunks, 0.263 at 320 ms, 0.106 at 1120 ms. Memory is about 4.7 GB fp32 / 2.5 GB fp16 for the 320 ms variant. Dynamic int8: RTF 0.148 and about 0.7 GB, but **WER vs reference 0.189** (vs 0.0082 fp32, 0.0137 fp16), i.e. noticeably degraded. Hardware not specified — [codavidgarcia repo](https://github.com/codavidgarcia/nemotron-3.5-asr-streaming-onnx)
- Microsoft: int4 k-quant Nemotron EN at 0.67 GB runs 7.2x real time on an AMD EPYC CPU with 8.20% avg WER, showing that careful quantization preserves accuracy — [arXiv 2604.14493](https://arxiv.org/html/2604.14493v1)
- OpenWhispr runs 632–650 MB INT8 Nemotron "in real time on a modern CPU" — [OpenWhispr](https://openwhispr.com/blog/local-streaming-speech-to-text)
- Kyutai 1B runs about 3x real time per stream (64 streams) on an L40S GPU. No CPU numbers are published — [Kyutai](https://kyutai.org/stt/)

### Inferences
- **WHISP-34 (AudioPipeline to streaming):**
  - The pipeline already works chunk by chunk with per-session state (NoiseGate envelope, AGC RMS window). That is structurally the same contract as streaming ASR, so it can feed a streaming recognizer as long as every stage is causal and adds bounded latency.
  - Risks to test:
    1. Whether the optional Denoiser (noisereduce) uses non-causal, whole-buffer spectral estimation. If it does, it needs a streaming/stationary mode, or must be bypassed for the partial path.
    2. NoiseGate attenuation can chop word onsets, and the AGC gain ramp at chunk boundaries can hurt cache-aware models trained on raw audio. A/B test streaming WER with the pipeline on vs off.
    3. The pipeline chunk size should divide or align with the ASR chunk (80 ms multiples for FastConformer at 16 kHz) to avoid extra buffering latency.
  - A reasonable plan is to run the VAD and the ASR on the same processed stream, keep the raw audio for an optional final re-decode, and expose a "pipeline for streaming" toggle.
- Recommended starting config: Nemotron 3.5 streaming at 560 ms, int8 (or better a k-quant/static-int8 export), language fixed (es or en) rather than auto, since NVIDIA reports best numbers with language input. Expect one core or less in steady state on a recent laptop (inferred from the RTF figures; not measured on WhisperAloud hardware).

### Gaps
- No laptop-specific (e.g. 4–8 core mobile CPU) CPU-percentage measurements were found for Nemotron 3.5 streaming. The codavidgarcia repo does not state its hardware.
- No published study on how classic DSP front ends (gate/AGC/denoise) affect cache-aware streaming ASR accuracy. This needs a local experiment.
