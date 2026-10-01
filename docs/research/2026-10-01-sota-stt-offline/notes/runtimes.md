# Local ASR inference runtimes and hardware backends on Linux (state as of 2026-10-01)

Method note: release dates come from the GitHub Releases API and the PyPI JSON API, queried on 2026-10-01. Model and backend support comes from the upstream READMEs, docs and release notes. Third-party benchmark numbers came from web search and are flagged where weak. Project-local numbers come from `bench/MATRIX.md` (i7-1360P, FLEURS).

## 1. Maintenance status and recent releases (since ~March 2026)

### Takeaway
All the runtimes WhisperAloud uses or is considering are actively maintained, with one exception: the **faster-whisper Python wrapper has not had a release in 11 months** (v1.2.1, 2025-10-31). Its engine, **CTranslate2, is healthy** (v4.7.0 to v4.8.2 in 2026, with ROCm wheels). The biggest change since March is that **whisper.cpp natively runs Parakeet TDT 0.6B v3 since v1.9.0 (2026-06-17)**. That makes whisper.cpp+Vulkan a candidate single runtime for both of the project's model families on AMD/Intel iGPUs. sherpa-onnx is the fastest-moving runtime for new non-LLM ASR models. In ONNX Runtime, the ROCm EP has been removed (use MIGraphX).

### Cited Findings

