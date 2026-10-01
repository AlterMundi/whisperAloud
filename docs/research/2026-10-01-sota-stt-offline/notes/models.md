# Open-weight, locally-runnable ASR models — state of the art as of October 2026 (changes since ~March 2026)

Legend: **[NEW]** = released/updated ~March–Oct 2026. **[EARLY-2026]** = Jan–Feb 2026 (just before the window, likely not in the March baseline decision). **[BASELINE]** = before 2026 / already known.
Project baseline (from `bench/MATRIX.md`, FLEURS, i7-1360P CPU int8): Parakeet TDT 0.6B v3 int8 (sherpa-onnx) ES 3.2% WER / RTF 0.151 / 1.4 GB RAM / 671 MB; EN 10.5% (an outlier vs the model card's FLEURS-en, likely normalisation); faster-whisper small int8 ES 5.1% / EN 7.2%; medium int8 ES 5.3% / EN 5.3%. Large-v3-turbo was evaluated but isn't in the matrix.

## 1. What new or updated open-weight ASR models were released ~March–October 2026?

### Takeaway
The window brought several new open-weight models: Cohere Transcribe 2B (Mar), IBM Granite Speech 4.1 2B + variants (Apr 29), NVIDIA Parakeet Unified EN (Apr), NVIDIA Nemotron 3.5 ASR Streaming 0.6B multilingual (Jun 4), Microsoft VibeVoice-ASR-BitNet for CPU (Jul 23) and VibeVoice-ASR-Streaming (Sep 3), and Moondream's Parakeet Ultra/Redux post-trains of Parakeet v3 (Sep). Jan–Feb 2026 also brought Qwen3-ASR, VibeVoice-ASR 7B, Voxtral Realtime 4B and Moonshine v2. OpenAI released **no** new open-weight ASR model. There's no Parakeet v4 and no new Kyutai STT checkpoint.

### Cited Findings
**Window releases (March–Oct 2026) [NEW]**
- **Cohere Transcribe (`CohereLabs/cohere-transcribe-03-2026`)**: released 2026-03-26. 2B params, Apache-2.0, 14 languages including Spanish. Open ASR avg WER 5.42 (LS-clean 1.25, LS-other 2.37, TED-LIUM 2.49, Earnings22 10.84, VoxPopuli 5.87). Punctuation on by default. **No timestamps, no diarization, no automatic language detection (the language must be specified).** The card warns it "is eager to transcribe, even non-speech sounds", so it needs a VAD in front. Runs on Transformers, vLLM, mlx-audio, Rust and WebGPU — [HF card](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026)
- **IBM Granite Speech 4.1 2B**: released 2026-04-29. 2B params, Apache-2.0. ASR languages: EN, FR, DE, **ES**, PT, JA. Open ASR mean WER 5.33 (as of April 2026). Punctuation and truecasing in all languages, **keyword-list biasing**. Variants: `-plus` (speaker attribution + word timestamps) and `-nar` (non-autoregressive, higher throughput) — [HF card](https://huggingface.co/ibm-granite/granite-speech-4.1-2b). RTFx 231.29. The NAR variant reaches ~1820 RTFx on H100 at batch 128, drops Japanese, and loses translation and keyword biasing — [MarkTechPost overview, 2026-07-23](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/); [MarkTechPost launch, 2026-04-30](https://www.marktechpost.com/2026/04/30/ibm-releases-two-granite-speech-4-1-2b-models-autoregressive-asr-with-translation-and-non-autoregressive-editing-for-fast-inference/)
- **Granite 4.1 2B in GGML**: the `transcribe.cpp` project (handy-computer) ships quantisations from BF16 (4.63 GB) down to Q4_K_M (1.60 GB). On an Apple M4 Max it runs at ~75× realtime on GPU and ~8× realtime on CPU only — [transcribe.cpp docs](https://github.com/handy-computer/transcribe.cpp/blob/main/docs/models/granite-speech-4.1-2b.md)
- **NVIDIA Parakeet Unified EN 0.6B**: released 2026-04-07. English only. RNN-T that does both offline and streaming in one model, with a minimum latency of 160 ms — [HF card](https://huggingface.co/nvidia/parakeet-unified-en-0.6b) (via search snippet). Licence is the NVIDIA Open Model License — [Spokenly](https://spokenly.app/blog/parakeet-models)
- **NVIDIA Nemotron 3.5 ASR Streaming 0.6B**: released 2026-06-04. 600M params, licence **OpenMDW-1.1**, 40 language-locales (es-ES and es-US are both in the "transcription-ready" tier). Built-in punctuation and capitalisation. Language-ID prompt conditioning plus `target_lang=auto`. Chunk sizes from 80 ms to 1.12 s. FLEURS WER at 1.12 s chunks: **ES 4.11%**, EN 7.91%. Runtimes: NeMo, Transformers ≥5.13, and the new **NeMo-Speech.cpp** C++ runtime — [HF card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- **Moondream Parakeet Ultra / Parakeet Redux**: released September 2026. These are post-trains of parakeet-tdt-0.6b-v3 (same 0.6B architecture). Ultra results: Open ASR 5.80, FLEURS-25 avg 9.55, **FLEURS-es 2.72**, FLEURS-en 3.55, "better than the original on every benchmark group". Licence CC-BY-4.0. Ships as safetensors F32/F16 and runs on Moondream's "Photon" engine on NVIDIA GPUs. Redux is a ternary-quantised sibling (178 MB) aimed at CPU and Apple silicon — [HF card](https://huggingface.co/moondream/parakeet-ultra). Core ML sizes: Ultra 632 MB, Redux 220 MB. Redux has "modest accuracy tradeoffs, particularly with background noise". No Parakeet v4 "as of September 2026" — [Spokenly](https://spokenly.app/blog/parakeet-models)
- **Microsoft VibeVoice-ASR-BitNet**: released 2026-07-23. Edge/CPU engine that compresses VibeVoice-ASR from 4.62 GB to 1.58 GB and runs in real time on 3+ CPU threads. **VibeVoice-ASR-Streaming**: released 2026-09-03, 10 languages, hotwords, "who said what". MIT licence — [GitHub microsoft/VibeVoice](https://github.com/microsoft/VibeVoice)
- **Research edge: diffusion-gemma-asr-small**: ~42M trained params on top of DiffusionGemma. LS-clean 6.6, FLEURS-en 15.7. Not practical yet — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- **MOSS-Transcribe-Diarize 0.9B**: Apache-2.0, 50+ languages, speaker + word timestamps in one generation, hotword biasing. Release date not given in the source — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)

**Jan–Feb 2026 [EARLY-2026]**
- **Qwen3-ASR 1.7B / 0.6B** (Alibaba): released January 2026, Apache-2.0. ASR + language ID for 52 languages/dialects (30 languages + 22 Chinese dialects). Streaming and offline in one model. Timestamps come via a separate **Qwen3-ForcedAligner-0.6B** (11 languages, ≤5 min) — [HF card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B); [tech report arXiv 2601.21337](https://arxiv.org/abs/2601.21337). Open ASR avg 5.76 (1.7B) — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- **Microsoft VibeVoice-ASR (7B/9B class)**: released 2026-01-21, MIT. Handles 60-minute audio in a single pass with diarization, timestamps and custom context/hotwords, 50+ languages — [GitHub](https://github.com/microsoft/VibeVoice); [MarkTechPost](https://www.marktechpost.com/2026/01/22/microsoft-releases-vibevoice-asr-a-unified-speech-to-text-model-designed-to-handle-60-minute-long-form-audio-in-a-single-pass/). Sources disagree on the size: the search summary says 9B, the GitHub README says "ASR-7B".
- **Mistral Voxtral Mini 4B Realtime 2602**: released 2026-02-04/05. Apache-2.0, ~3.4B LM + ~970M audio encoder, 13 languages. FLEURS avg 8.72% at 480 ms delay, **ES 3.31%**. Delay configurable from 80 ms to 2400 ms. Needs a GPU with ≥16 GB. Runs on vLLM, Transformers ≥5.2, with community C/Rust/MLX ports — [HF card](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602). The batch model, Voxtral Mini Transcribe V2, is API-only — [Simon Willison](https://simonwillison.net/2026/Feb/4/voxtral-2/); [Mistral](https://mistral.ai/news/voxtral-transcribe-2/)
- **Moonshine v2** (Moonshine AI / ex-Useful Sensors): February 2026 (arXiv 2602.12241, weights public ~2026-02-23). Sliding-window "ergodic" streaming encoder. Sizes: Tiny 26M … Medium 200M. The Medium streaming model beats Whisper large-v3 on the Open ASR leaderboard. Runs on ONNX Runtime on CPU. Languages: EN, ES, ZH, JA, KO, VI, UK, AR, with one model per language — [GitHub moonshine-v2](https://github.com/moonshine-ai/moonshine-v2); [arXiv](https://arxiv.org/html/2602.12241v1). Spanish Base 58M reaches **4.33% WER** (dataset not stated in the snippet) — [search result summarising Moonshine](https://github.com/moonshine-ai/moonshine-v2)

**Not released / unchanged**
- **OpenAI**: no new open weights. Its newer models (gpt-4o-transcribe, Mar 2025; "GPT-Realtime-Whisper", May 2026; "gpt-live-transcribe", Jul 2026) are API-only. Whisper large-v3 / large-v3-turbo (Sep 2024) are still its latest open weights — [innfactory / search summary](https://innfactory.ai/en/ai-models/openai-whisper/). The May/Jul 2026 model names come only from a search-aggregator summary and are unverified.
- **Kyutai STT**: still only `stt-1b-en_fr` and `stt-2.6b-en` (2025). In 2026 Kyutai shipped TTS (Pocket TTS, multilingual since April), not new STT — [Kyutai HF](https://huggingface.co/kyutai/stt-2.6b-en); [Kyutai TTS](https://kyutai.org/tts/)
- **[BASELINE] models still relevant**: Parakeet TDT 0.6B v3 (2025-08-14, CC-BY-4.0) — [HF](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3). Canary-1B-v2 (2025, 25 EU languages, word timestamps) — [arXiv 2509.14128](https://arxiv.org/abs/2509.14128). Canary-Qwen-2.5B (English only, 5.63 avg, CC-BY-4.0) — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/). Meta Omnilingual ASR (Nov 2025, 300M–7B, 1600+ languages, Apache-2.0) — same source. Kyutai STT (CC-BY-4.0, semantic VAD, 0.5 s / 2.5 s delay) — same source.

### Inferences
- The Mar–Oct 2026 trend is **2B-class "speech-LLM" style models** (Cohere, Granite) taking the English accuracy crown, while **0.6B transducers** (Parakeet derivatives, Nemotron 3.5) stay the speed/size sweet spot. Of all the new entries, the Parakeet Ultra post-train and Nemotron 3.5 Streaming are the ones that fit WhisperAloud's existing sherpa-onnx/NeMo-adjacent path.
- Parakeet Ultra keeps the v3 architecture, so in principle it could be exported to sherpa-onnx the same way v3 is. I found no evidence that a sherpa-onnx/ONNX export exists yet.

### Gaps
- No confirmed sherpa-onnx or ONNX exports were found for Parakeet Ultra/Redux, Nemotron 3.5 ASR Streaming, Granite 4.1 or Cohere Transcribe. Check the sherpa-onnx releases page.
- Exact release dates for MOSS-Transcribe-Diarize and the Moonshine v2 Spanish model licence date are not confirmed.
- I couldn't load the live HF Open ASR Leaderboard Space (Gradio), so the rankings below come from model cards, the leaderboard paper (v4, March 2026) and secondary summaries.
- No new Meta, Microsoft Phi or distil-whisper ASR checkpoints dated within the window were found; I didn't search exhaustively for distil-whisper.

## 2. Who tops the HF Open ASR Leaderboard, and how do they compare to Whisper large-v3-turbo and Parakeet v3?

### Takeaway
English short-form mean WER (lower is better): Granite Speech 4.1 2B 5.33 → Cohere Transcribe 5.42 → Canary-Qwen-2.5B 5.63 → Qwen3-ASR-1.7B 5.76 → Parakeet Ultra 5.80 → Parakeet v3 6.32–6.34 → Kyutai 2.6B 6.40 → Whisper large-v3 7.44 → large-v3-turbo 7.83 (7.68 for Voxtral Realtime). Parakeet v3 is still by far the fastest (RTFx ~3333 vs ~200 for turbo). On the multilingual track (DE/FR/IT/ES/PT), the best open models are Voxtral Small 24B (3.70) and Cohere Transcribe (3.83).

### Cited Findings
- Leaderboard paper v4 (2026-03-30), English track: Cohere Transcribe 5.42 WER / 525 RTFx. Canary-Qwen-2.5B 5.63 / 418. IBM Granite (3.x) models 5.52–5.76. Whisper large-v3 7.44 / 146 RTFx — [arXiv 2510.06961v4](https://arxiv.org/html/2510.06961v4)
- Leaderboard paper v4, multilingual track (DE, FR, IT, ES, PT across CoVoST-2, FLEURS and MLS): ElevenLabs Scribe v2 2.67 (closed), AssemblyAI U3 Pro 3.23 (closed), **Voxtral Small 24B 3.70 (open)**, **Cohere Transcribe 3.83 (open)** — [arXiv 2510.06961v4](https://arxiv.org/html/2510.06961v4)
- Granite Speech 4.1 2B, mean WER 5.33 — "current SOTA" per the HF card (April 2026) — [HF](https://huggingface.co/ibm-granite/granite-speech-4.1-2b); same figure in [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- Whisper large-v3-turbo: mean WER 7.83, RTFx 200.19. 809M params (decoder cut from 32 to 4 layers) — [search summary of HF card / leaderboard](https://huggingface.co/openai/whisper-large-v3-turbo); [vexascribe](https://vexascribe.com/whisper-large-v3-vs-turbo)
- Parakeet v3: 6.32 WER, RTFx 3332.74, "highest throughput among multilingual models" — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/). The HF card gives 6.34 — [HF](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- Parakeet Ultra: 5.80 — [HF](https://huggingface.co/moondream/parakeet-ultra). Voxtral Realtime: 7.68, which the source calls misleading for a streaming model. Kyutai STT: 6.40 — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- Caveat: averages aren't always comparable. Dropping TED-LIUM moves Cohere from 5.42 to 5.84 — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- Third-party benchmark (Apple M5 Pro): Qwen3-ASR 1.7B MLX 5-bit 1.32% WER vs WhisperKit large-v3-turbo 1.71% vs Parakeet v3 2.37%. Speed: Parakeet 117× RT at 0.9 GB vs Qwen3-ASR 36× RT — [Soniqo](https://soniqo.audio/benchmarks). The test set is not described clearly, so treat this as low confidence.

### Inferences
- Moving from turbo to any of the 2026 top-5 cuts English WER by roughly 25–30% relative. Moving from Parakeet v3 to Parakeet Ultra cuts it by ~8% at the same size and speed.
- The leaderboard's RTFx is measured on datacenter GPUs. It doesn't reflect CPU dictation latency on a laptop.

### Gaps
- I didn't get a current (October 2026) snapshot of the live leaderboard ranking or the RTFx of Granite 4.1 / Ultra beyond the values above.

## 3. Spanish specifically: best Spanish WER today

### Takeaway
On FLEURS-es the best open small models are now **Parakeet Ultra 2.72**, Qwen3-ASR-1.7B 3.36, Voxtral Realtime 3.31 and Canary-1B-v2 ~2.9. For comparison: Whisper large-v3 3.12, Parakeet v3 3.45, Nemotron 3.5 Streaming 4.11, Granite 4.1 5.48 (third-party measurement). On the leaderboard's multilingual track, Cohere Transcribe is the best open Spanish average (2.81). These gaps are small, and text normalisation differences between sources matter.

### Cited Findings
| Model | FLEURS-es | CV-es | MLS-es | Source |
|---|---|---|---|---|
| Parakeet TDT 0.6B v3 [BASELINE] | 3.45 | 3.41 (CoVoST) | 4.39 | [HF](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) |
| Parakeet Ultra [NEW, Sep] | 2.72 | – | – | [HF](https://huggingface.co/moondream/parakeet-ultra) |
| Qwen3-ASR-1.7B [EARLY-2026] | 3.36 | 4.65 | 4.63 | [arXiv 2601.21337 Table A.2](https://arxiv.org/abs/2601.21337) |
| Qwen3-ASR-0.6B [EARLY-2026] | 4.94 | 7.16 | 7.19 | same |
| Voxtral Mini 4B Realtime (480 ms) [EARLY-2026] | 3.31 | – | – | [HF](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) |
| Nemotron 3.5 ASR Streaming 0.6B (1.12 s) [NEW, Jun] | 4.11 | – | – | [HF](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) |
| Granite Speech 4.1 2B [NEW, Apr] | 5.48 (transcribe.cpp measurement) | – | – | [transcribe.cpp](https://github.com/handy-computer/transcribe.cpp/blob/main/docs/models/granite-speech-4.1-2b.md) |
| Whisper large-v3 [BASELINE] | 3.12 | – | – | [search summary of arXiv 2509.14128](https://arxiv.org/pdf/2509.14128) |
| Canary-1B-v2 [BASELINE] | 2.90 | – | – | same |

- Leaderboard multilingual Spanish average (CoVoST/FLEURS/MLS): Cohere Transcribe 2.81, Canary-1B-v2 3.25, Meta Omnilingual 7B v2 3.44, Whisper large-v3 3.65 — [arXiv 2510.06961v4](https://arxiv.org/html/2510.06961v4)
- Qwen3-ASR multilingual aggregates (Whisper large-v3 vs Qwen3-ASR-1.7B): MLS 8.62 vs 8.55, CommonVoice 10.77 vs 9.18, FLEURS(12 languages) 5.27 vs 4.90. Language-ID accuracy on FLEURS-30 is 97.1% (0.6B) and 98.7% (1.7B) — [arXiv 2601.21337](https://arxiv.org/abs/2601.21337)
- **Data-quality warning**: a search snippet attributed "Qwen3-ASR-1.7B 29.90% WER on Spanish". In the paper's Table A.2 that number is **Farsi (fa)**. The real Spanish FLEURS figure is 3.36 — [arXiv 2601.21337](https://arxiv.org/abs/2601.21337)
- The Granite 4.1 2B card reports Spanish punctuation/capitalisation Cap-F1 of 95.68 on CommonVoice-ES — [HF](https://huggingface.co/ibm-granite/granite-speech-4.1-2b). A secondary site claims MLS-es 23.0% for `-plus`. That looks like a different metric or setup and is unverified — [search result](https://huggingface.co/ibm-granite/granite-speech-4.1-2b-plus)
- Moonshine Spanish Base (58M) reaches 4.33% WER, but it uses the **non-commercial** Moonshine Community License — [GitHub](https://github.com/moonshine-ai/moonshine-v2)
- MLC-SLM (conversational) Spanish: Qwen3-ASR-1.7B 11.07, 0.6B 13.89. This shows how much harder conversational speech is than read speech — [arXiv 2601.21337](https://arxiv.org/abs/2601.21337)
- The project's own bench: Parakeet v3 int8 gets FLEURS-es 3.2% on CPU. That matches the card's 3.45 — [bench/MATRIX.md](../../bench/MATRIX.md)

### Inferences
- For Spanish dictation on CPU, Parakeet v3 (the current choice) is still within ~0.7 pt of the best open small models. Parakeet Ultra is the obvious drop-in upgrade candidate (−0.7 pt FLEURS-es, same architecture), if an ONNX/sherpa export can be produced.

### Gaps
- **No Latin-American / Rioplatense accent-specific benchmark** was found for any of these models. FLEURS-es and MLS-es are mostly peninsular or read speech. Nemotron 3.5 is the only model that explicitly lists **es-US** as a locale.
- Cohere Transcribe and Granite 4.1 per-dataset Spanish FLEURS numbers from the vendors themselves were not found.

## 4. Model sizes, memory footprints, CPU-only viability

### Takeaway
CPU-viable on a laptop: Parakeet v3/Ultra (0.6B, ~0.7 GB int8, ~1–1.4 GB RAM), Parakeet Redux (178–220 MB), Nemotron 3.5 Streaming 0.6B (via NeMo-Speech.cpp), Moonshine v2 (26–200M), Qwen3-ASR-0.6B, and Granite 4.1 2B quantised to Q4_K_M (1.6 GB, ~8× RT on M4 Max CPU). VibeVoice-ASR-BitNet (1.58 GB) claims CPU real time. GPU-oriented: Voxtral Realtime 4B (≥16 GB VRAM), Cohere 2B (bf16 ~4+ GB, no CPU runtime documented), VibeVoice-ASR 7B full, Canary-Qwen 2.5B.

### Cited Findings
- Parakeet v3: fp32 ≈2.5 GB; Ultra Core ML 632 MB; Redux 220 MB (Core ML) — [Spokenly](https://spokenly.app/blog/parakeet-models). Redux 178 MB ternary for CPU — [HF](https://huggingface.co/moondream/parakeet-ultra). Project bench: int8 sherpa-onnx is 671 MB, uses ~1.2–1.4 GB RAM, RTF 0.15–0.18 on an i7-1360P — [bench/MATRIX.md](../../bench/MATRIX.md)
- Granite 4.1 2B GGUF: BF16 4.63 GB … Q4_K_M 1.60 GB; ~8× RT CPU-only on M4 Max — [transcribe.cpp](https://github.com/handy-computer/transcribe.cpp/blob/main/docs/models/granite-speech-4.1-2b.md)
- Voxtral Realtime needs a single GPU with ≥16 GB; BF16 4B; 8.87 GB download — [HF](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602); [Simon Willison](https://simonwillison.net/2026/Feb/4/voxtral-2/)
- VibeVoice-ASR-BitNet: 4.62 GB → 1.58 GB, real time on 3+ CPU threads — [GitHub](https://github.com/microsoft/VibeVoice)
- Moonshine v2: Tiny 26M, Medium 200M, ONNX Runtime on CPU — [GitHub](https://github.com/moonshine-ai/moonshine-v2)
- Qwen3-ASR-1.7B: MLX 5-bit uses ~600 MB less peak RSS than 8-bit. Parakeet v3 uses 0.9 GB peak. Omnilingual CTC 300M uses 0.4 GB — [Soniqo](https://soniqo.audio/benchmarks)
- Nemotron 3.5 ASR Streaming has a lightweight C++ runtime, NeMo-Speech.cpp — [HF](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)

### Inferences
- New candidate runtimes on Linux CPU that would avoid PyTorch: `transcribe.cpp` (GGML: Granite, plus listed Qwen3-ASR/Parakeet/FireRedASR2 via openasr.org) and `NeMo-Speech.cpp`. Both are worth a look next to sherpa-onnx.

### Gaps
- No measured x86 laptop CPU RTF was found for Granite 4.1, Qwen3-ASR, Nemotron 3.5 or Parakeet Ultra. Only Apple-silicon numbers exist.

## 5. Licenses (redistribution in Debian/Flatpak)

### Takeaway
Most new models are permissive. Apache-2.0: Cohere Transcribe, Granite 4.1, Qwen3-ASR, Voxtral Realtime, Omnilingual, MOSS. MIT: Whisper, VibeVoice, Moonshine English. CC-BY-4.0 (attribution required): Parakeet v2/v3/Ultra/Redux, Canary, Kyutai STT. The ones to watch: **Moonshine non-English models (including Spanish) are non-commercial**. Newer NVIDIA models use the **NVIDIA Open Model License** (Parakeet Unified) or **OpenMDW-1.1** (Nemotron 3.5); both are reported as commercial-use OK, but they aren't DFSG-standard texts.

### Cited Findings
- Cohere Transcribe Apache-2.0 — [HF](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026). Granite 4.1 Apache-2.0 — [HF](https://huggingface.co/ibm-granite/granite-speech-4.1-2b). Qwen3-ASR Apache-2.0 — [HF](https://huggingface.co/Qwen/Qwen3-ASR-1.7B). Voxtral Realtime Apache-2.0 — [HF](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)
- Parakeet v3 CC-BY-4.0 — [HF](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3). Parakeet Ultra CC-BY-4.0 — [HF](https://huggingface.co/moondream/parakeet-ultra). Parakeet Unified and Realtime EOU use the NVIDIA Open Model License, "commercial-use approved" — [Spokenly](https://spokenly.app/blog/parakeet-models)
- Nemotron 3.5 ASR Streaming: OpenMDW-1.1, "ready for commercial use" — [HF](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Moonshine: English models MIT; non-English models under the Moonshine Community License (non-commercial) — [GitHub](https://github.com/moonshine-ai/moonshine-v2)
- VibeVoice MIT. Microsoft pulled the VibeVoice **TTS** code over misuse concerns; the ASR parts are unaffected — [GitHub](https://github.com/microsoft/VibeVoice)

### Inferences
- Debian main generally rejects non-free/NC model weights. Moonshine-ES would be non-free. CC-BY-4.0 and Apache are fine, with attribution notices in `debian/copyright`. The NVIDIA Open Model License and OpenMDW would need legal review before going into Debian main. Downloading at runtime (as with HF cache) sidesteps the bundling question.

### Gaps
- I didn't read the full OpenMDW-1.1 or NVIDIA Open Model License texts for redistribution or field-of-use clauses.

## 6. Dictation-relevant features

### Takeaway
Punctuation and casing are now standard across Parakeet, Nemotron, Granite, Cohere and Qwen3. Context/keyword biasing is available in Granite 4.1 (keyword list), Voxtral Realtime/Transcribe 2, VibeVoice (hotwords/context) and MOSS (hotwords); Whisper has prompts. Automatic language ID: Parakeet v3, Nemotron 3.5 (`auto`), Qwen3-ASR (98.7% LID on FLEURS-30) and Whisper; **Cohere has none**. Timestamps: Parakeet/Canary native; Granite only in `-plus`; Qwen3 via a separate aligner; **Cohere has none**. Hallucination on silence is explicitly flagged for Cohere and still needs VAD gating for autoregressive LLM-decoder models generally.

### Cited Findings
- Parakeet v3: automatic punctuation/capitalisation, word and segment timestamps, auto language detection, audio up to 24 min. Noise: avg WER 7.12 at SNR 10 dB and 11.66 at 0 dB — [HF](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
- Nemotron 3.5: punctuation and capitalisation, LID prompt or `auto`, streaming chunks down to 80 ms — [HF](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- Granite 4.1: punctuation and truecasing, keyword-list biasing. `-plus` adds word timestamps and speaker attribution. NAR loses keyword biasing — [HF](https://huggingface.co/ibm-granite/granite-speech-4.1-2b); [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- Cohere Transcribe: no LID, no timestamps; "eager to transcribe, even non-speech sounds" — [HF](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026)
- Voxtral Transcribe 2 family: context biasing, word-level timestamps, diarization (some only in the API model) — [gend.co](https://www.gend.co/blog/voxtral-transcribe-2); [Mistral](https://mistral.ai/news/voxtral-transcribe-2/)
- Kyutai STT has a built-in semantic VAD — [MarkTechPost](https://www.marktechpost.com/2026/07/23/best-open-speech-recognition-asr-models-in-2026-wer-languages-latency-and-license-compared/)
- Canary-Qwen-2.5B preserves disfluencies (relevant for verbatim vs clean dictation) — same source.

### Inferences
- For WhisperAloud's push-to-talk dictation with the existing NoiseGate/VAD pipeline: Parakeet-family transducers (non-autoregressive token emission) are structurally less prone to the free-running hallucinations seen with Whisper-style or LLM decoders. Parakeet Ultra keeps that property.
- Granite 4.1 is the most interesting accuracy-tier option for ES+EN with custom vocabulary (keyword biasing) under Apache-2.0, with a GGML CPU path.

### Gaps
- No systematic hallucination-on-silence benchmark across the 2026 models was found.
- No code-switching (ES↔EN) evaluations were found, apart from Qwen3's general multilingual/LID results.
