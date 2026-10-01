# WHISP-55 spike: NVIDIA Nemotron 3.5 ASR Streaming 0.6B in sherpa-onnx

Date: 2026-10-01. Spike only — no code is wired into the daemon.

## Decision-oriented summary

- **It is available and it works.** sherpa-onnx officially ships ONNX exports of the
  multilingual Nemotron 3.5 ASR Streaming 0.6B model, in five chunk sizes
  (80/160/320/560/1120 ms), as INT8-only artifacts on its "asr-models" GitHub release.
  No FP32 export is published by the project.
- **Minimum sherpa-onnx version is 1.13.3** (2026-06-15), the first release containing
  the merged multilingual support (PR #3671). Current latest is 1.13.8 (2026-09-10), as
  the ticket assumed. Both have Linux x86_64 (manylinux2014) CPython wheels for
  3.9–3.14 on PyPI; there is **no CUDA wheel on PyPI** — CUDA builds are distributed via
  k2-fsa's own wheel index, consistent with `docs/research/.../notes/runtimes.md`.
- **Ran it successfully on CPU**, 560 ms chunks, Spanish LibriVox audio, 2 OMP threads:
  **RTF ≈ 0.21**, **peak RSS ≈ 990 MB** (dominated by the 657 MB encoder.int8.onnx
  resident + onnxruntime overhead), transcript quality subjectively good (correctly
  transcribed the standard LibriVox public-domain disclaimer and the poem's opening
  line, with a couple of proper-noun/punctuation glitches).
- **License is OpenMDW-1.1**, a permissive MIT-like license from The Linux Foundation
  with a patent-retaliation clause (see §4). It does **not** forbid redistribution,
  runtime auto-download, or bundling in a `.deb`; it requires keeping the license text
  and origin notices when you redistribute the Model Materials. This is a factual
  reading, not a legal conclusion — route it to human legal review as requested.
- **Recommendation for WhisperAloud**: this is a credible native-streaming candidate
  for Spanish + English, same runtime (sherpa-onnx) and similar footprint to the
  already-integrated Parakeet TDT v3. Before adopting: (a) get a human legal read on
  OpenMDW-1.1 vs. the project's existing CC-BY-4.0 (Parakeet)/MIT (Moonshine)
  dependencies, (b) benchmark WER per chunk size on real dictation audio, not just
  FLEURS, (c) decide 560 ms vs 1120 ms trade-off (latency vs the ~0.15pp WER gain quoted
  by NVIDIA), (d) note `modified_beam_search`/hotwords are not supported yet for
  `model_type="nemotron"` (greedy_search only), which blocks custom-vocabulary biasing.

---

## 1. Released sherpa-onnx export(s)

Official assets are attached to the sherpa-onnx GitHub release tagged **`asr-models`**
(a rolling "model zoo" tag, not a version tag). Verified via the GitHub API
(`api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models`) on 2026-10-01:

| File | Size (bytes) | Chunk | Quant |
|---|---:|---|---|
| `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-80ms-int8-2026-06-11.tar.bz2` | 475,274,007 | 80 ms | int8 |
| `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-160ms-int8-2026-06-11.tar.bz2` | 475,273,363 | 160 ms | int8 |
| `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11.tar.bz2` | 475,272,949 | 320 ms | int8 |
| `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2` | 475,271,763 | 560 ms | int8 |
| `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-1120ms-int8-2026-06-11.tar.bz2` | 475,276,334 | 1120 ms | int8 |

Download URL pattern:
`https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/<filename>`

**No FP32/FP16 export of the multilingual model is published** by sherpa-onnx (only
the five int8 chunk variants above). There is a separate, earlier **English-only**
Nemotron Speech Streaming EN 0.6B family (`sherpa-onnx-nemotron-speech-streaming-en-0.6b-*-int8-2026-04-25.tar.bz2`, ~464 MB each, plus an older
`...-int8-2026-01-14.tar.bz2`), also int8-only.

Extracted contents of the 560 ms archive (downloaded and inspected for this spike):

```
sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11/
├── encoder.int8.onnx   657,601,403 bytes
├── decoder.int8.onnx    14,978,075 bytes
├── joiner.int8.onnx      9,504,438 bytes
├── tokens.txt               131,440 bytes
└── README.md
```

(extracted total ≈ 682 MB vs. 475 MB compressed; the encoder dominates). The bundled
`README.md` says: *"Use per-stream language strings such as `"en"`, `"ja"`, or
`"auto"`."* — confirming the language is a per-stream runtime option, not baked into
the chunk-size variant.

Sources: [asr-models release (GitHub API)](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models), file inspection in this spike's `.spike-models/` (gitignored, not committed).

## 2. Python API

### Factory and parameters

The model is loaded through the existing streaming-transducer factory, with
`model_type="nemotron"`:

```python
import sherpa_onnx

recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f"{model_dir}/tokens.txt",
    encoder=f"{model_dir}/encoder.int8.onnx",
    decoder=f"{model_dir}/decoder.int8.onnx",
    joiner=f"{model_dir}/joiner.int8.onnx",
    sample_rate=16000,
    feature_dim=128,
    model_type="nemotron",
    decoding_method="greedy_search",  # only supported method for this model_type
)
```

- **Decoding**: only `decoding_method="greedy_search"` is supported for
  `model_type="nemotron"`. A request to support `modified_beam_search` (needed for
  hotwords/contextual biasing) is tracked in
  [issue #3572](https://github.com/k2-fsa/sherpa-onnx/issues/3572) (opened
  2026-05-03); a linked PR (#3895) shows as closed but this spike could not confirm
  from the issue thread alone whether beam search landed or in which release — verify
  against the CHANGELOG before relying on it.
- **Language selection**: per-stream, via the general `OnlineStream.set_option(key,
  value)` call (confirmed against the installed 1.13.8 build — there is no
  `set_config()` method, despite that pattern being folklore in some blog posts):
  ```python
  stream = recognizer.create_stream()
  stream.set_option("language", "es")  # or "en", "auto", etc.
  ```
  This matches the usage shown for the (non-streaming) NeMo Canary models in
  sherpa-onnx's own `offline_recognizer.py` (`stream.set_option("language", "de")`),
  and the bundled model README's "en"/"ja"/"auto" examples.
- **Built-in punctuation/casing**: the underlying Nemotron 3.5 model has built-in
  punctuation and capitalization (per NVIDIA's model card); this spike's transcript
  shows capitalized sentence starts and a period, consistent with that. sherpa-onnx
  does not appear to add or strip punctuation itself for this model type.
- **Multilingual mechanism**: per PR #3671, the encoder ONNX graph carries a 6th input
  (`prompt_index`) and an embedded language→prompt-id metadata table; sherpa-onnx's
  `set_option("language", ...)` resolves the string to that id internally
  (`IsMultilingual()` / `GetLanguagePromptId()` in `online-transducer-nemo-model.cc`).
  Passing `"auto"` requests language auto-detection (prompt index 101 per the
  streaming.md research note).

### Minimal working snippet

See `scripts/spikes/nemotron_stream_demo.py` in this branch — it streams a 16 kHz
mono WAV in fixed-size chunks, prints partials as they change, and reports RTF and
peak RSS. Core loop:

```python
stream = recognizer.create_stream()
stream.set_option("language", "es")
for chunk in chunks_of(samples, chunk_size):
    stream.accept_waveform(sample_rate, chunk)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    text = recognizer.get_result(stream)  # plain str, not an object with .text
stream.input_finished()
while recognizer.is_ready(stream):
    recognizer.decode_stream(stream)
final_text = recognizer.get_result(stream)
```

Two corrections made while actually running it (both now reflected in the script):
`recognizer.get_result(stream)` returns a **plain `str`**, not an object with a
`.text` attribute; and the language option setter is `stream.set_option(key, value)`,
not a JSON `set_config()` call (which does not exist on `OnlineStream`).

### Actually run: 560 ms chunks, Spanish audio

- **Audio**: a 30-second slice (ffmpeg-trimmed, resampled to 16 kHz mono PCM16) of a
  public-domain LibriVox recording, "Platero y Yo" ch. 7, read by a LibriVox volunteer
  — [archive.org/details/plateroyyo_1505_librivox](https://archive.org/details/plateroyyo_1505_librivox),
  file `plateroyyo_07_jimenez.mp3`. LibriVox recordings are public domain in the US.
- **Environment**: throwaway venv inside the worktree (`.spike-venv/`, gitignored),
  `sherpa-onnx==1.13.8`, `OMP_NUM_THREADS=2`, CPU only, no GPU, no microphone.
- **Partials** (sample, see full run in script output — correctly transcribes the
  standard LibriVox disclaimer read at the start of the chapter):
  ```
  [partial @  3.92s] Séptima sección de platero y yo
  [partial @ 11.20s] ...están en el dominio público
  [partial @ 29.68s] ...Dulce Platero Trotón
  ```
- **Results**:

  | Metric | Value |
  |---|---|
  | Audio duration | 30.00 s |
  | Wall time | 6.27–6.35 s (two runs) |
  | RTF | 0.209–0.212 |
  | Peak RSS | 983–993 MB |

  RSS is dominated by the 657 MB encoder.int8.onnx being memory-mapped/loaded plus
  onnxruntime session overhead; it is not a realistic steady-state number for a
  daemon holding one long-lived stream (no repeated allocation), but it is a real
  one-time cost per recognizer instance on a constrained laptop.
- Transcript quality was subjectively good for connected speech but mangled a reader's
  name and lost a touch of punctuation — consistent with NVIDIA's ~4.1–4.9% FLEURS WER
  figures for Spanish, not a controlled WER measurement (no reference transcript was
  prepared for this 30 s clip).

## 3. Versions, wheels, CPU vs CUDA

- **Minimum version**: 1.13.3 (2026-06-15) — first release whose changelog credits
  PR #3671 ("Add multilingual Nemotron-3.5 streaming ASR support"), merged
  2026-06-12. Confirmed via
  [v1.13.3 release notes](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.3).
- **Latest**: 1.13.8, published 2026-09-10 — confirmed via
  [v1.13.8 release notes](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.8)
  (17 commits to master since, no new tagged release as of 2026-10-01).
- **Wheels** (checked PyPI JSON API for both 1.13.3 and 1.13.8): Linux x86_64
  `manylinux2014_x86_64` CPython wheels exist for **3.9, 3.10, 3.11, 3.12, 3.13, and
  3.14** (plus aarch64, armv7l, macOS, Windows). No `cp3xx` gap in the 3.11–3.13 range
  the ticket asked about.
- **CPU vs CUDA**: the PyPI `sherpa-onnx` package is **CPU-only**; there is no
  `sherpa-onnx-cuda` or CUDA-tagged wheel on PyPI (`pip index versions
  sherpa-onnx-cuda` → no match). CUDA 12/13 builds are published separately via
  k2-fsa's own wheel index/GitHub Actions artifacts, matching what
  `docs/research/2026-10-01-sota-stt-offline/notes/runtimes.md` already says about
  sherpa-onnx's GPU packaging. WhisperAloud's existing `[parakeet]` extra
  (`build/pyproject` — see `d3c282b`) should already reflect this; no change needed
  for CPU-only use.

## 4. License (OpenMDW-1.1) — factual summary for legal review

Full text fetched from the canonical repo:
[`OpenMDW/OpenMDW` 1.1/LICENSE.OpenMDW-1.1](https://github.com/OpenMDW/OpenMDW/blob/main/1.1/LICENSE.OpenMDW-1.1)
(mirrors [openmdw.ai/license/1-1](https://openmdw.ai/license/1-1/), a Linux Foundation
project). This is **not Apache-2.0 or CC-BY**; it is its own short permissive license.
No legal conclusion is drawn here — the obligations below are a plain reading of the
text for a lawyer to confirm.

- **Scope**: covers "Model Materials" — the model weights/architecture plus
  "all related artifacts (including associated data, documentation and software)"
  provided under the license. For the Nemotron 3.5 HF release this would be the model
  weights/config/README as published by NVIDIA; it is less clear whether it extends to
  a third-party ONNX re-export (sherpa-onnx's conversion) — the license text does not
  address derivative-format re-exports explicitly.
- **Grant**: "permission is hereby granted, free of charge, to deal in the Model
  Materials without restriction, including under all copyright, patent, database, and
  trade secret rights" — broad, MIT-style grant, no field-of-use restriction.
- **Redistribution obligation**: "If you distribute any portion of the Model
  Materials, you shall retain in your distribution (1) a copy of this agreement, and
  (2) all copyright notices and other notices of origin included in the Model
  Materials that are applicable to your distribution." This is the only affirmative
  obligation in the license. Plain reading: bundling the model files in a `.deb`
  package, or auto-downloading them at runtime and shipping the license text alongside
  (e.g. in the package's `debian/copyright` or a LICENSES directory), would satisfy
  this — but the license does not define "distribute" and does not distinguish
  "bundled in a package" from "fetched at runtime by code you ship"; a lawyer should
  confirm whether runtime auto-download by WhisperAloud code counts as "distributing"
  the model and what "retain in your distribution" means for a download-on-demand
  flow (e.g., whether the license text must ship in the .deb even if the model itself
  is fetched later).
- **No output restrictions**: "This agreement does not impose any restrictions or
  obligations with respect to any use, modification, or sharing of any outputs
  generated by using the Model Materials" — transcripts produced by the model are
  explicitly unencumbered.
- **Patent/copyright litigation termination clause**: filing or voluntarily joining a
  patent- or copyright-infringement suit against anyone over the Model Materials
  terminates your rights under the license, unless it's a defensive counter-suit.
  Standard defensive-termination pattern (similar in spirit to Apache-2.0 §3's patent
  clause, though worded more broadly — it covers copyright suits too, not just
  patent).
- **No warranty, no liability**: standard "AS IS" disclaimer and liability waiver, and
  the user bears sole responsibility for clearing any third-party rights in the Model
  Materials (e.g., rights in training data) — the license does not itself warrant the
  model was trained on cleared data.
- **Use restrictions**: none found in the text (no acceptable-use policy, no
  commercial-use carve-out, no attribution-in-UI requirement beyond the
  license+notices retention above).
- **Not independently found**: whether SPDX has assigned an identifier for 1.1 (SPDX
  currently lists `OpenMDW-1.0`; this spike did not find an `OpenMDW-1.1` SPDX entry,
  only the license text itself and a Natolambert commentary piece
  ([substack](https://natolambert.substack.com/p/on-the-new-openmdw-open-source-ai))
  describing it as a new permissive AI-model license from the Linux Foundation).

## 5. Alternatives if Nemotron 3.5 is unavailable/unsuitable

| Model | Streaming | Spanish | License | Runtime |
|---|---|---|---|---|
| **Parakeet TDT 0.6B v3** (already integrated, `[parakeet]` extra) | Pseudo-streaming (chunked re-encoding of an offline transducer), not cache-aware | Yes (25 European langs incl. es) | **CC-BY-4.0** | sherpa-onnx |
| **Moonshine v2** (base, es) | Yes — genuinely streaming ("ergodic streaming encoder") | Yes — dedicated `sherpa-onnx-moonshine-base-es-quantized-2026-02-27` export | **MIT** (current-generation streaming models; older non-English non-streaming models were under a separate non-commercial Moonshine Community License — only relevant if picking an older/legacy export) | sherpa-onnx |
| **Nemotron Speech Streaming EN 0.6B** (earlier, English-only sibling) | Yes, cache-aware | No (English only) | OpenMDW-1.1 (same family) | sherpa-onnx |
| Qwen3-ASR / Cohere Transcribe / FunASR-Nano | No (offline/LLM-style decoders) | Qwen3-ASR and Cohere Transcribe both claim broad multilingual incl. Spanish | Varies (check per model) | sherpa-onnx |

For a CPU-only, Spanish-capable, genuinely-cache-aware streaming model with the most
permissive license of the bunch, **Moonshine v2 (MIT)** is the strongest fallback if
OpenMDW-1.1 turns out to be a blocker for packaging. It was not benchmarked in this
spike (out of scope/time budget) — WER and RTF numbers for the Spanish Moonshine
export are not yet gathered.

---

## Not done / open questions

- Did not benchmark the other four chunk sizes (80/160/320/1120 ms) — only 560 ms was
  run, per the ticket's ask. RTF/WER at other chunk sizes is NVIDIA's own FLEURS
  numbers (already in `notes/streaming.md`), not independently reproduced here.
- Did not compute an objective WER for the 30 s Spanish clip (no reference transcript
  was prepared); the run is a functional/performance smoke test, not an accuracy
  benchmark.
- Did not confirm the current status of `modified_beam_search`/hotwords support for
  `model_type="nemotron"` beyond finding the open issue and an ambiguous closed PR
  reference (#3895) — needs a direct source read before being relied on.
- Did not benchmark Moonshine v2 Spanish or confirm its exact WER/RTF — listed only as
  a license-driven fallback candidate.
- Did not determine whether a third-party ONNX re-export of a model counts as new
  "Model Materials" under OpenMDW-1.1's distribution clause, or whether sherpa-onnx's
  own re-export carries forward NVIDIA's original notices intact inside the
  `.tar.bz2` (this spike did not diff the HF original files against the sherpa-onnx
  export for notice preservation).

## Disk usage (for cleanup)

All spike artifacts live inside this worktree and are gitignored (see commit
`chore(spike): gitignore scratch venv and model cache dirs`):

- `.spike-venv/` — throwaway Python venv with `sherpa-onnx==1.13.8` — **≈137 MB**
- `.spike-models/` — one extracted model (560 ms int8, ≈682 MB) plus the LibriVox
  source mp3/wav (≈2.4 MB) — **≈654 MB**
- Total: **≈790 MB**, all under
  `/home/fede/REPOS/whisperAloud/.claude/worktrees/agent-ae78513396e9bdaa1/`

Safe to `rm -rf .spike-venv .spike-models` once this report has been reviewed; neither
directory is tracked by git.