**faster-whisper / CTranslate2**
- faster-whisper: last PyPI/GitHub release v1.2.1, 2025-10-31; v1.2.0, 2025-08-06. The repo still gets occasional commits: 2026-09-30 "Adopt av.open usage according to PyAV 19.0.0", 2026-10-01 a comment edit. Between 2025-11-19 and 2026-09-30 there were no commits at all — [GitHub releases](https://github.com/SYSTRAN/faster-whisper/releases), [commits](https://github.com/SYSTRAN/faster-whisper/commits/master), [PyPI](https://pypi.org/project/faster-whisper/)
- faster-whisper 1.2.1 depends on `ctranslate2>=4.0,<5`, `onnxruntime>=1.14,<2` (for Silero VAD), `av>=11`, `tokenizers`, and `huggingface-hub`. It ships as a pure-Python wheel of about 1.1 MB — [PyPI](https://pypi.org/project/faster-whisper/)
- CTranslate2 releases: v4.7.0 (2026-02-03), v4.7.1 (2026-02-04), v4.7.2 (2026-05-19), v4.8.0 (2026-06-06), v4.8.1 (2026-07-03), v4.8.2 (2026-08-31) — [GitHub releases](https://github.com/OpenNMT/CTranslate2/releases)
- CTranslate2 v4.7.0 "Introduce AMD GPU support with ROCm HIP (#1989)". It also added Transformers v5 compatibility and changed Whisper vocab handling ("Assume less about whisper vocab") — [v4.7.0 notes](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.7.0)
- CTranslate2 v4.8.0 "Enables PACKED_GEMM by default for Intel MKL to improve Intel CPU performance" and upgrades Thrust to CCCL 2.7.0 — [v4.8.0 notes](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.0)
- CTranslate2 v4.8.2 changes:
  - Python converters are now imported lazily "to avoid loading torch for inference-only use".
  - Security fixes: out-of-bounds reads and model-size validation.
  - GCC 15 compatibility.
  - Removes the runtime dependency on setuptools.
  - [v4.8.2 notes](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.2)
- ROCm wheels for CTranslate2 are **not on PyPI**. They ship as release assets (`rocm-python-wheels-Linux.zip`). The README says "If you have an AMD ROCm GPU, we provide specific Python wheels on the releases page" — [v4.8.2 assets](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.2), [README](https://github.com/OpenNMT/CTranslate2)
- CTranslate2 has no Vulkan, OpenVINO or SYCL backend. Its backends are CPU (MKL/oneDNN/OpenBLAS/Ruy), CUDA, and now ROCm/HIP — [README](https://github.com/OpenNMT/CTranslate2)

**whisper.cpp (ggml)**
- Releases: v1.8.4 (2026-03-19), v1.8.5 (2026-05-29), v1.8.6 (2026-06-02), v1.8.7 (2026-06-16), **v1.9.0 (2026-06-17)**, v1.9.1 (2026-06-19), v1.9.2 (2026-08-04), v1.9.3 (2026-08-20), v1.9.4 (2026-09-11). Nightly `bNNNN` builds also exist (b5130 on 2026-09-11) — [GitHub releases](https://github.com/ggml-org/whisper.cpp/releases)
- v1.9.0's headline change is "parakeet : add support for NVIDIA Parakeet" (PR #3735, merged 2026-06-16). It adds a `parakeet-cli` example, a `parakeet-quantize` tool, and converted models at `ggml-org/parakeet-GGUF` (e.g. `ggml-parakeet-tdt-0.6b-v3-f16.bin`). The CLI has `-ng/--no-gpu` and `-dev N` GPU device flags and prints token-level timestamps — [v1.9.0](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.0), [PR #3735](https://github.com/ggml-org/whisper.cpp/pull/3735), [parakeet-cli README](https://github.com/ggml-org/whisper.cpp/tree/master/examples/parakeet-cli)
- v1.9.2 follow-ups: Parakeet added to the xcframework build, hparams verification for Parakeet bins, VAD timestamp mapping fixes, and an exposed VAD speech-segments API — [v1.9.2](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.2)
- v1.9.3/v1.9.4 mostly sync ggml (ggml 0.20 → 0.23). That brings Vulkan work: a coopmat1 tuning path for Intel Xe ("SHMEM_STRIDE_PAD/APPLY_SLM_A_RESHAPE for coopmat1 on Intel Xe"), "Fixed gating logic for problematic Intel driver version", "vulkan: fix FA dequant path engagement", bf16 extension only when supported, plus SYCL fusions and OpenVINO backend updates. v1.9.3 notes the project adopted semantic versioning and a release process — [v1.9.3](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.3), [v1.9.4](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.4)
- whisper.cpp's README lists these backends: Vulkan, NVIDIA (CUDA), AMD ROCm, AMD Ryzen AI NPU, OpenVINO (encoder only; "x86 CPUs and Intel GPUs (integrated & discrete)"), Ascend, Moore Threads, and CPU-only — [README](https://github.com/ggml-org/whisper.cpp)
- Prebuilt release assets still lack Vulkan/ROCm Linux binaries. Open issue #4063 (2026-09-12): "whisperfile release … is missing such files, so the user cannot let whisper use GPU". A Windows Vulkan binary request (#3673) was closed 2026-09-08 — [#4063](https://github.com/ggml-org/whisper.cpp/issues/4063), [#3673](https://github.com/ggml-org/whisper.cpp/issues/3673)
- Repo: 54k stars, last push 2026-09-28 — [GitHub](https://github.com/ggml-org/whisper.cpp)

**pywhispercpp (Python binding for whisper.cpp)**
- Releases: v1.4.0 (2025-11-27), v1.4.1 (2025-12-30), v1.5.0 (2026-05-30; VAD support, updated to whisper.cpp v1.8.4 API), v1.5.1 (2026-08-22; "update whisper.cpp for CUDA 13.3"). The repo has 348 stars — a small, mostly single-maintainer project — [releases](https://github.com/absadiki/pywhispercpp/releases), [PyPI](https://pypi.org/project/pywhispercpp/)
- PyPI ships manylinux_2_28 and musllinux x86_64 wheels of about 4.7–5.5 MB. Dependencies are light: numpy, requests, tqdm, platformdirs — [PyPI](https://pypi.org/project/pywhispercpp/)
- Version alignment: pywhispercpp v1.5.0 tracked whisper.cpp v1.8.4, which predates the v1.9.0 Parakeet support. No pywhispercpp release notes mention Parakeet — [v1.5.0 notes](https://github.com/absadiki/pywhispercpp/releases/tag/v1.5.0)

**sherpa-onnx (k2-fsa)**
- Release cadence is very high: 1.12.29 (2026-03-12) through 1.12.40 (2026-04-24), then 1.13.0 (2026-04-28) through 1.13.8 (2026-09-10), roughly every 1–2 weeks — [GitHub releases](https://github.com/k2-fsa/sherpa-onnx/releases), [PyPI](https://pypi.org/project/sherpa-onnx/)
- Models added in 2026:
  - Moonshine v2 (1.12.28, 2026-02-28)
  - dynamic decoder layers for Canary (1.12.29)
  - **Qwen3-ASR** (1.12.34, 2026-03-26), plus hotwords and a per-stream language hint (1.12.35/36)
  - **Cohere Transcribe** (1.12.35, 2026-04-03)
  - multilingual **Nemotron-3.5 streaming ASR** (1.13.3, 2026-06-15)
  - FunASR-Nano and FireRedASR v2 CTC (present in 2026 release asset lists)
  - [release notes](https://github.com/k2-fsa/sherpa-onnx/releases)
- 1.13.5 (2026-08-11): "Build libs and Python wheels for CUDA 13". 1.13.4/1.13.5 add Qualcomm QNN runtimes for Parakeet TDT/CTC, Whisper, Moonshine and Nemotron (mobile/ARM only) — [v1.13.5](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.5), [v1.13.4](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.4)
- 1.13.8 (2026-09-10) changes:
  - "Add Intel NPU support through OpenVINO"
  - fixes for hallucinations on silent audio in Cohere Transcribe, Moonshine v2, FunASR-Nano and Qwen3-ASR
  - "Fix Canary empty transcript when the first decoded token is eos"
  - bundled onnxruntime updated to v1.28.2
  - [v1.13.8](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.8)
- The OpenVINO path needs a custom build. "the standard ONNX Runtime package bundled by sherpa-onnx does not include it". You must build sherpa-onnx against an OpenVINO-EP onnxruntime. It is documented for KWS and VAD on the NPU, and for KWS transducers it "runs the compute-heavy encoder on OpenVINO and keeps the small decoder and joiner on CPU" — [OPENVINO.md](https://github.com/k2-fsa/sherpa-onnx/blob/master/OPENVINO.md)

**ONNX Runtime**
- Release cadence is roughly monthly: 1.27.0 (2026-06-15), 1.28.0 (2026-07-25), 1.28.2 (2026-09-03), 1.29.0 (2026-08-17), 1.30.0 (2026-09-10). A separate WebGPU plugin EP is at v0.4.0 (2026-09-22) — [GitHub releases](https://github.com/microsoft/onnxruntime/releases), [PyPI](https://pypi.org/project/onnxruntime/)
- 1.30.0 adds fused CPU LinearAttention kernels (AVX-512/NEON/SVE) and AVX2 LayerNorm/RMSNorm acceleration. CPU FP16 Gemm/MatMul now falls back to FP32 when hardware lacks FP16. The WebGPU plugin EP ships Linux aarch64 packages — [v1.30.0](https://github.com/microsoft/onnxruntime/releases/tag/v1.30.0)
- **The ROCm EP was removed from the source tree.** The release notes say "Users are recommended to use Migraphx or Vitis AI EPs from AMD" (1.23 notes). On PyPI, `onnxruntime-rocm` is frozen at 1.22.2.post3 (2026-07-12 repost). `onnxruntime-migraphx` is current at 1.27.1 (2026-07-12) with manylinux_2_34 wheels — [v1.23.0 notes](https://github.com/microsoft/onnxruntime/releases/tag/v1.23.0), [PyPI onnxruntime-rocm](https://pypi.org/project/onnxruntime-rocm/), [PyPI onnxruntime-migraphx](https://pypi.org/project/onnxruntime-migraphx/)
- `onnxruntime-openvino` on PyPI lags: latest 1.24.1 (2026-02-26), versus OpenVINO itself at 2026.4.1 (2026-10-01) — [PyPI onnxruntime-openvino](https://pypi.org/project/onnxruntime-openvino/), [PyPI openvino](https://pypi.org/project/openvino/)
- Current `onnxruntime` and `onnxruntime-gpu` wheels declare `requires_python >=3.11` — [PyPI](https://pypi.org/project/onnxruntime/)

**onnx-asr (istupakov)**
- Releases: v0.10.2 (2026-01-18), v0.11.0 (2026-03-23), v0.12.0 (2026-07-15). v0.12.0 adds GigaAM Multilingual CTC and conv-based ONNX preprocessors (faster with CUDA EP), and notes that "Current onnxruntime-gpu versions require CUDA 13". It is pure Python (about 4 MB wheel) with an optional `onnxruntime` or `onnxruntime-gpu` extra, and pins out ORT 1.24.1, 1.25.x and 1.26.0. The repo has 381 stars — [v0.12.0](https://github.com/istupakov/onnx-asr/releases/tag/v0.12.0), [PyPI](https://pypi.org/project/onnx-asr/)

**NVIDIA NeMo**
- v2.7.0 (2026-02-26) through 2.7.3 (2026-04-23), then **v3.0.0 (2026-08-07), "NeMo Speech 3.0"**. This is the first major release after "the repo split and rename to NVIDIA-NeMo/Speech". LLM/VLM/diffusion/export/deploy moved to separate repos. It removed 800k deprecated LOC, migrated to uv, and includes vLLM integration for SpeechLM2 export/serving — [v3.0.0 notes](https://github.com/NVIDIA/NeMo/releases/tag/v3.0.0)

**llama.cpp (audio/ASR)**
- llama.cpp's multimodal (mtmd) docs now list audio-input models:
  - Ultravox 0.5
  - Qwen2-Audio (no pre-quantized GGUF: "very poor result")
  - **Voxtral Mini 3B-2507**
  - **Qwen3-ASR 0.6B and 1.7B** (`ggml-org/Qwen3-ASR-*-GGUF`)
  - Qwen2.5-Omni, Qwen3-Omni-30B-A3B
  - **Gemma 4 E2B/E4B** (audio+vision)
  - [docs/multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- Recent audio work: "mtmd: support Qwen3-TTS" (2026-08-04), "audio: fix mel preprocessor in LFM2 audio" (2026-09-25), server accepting `input_audio` data: URLs (2026-09-02) — [PR #26254](https://github.com/ggml-org/llama.cpp/pull/26254), [PR #29403](https://github.com/ggml-org/llama.cpp/pull/29403), [PR #27735](https://github.com/ggml-org/llama.cpp/pull/27735)
- Builds `b11316`–`b11321` were all published on 2026-10-01 (multiple builds per day) — [GitHub releases](https://github.com/ggml-org/llama.cpp/releases)

**vLLM**
- v0.26.0 (2026-07-27) through v0.30.0 (2026-09-22) — [GitHub releases](https://github.com/vllm-project/vllm/releases)
- The supported-models "Transcription" section lists:
  - Cohere-Transcribe (`CohereLabs/cohere-transcribe-03-2026`)
  - Gemma 3n
  - Granite Speech (`granite-4.0-1b-speech`, `granite-speech-3.3-2b`)
  - Granite Speech Plus (`granite-speech-4.1-2b-plus`)
  - Qwen3-ASR
  - Voxtral (3B/24B)
  - Whisper
  - "Realtime Transcription": `VoxtralRealtimeGeneration` (`mistralai/Voxtral-Mini-4B-Realtime-2602`) and `Qwen3ASRRealtimeGeneration` (Qwen3-ASR-0.6B)
  - [supported_models.md](https://github.com/vllm-project/vllm/blob/main/docs/models/supported_models.md)

**New ggml-based multi-model ASR runtimes (2026)**
- **CrispASR** (CrispStrobe/CrispASR): created 2026-03-29, MIT, 723 stars, v0.8.39 on 2026-09-30.
  - Packaging: "One C++ binary, 119 backends — 62 of them TTS". A fork of whisper.cpp with Python/Rust/Go/etc. bindings and Linux tarballs for CPU, `-cuda`, `-vulkan` and `-hip`. The `-hip`/`-vulkan` builds "do not fall back to CPU".
  - ASR backends include whisper, parakeet (TDT v3), canary, cohere, voxtral, voxtral4b (Voxtral-Mini-4B-Realtime-2602), qwen3, granite (incl. granite-4.1 / -plus / -nar), glm-asr, kyutai-stt, nemotron, moonshine and firered.
  - The README shows a parakeet example "3.2s audio in 0.32s (10.1x realtime)" with no hardware stated.
  - [GitHub](https://github.com/CrispStrobe/CrispASR)
- **audio.cpp** (0xShug0/audio.cpp): created 2026-06-23, 3.2k stars, v0.9.0 on 2026-09-30. It is described as an "all-in-one, pure C++ inference engine for audio models, powered by ggml" (TTS, STT, VAD…). License: NOASSERTION — [GitHub](https://github.com/0xShug0/audio.cpp)

**Kyutai STT**
- The official repo (kyutai-labs/delayed-streams-modeling) offers PyTorch "for research", a Rust server "for production" (websockets; "on a L40S GPU … 64 simultaneous connections at a real-time factor of 3x"), and MLX for Apple. Last push was 2026-01-26 — [README](https://github.com/kyutai-labs/delayed-streams-modeling)

**Candle / MLX**
- huggingface/candle: active (last push 2026-09-28, 21k stars). No GitHub Releases are published via the API (versioned via crates.io). I did not verify which ASR models it supports in 2026 — [GitHub](https://github.com/huggingface/candle)
- MLX is Apple-silicon only and not applicable to Linux x86. Kyutai's on-device path is MLX for Mac/iPhone — [Kyutai README](https://github.com/kyutai-labs/delayed-streams-modeling)

### Inferences
- faster-whisper is **stagnant as a wrapper, not dead**. CTranslate2 underneath is well maintained, and the wrapper is thin enough for WhisperAloud to keep using or vendor. Its value rests on CTranslate2, and **CTranslate2 still has no Vulkan/OpenVINO path**. For AMD/Intel iGPUs it is not the answer. AMD's only route is ROCm wheels off-PyPI.
- whisper.cpp ≥1.9 can serve both Whisper and Parakeet TDT v3 through one ggml runtime with Vulkan/ROCm/SYCL/OpenVINO backends. That makes it more attractive for the WHISP backlog item than when it was Whisper-only. Caveat: Parakeet support is new (June 2026, PR described as "work in progress"). It is exposed via `parakeet-cli`, and I did not confirm a stable C-API in `whisper.h` or pywhispercpp bindings.
- sherpa-onnx is the de facto "model zoo" runtime for non-LLM and small-LLM ASR (Parakeet, Canary, Moonshine v2, Qwen3-ASR, Cohere Transcribe, Nemotron streaming, FunASR-Nano, FireRed). Its weakness is GPU breadth on Linux: CPU and CUDA only in the stock wheels, with OpenVINO needing a custom build.
- LLM-style ASR models (Voxtral, Granite Speech, Qwen3-ASR 1.7B, Gemma) are covered best by vLLM (server/GPU-oriented, heavy) or by llama.cpp/CrispASR (ggml; CPU/Vulkan/ROCm-capable, lighter).

### Gaps
- Whether `whisper.h` exposes a Parakeet API (vs only the `parakeet-cli` example) was not verified. Neither was whether pywhispercpp ≥1.5.1 can drive Parakeet.
- Candle ASR model coverage in 2026 was not researched (no reliable source found within budget).
- No transformers.js-like Linux-native option was investigated. transformers.js runs on ONNX Runtime Web/WebGPU, which is not a natural fit for a Python daemon.

## 2. Which runtime runs which model (Parakeet/Canary and 2026 SOTA)

### Takeaway
No single runtime covers everything:
- **sherpa-onnx**: transducer/CTC/AED models plus Qwen3-ASR and Cohere Transcribe.
- **whisper.cpp**: Whisper and Parakeet TDT.
- **llama.cpp**: Voxtral 3B, Qwen3-ASR, Gemma 4 audio.
- **vLLM**: all the big LLM-ASR models, GPU-server oriented.
- **CrispASR**: claims the broadest coverage in a single ggml binary, including Voxtral Realtime 4B, Granite 4.1, Kyutai STT and Canary, but is a young project.

### Cited Findings (support matrix)

| Model | sherpa-onnx | whisper.cpp | llama.cpp (mtmd) | vLLM | CrispASR (ggml) | onnx-asr | CTranslate2 |
|---|---|---|---|---|---|---|---|
| Whisper (all sizes, turbo) | yes | yes | – | yes | yes | yes | yes |
| Parakeet TDT 0.6B v2/v3 | yes (int8) | **yes since v1.9.0** | – | – | yes | yes | – |
| Canary 1B v2 / Canary family | yes (Canary fixes in 1.13.8) | – | – | – | yes | (not verified) | – |
| Nemotron(-3.5) streaming 0.6B | yes (1.13.3) | – | – | – | yes (`nemotron`) | – | – |
| Moonshine v2 | yes (1.12.28) | – | – | – | yes | – | – |
| Qwen3-ASR 0.6B/1.7B | yes (1.12.34) | – | **yes** (GGUF) | yes (+Realtime 0.6B) | yes | – | – |
| Cohere Transcribe 03-2026 | yes (1.12.35) | – | – | yes | yes | – | – |
| Voxtral Mini 3B-2507 | – | – | yes | yes | yes | – | – |
| Voxtral Mini 4B Realtime 2602 | – | – | – | yes (Realtime) | yes (`voxtral4b`) | – | – |
| Granite Speech 3.3 / 4.0-1b / 4.1-2b-plus | – | – | – | yes | yes | – | – |
| Kyutai STT 1B | – | – | – | – | yes (`kyutai-stt`) | – | – |
| Gemma 4 E2B/E4B (audio) | – | – | yes | (Gemma 3n listed) | – | – | – |
| GigaAM multilingual CTC | (GigaAM v2 historically) | – | – | – | – | yes (0.12.0) | – |

Sources for the table:
- sherpa-onnx — [release notes](https://github.com/k2-fsa/sherpa-onnx/releases)
- whisper.cpp — [v1.9.0](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.0)
- llama.cpp — [multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- vLLM — [supported_models.md](https://github.com/vllm-project/vllm/blob/main/docs/models/supported_models.md)
- CrispASR — [README](https://github.com/CrispStrobe/CrispASR)
- onnx-asr — [v0.12.0](https://github.com/istupakov/onnx-asr/releases/tag/v0.12.0)
- Kyutai's own runtimes are PyTorch/Rust/MLX — [README](https://github.com/kyutai-labs/delayed-streams-modeling)
- NeMo 3.0 is the reference (PyTorch) runtime for Parakeet/Canary/Nemotron and adds vLLM export for SpeechLM2 — [NeMo v3.0.0](https://github.com/NVIDIA/NeMo/releases/tag/v3.0.0)
- CrispASR states its "speech-LLM backends (qwen3, voxtral, voxtral4b, granite, glm-asr, kyutai-stt) inject audio encoder frames directly into an autoregressive language model's input embeddings" — [CrispASR README](https://github.com/CrispStrobe/CrispASR)

### Inferences
- If WhisperAloud wants to add Qwen3-ASR or Cohere Transcribe, its existing **sherpa-onnx dependency already covers them** with no new runtime. That is the lowest-cost way to reach the newer models.
- Voxtral / Granite Speech / Kyutai on a laptop without NVIDIA would need a ggml runtime (llama.cpp or CrispASR) or PyTorch. vLLM is impractical for a desktop daemon (CUDA-centric, heavy).
- Silence-hallucination fixes landed in sherpa-onnx in Sep 2026 (1.13.7/1.13.8) for the LLM-ish decoders (Qwen3-ASR, Cohere, Moonshine v2, FunASR-Nano). Pin ≥1.13.8 if those models are adopted.

### Gaps
- Canary support in onnx-asr and whisper.cpp was not verified.
- No primary source found for NVIDIA Canary-Qwen / newer NVIDIA 2026 releases in non-NeMo runtimes, beyond CrispASR claims.
- CrispASR's claims (119 backends, numerical parity) are self-reported and not independently verified.

## 3. Vulkan on AMD/Intel iGPU: real-world numbers, stability, worth it vs CPU int8?

### Takeaway
No rigorous, recent (2026) whisper.cpp Vulkan RTF numbers for laptop iGPUs (Radeon 780M/890M, Intel Xe/Arc/Lunar Lake) exist in the sources found. Available evidence is anecdotal: about 2.5–3.3× over CPU in one dictation app's table, plus scattered issue reports. Stability has improved (ggml syncs with Intel-Xe/AMD fixes), but there is a recurring tail of driver-specific bugs. The **project must benchmark itself**. Parakeet int8 on CPU (RTF ≈0.15–0.18 on i7-1360P) already sets a high bar for an iGPU path.

### Cited Findings
- Vocalinux, a Linux dictation app using whisper.cpp, recommends Vulkan for AMD/Intel/NVIDIA ("No vendor-specific drivers required"). Its table for 5-second clips (hardware unspecified, "mid-range Vulkan-compatible GPUs", undated):

  | Model | CPU | GPU | Speedup |
  |---|---|---|---|
  | small | ~1.5s | ~0.5s | 3× |
  | medium | ~4s | ~1.2s | 3.3× |
  | large | ~8s | ~2.5s | 3.2× |

  It also warns: "Some Intel integrated GPUs don't fully support the compute features needed for whisper.cpp GPU acceleration. Vocalinux v0.7.0+ automatically detects incompatible Intel GPUs and falls back to CPU." — [Vocalinux GPU page](https://vocalinux.com/gpu-acceleration/)
- WhisperX (not whisper.cpp) with ROCm in Docker on a Radeon 890M took 34 s for a 4:40 German recording with large-v3 (≈RTF 0.12) — [dominic-boettger.com](https://dominic-boettger.com/blog/whisperx-gpu-amd-radeon-rocm-docker/) (search-snippet level; not fetched in full)
- whisper.cpp 1.8.0 works on Strix Halo (Ryzen AI Max+ 395) with ROCm 7.0.1 — [Discussion #3460](https://github.com/ggml-org/whisper.cpp/discussions/3460)
- Vulkan bugs reported in 2026 on the whisper.cpp tracker:
  - "[Vulkan] Beam size 8 crashes with AMD Radeon 780M when VAD is enabled (large-v2)" (2026-03-26, closed)
  - "whisper-server crashes in amdvlk64.dll with flash attention enabled on AMD RX 9070 XT" (2026-05, closed)
  - ggml-vulkan `supports_op` rejecting ops on low-`maxStorageBufferRange` devices (closed)
  - Windows-only build hangs or failures in `vulkan-shaders-gen` (open)
  - An older report of garbled output on the 780M with Vulkan (#2596)
  - [#3723](https://github.com/ggml-org/whisper.cpp/issues/3723), [#3806](https://github.com/ggml-org/whisper.cpp/issues/3806), [#3777](https://github.com/ggml-org/whisper.cpp/issues/3777), [#3958](https://github.com/ggml-org/whisper.cpp/issues/3958), [#2596](https://github.com/ggml-org/whisper.cpp/issues/2596)
- Upstream ggml (synced into whisper.cpp v1.9.3) added Intel Xe coopmat1 tuning and "Fixed gating logic for problematic Intel driver version" — [v1.9.3](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.3)
- The whisper.cpp benchmark thread (#89, 187 comments) has 2026 entries, but they are mostly desktop dGPUs (RX 9070 XT, Vulkan, Windows) or CPU (Ryzen 7 6800H, Kubuntu 26.04, base.en encode 661 ms at 8 threads). I found no iGPU Vulkan row in 2026 — [Issue #89](https://github.com/ggml-org/whisper.cpp/issues/89)
- A 2026 comparison article (updated 2026-08-28) lists Vulkan as supported for AMD/Intel but gives **no Vulkan numbers**. It does report faster-whisper large-v3 int8 at ~12× real time on an RTX 4070 vs whisper.cpp CUDA at ~8×, and CPU base int8 at ~20× (faster-whisper) vs ~15× (whisper.cpp) — [PromptQuorum](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026) (secondary source, thin methodology)
- A search snippet attributes "Vulkan is about 10x faster compared to the CPU backend" to an RX 580 (dGPU) guide. This does not transfer to iGPUs — [deepwiki rx580 guide](https://deepwiki.com/aivisionslab-studios/rx580-local-ai-guide/3.3-compiling-whisper.cpp-with-vulkan)
- whisper.cpp also offers an **OpenVINO encoder path** for "x86 CPUs and Intel GPUs (integrated & discrete)". It needs a separate encoder IR model produced by a Python conversion script — [README](https://github.com/ggml-org/whisper.cpp#openvino-support)

### Inferences
- The decoder in whisper.cpp is token-by-token and memory-bound, so iGPU gains concentrate in the encoder. For short dictation clips (5–15 s), launch/upload overhead eats into gains. The ~3× speedups in the Vocalinux table are plausible but unverified for specific iGPUs.
- Compare against the project's baseline: Parakeet TDT v3 int8 on CPU is RTF 0.151–0.184 on an i7-1360P, versus faster-whisper medium int8 CPU at RTF 0.49–0.52 ([bench/MATRIX.md](../../bench/MATRIX.md)). The case for Vulkan is strongest for **Whisper medium/large-v3-turbo on AMD 780M/890M-class iGPUs**. For Parakeet (already fast on CPU) or weak Intel Xe iGPUs, the gain may not justify the complexity.
- Because Vulkan builds don't fall back to CPU in some distributions (CrispASR explicitly), WhisperAloud needs runtime device probing and a CPU fallback. Vocalinux does this already.

### Gaps
- No credible 2026 RTF for whisper.cpp Vulkan on Radeon 780M/890M or Intel Arc/Xe-LPG/Lunar Lake iGPU for large-v3-turbo or Parakeet. **Recommendation: run `whisper-bench` and the project's FLEURS bench on target iGPUs.**
- No data on Parakeet-on-whisper.cpp with Vulkan at all.
- ROCm on consumer iGPUs (gfx1103/780M) is still unofficial (requires HSA_OVERRIDE tricks, per community blogs). No primary 2026 source was verified.

## 4. CPU performance: int8, RTF on laptop CPUs (Whisper turbo vs Parakeet vs newer)

### Takeaway
On CPU, transducer models (Parakeet TDT v3 int8 via sherpa-onnx/onnx-asr) remain the speed and accuracy sweet spot for dictation. The project measured RTF ≈0.15–0.18 on an i7-1360P, slightly faster than Whisper small int8 and ~3× faster than Whisper medium int8. Independent 2026 CPU RTF numbers for Whisper large-v3-turbo vs Parakeet on laptop CPUs are scarce.

### Cited Findings
- Project measurements, i7-1360P CPU, FLEURS:

  | Lang | Model | WER | RTF |
  |---|---|---|---|
  | en | Parakeet int8 (sherpa-onnx) | 10.5% | 0.184 |
  | es | Parakeet int8 (sherpa-onnx) | 3.2% | 0.151 |
  | en | faster-whisper small int8 | 7.2% | 0.201 |
  | es | faster-whisper small int8 | 5.1% | 0.161 |
  | en | faster-whisper medium int8 | 5.3% | 0.518 |
  | es | faster-whisper medium int8 | 5.3% | 0.488 |

  Parakeet RAM was ~1.2–1.4 GB, model 671 MB — [bench/MATRIX.md](../../bench/MATRIX.md)
- onnx-asr reports Parakeet v2/v3 at RTFx ≈36 on a Ryzen 9800X3D CPU (desktop, search snippet from PyPI page) — [onnx-asr PyPI](https://pypi.org/project/onnx-asr/)
- "Parakeet-TDT-0.6B-v3 attains the highest throughput among all systems (RTFx = 3332.74)" — on GPU with batching, not CPU — [Canary-1B-v2 & Parakeet-TDT-0.6B-v3 paper](https://arxiv.org/pdf/2509.14128)
- A secondary blog (2026-07-18) gives Parakeet TDT v3 average WER 6.34% vs Whisper large-v3 ~7.4% on the Open ASR Leaderboard. INT8 ONNX sizes: Parakeet 680 MB, Nemotron 3.5 ASR 632–650 MB. It recommends sherpa-onnx for NVIDIA models and whisper.cpp for Whisper — [OpenWhispr blog](https://openwhispr.com/blog/parakeet-vs-whisper-vs-nemotron)
- Recent CPU-side runtime improvements:
  - CTranslate2 v4.8.0 enabled MKL PACKED_GEMM by default for Intel CPUs ([v4.8.0](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.0))
  - ONNX Runtime 1.30 added AVX2 LayerNorm/RMSNorm and AVX-512 fused kernels ([v1.30.0](https://github.com/microsoft/onnxruntime/releases/tag/v1.30.0))
- An M5 Max (not Linux, but indicative of the CPU vs GPU ratio) with whisper.cpp 1.9.4, 30 s clip, large-v3-turbo: CPU 11.8 s (RTF 0.39) vs Metal 1.1 s — [openscribe #29](https://github.com/streichsbaer/openscribe/issues/29)

### Inferences
- On a typical 2023–2025 Intel/AMD laptop CPU, Whisper large-v3-turbo int8 is likely RTF ~0.3–0.6. That would be slower than Parakeet by 2–3×, though this is extrapolated from the medium numbers and the M5 Max CPU figure. Turbo has 4 decoder layers vs medium's 24, but a large-v3 encoder.
- After CTranslate2 4.8 (MKL packed GEMM), the project should re-run its FLEURS bench. It may shift faster-whisper CPU numbers on Intel CPUs.

### Gaps
- No primary 2026 CPU benchmark comparing Parakeet (sherpa-onnx vs onnx-asr vs whisper.cpp ggml) on the same laptop CPU.
- No CPU RTF numbers found for Qwen3-ASR 0.6B, Cohere Transcribe, Moonshine v2 or Voxtral on x86 laptops.

## 5. Python packaging: wheels, CUDA variants, dependency weight, Debian/Flatpak bundling

### Takeaway
- **sherpa-onnx and pywhispercpp have the lightest Python packaging** (wheels of a few MB, manylinux). sherpa-onnx CPU wheels bundle onnxruntime via `sherpa-onnx-core`, and CUDA 12/13 builds exist.
- **CTranslate2** is manylinux on PyPI (~40 MB) for CPU+CUDA. ROCm is only via GitHub release zips.
- **ONNX Runtime GPU** wheels are huge (~247 MB) and now need CUDA 13 and Python ≥3.11. The ROCm EP is gone (MIGraphX only).
- **Vulkan for whisper.cpp is not available as a prebuilt Python wheel or Linux binary.** You build it from source (`GGML_VULKAN=1`). That suits the project's existing vendored-engine Debian package and a Flatpak module, but adds build complexity.

### Cited Findings
- PyPI wheel facts (checked 2026-10-01):

  | Package | Latest | Linux x86_64 wheel | Notes |
  |---|---|---|---|
  | ctranslate2 | 4.8.2 | manylinux_2_27/2_28, max ≈40 MB | |
  | sherpa-onnx | 1.13.8 | manylinux2014, ≈4.4 MB (cp312) | `Requires-Dist: sherpa-onnx-core==1.13.8` |
  | onnxruntime | 1.30.0 | manylinux_2_28, ≈24 MB | Python ≥3.11 |
  | onnxruntime-gpu | 1.30.0 | ≈247 MB | Python ≥3.11 |
  | onnxruntime-openvino | 1.24.1 | ≈84 MB | lags upstream |
  | onnxruntime-migraphx | 1.27.1 | manylinux_2_34 | |
  | onnxruntime-rocm | 1.22.2.post3 | – | frozen |
  | pywhispercpp | 1.5.1 | manylinux_2_28 + musllinux, ≈4.7 MB | |
  | openvino | 2026.4.1 | ≈59 MB | |
  | faster-whisper / onnx-asr / nemo-toolkit | – | pure-Python wheels | |

  [PyPI ctranslate2](https://pypi.org/project/ctranslate2/), [sherpa-onnx](https://pypi.org/project/sherpa-onnx/), [onnxruntime](https://pypi.org/project/onnxruntime/), [onnxruntime-gpu](https://pypi.org/project/onnxruntime-gpu/), [onnxruntime-openvino](https://pypi.org/project/onnxruntime-openvino/), [onnxruntime-migraphx](https://pypi.org/project/onnxruntime-migraphx/), [onnxruntime-rocm](https://pypi.org/project/onnxruntime-rocm/), [pywhispercpp](https://pypi.org/project/pywhispercpp/), [openvino](https://pypi.org/project/openvino/)
- sherpa-onnx built "libs and Python wheels for CUDA 13" in 1.13.5 (2026-08-11) — [v1.13.5](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.5)
- onnx-asr: "Current onnxruntime-gpu versions require CUDA 13". It documents separate CUDA 12 and CUDA 13 install commands — [onnx-asr v0.12.0](https://github.com/istupakov/onnx-asr/releases/tag/v0.12.0)
- CTranslate2 v4.8.2 lazy-loads converters so torch isn't loaded for inference, and drops the setuptools runtime dependency. Both help slim bundles — [v4.8.2](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.2)
- CTranslate2 ROCm wheels ship only as a GitHub release asset `rocm-python-wheels-Linux.zip` — [v4.8.2 release](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.2)
- whisper.cpp release assets lack Linux Vulkan/ROCm binaries (open issue #4063). There are also Vulkan build issues on Windows (#3958, #3973) and armhf memory exhaustion while compiling shaders (#4076) — [#4063](https://github.com/ggml-org/whisper.cpp/issues/4063), [#3973](https://github.com/ggml-org/whisper.cpp/issues/3973), [#4076](https://github.com/ggml-org/whisper.cpp/issues/4076)
- A 2026-04 issue complained that whisper.cpp "Shared objects unconditionally installed to /usr/lib" (closed). That is relevant to Debian packaging hygiene — [#3772](https://github.com/ggml-org/whisper.cpp/issues/3772)
- CrispASR ships prebuilt Linux tarballs (`crispasr-linux-x86_64.tar.gz`, `-cuda`, `-vulkan`, `-hip`) with Python bindings. Only the `-cuda` build falls back to CPU (since v0.8.30) — [CrispASR README](https://github.com/CrispStrobe/CrispASR)
- NeMo 3.0 uses `uv sync`, which "may replace Python/PyTorch/CUDA inside .venv". It is a PyTorch stack, unsuitable as an embedded desktop runtime — [NeMo v3.0.0](https://github.com/NVIDIA/NeMo/releases/tag/v3.0.0)

### Inferences
- **Debian:** the existing `whisper-aloud-engine-ctranslate2` (vendored venv) pattern extends naturally to a `whisper-aloud-engine-whispercpp-vulkan` package. That package would build whisper.cpp with `-DGGML_VULKAN=ON` and depend on `libvulkan1` plus Mesa (RADV/ANV) drivers, which Debian already ships. Vulkan needs only the loader and ICD, no vendor SDK at runtime. This is the main packaging advantage over ROCm (huge, unofficial on iGPUs) and over OpenVINO (separate IR conversion, lagging ORT-OpenVINO wheels).
- **Flatpak:** Vulkan works inside Flatpak through the GL/Vulkan extension (`org.freedesktop.Platform.GL.*`) with `--device=dri`. CUDA needs the NVIDIA GL extension plus host CUDA libs, and ROCm in Flatpak is impractical. This is general Flatpak knowledge, not re-verified in this session.
- Python ≥3.11 for current onnxruntime is fine on Debian 13 (Python 3.13), but it matters for older LTS targets.
- Since pywhispercpp's PyPI wheels are presumably CPU-only (4.7 MB, no Vulkan mention), the project would need either a source build of pywhispercpp with `GGML_VULKAN=1` or a thin ctypes/cffi wrapper over `libwhisper.so`. The latter avoids depending on a 348-star single-maintainer binding that lags upstream.

### Gaps
- I did not verify whether pywhispercpp's PyPI wheels include any GPU backend, or the exact env var it honors for a Vulkan source build.
- I did not verify whether sherpa-onnx's CUDA Python wheels are on PyPI or only on the k2-fsa wheel index.
- No first-hand Flatpak manifests for whisper.cpp+Vulkan in 2026 were reviewed.
