# WHISP-52 spike: Parakeet Ultra / Redux (Moondream)

Date: 2026-10-01. Spike only — no code is wired into the daemon. No inference was run;
nothing was installed outside this worktree. All evidence below comes from the Hugging
Face API/raw-file endpoints and public GitHub repos, fetched directly in this session.

## Decision-oriented summary

- **The model exists. It is not a hallucination.** `moondream/parakeet-ultra` and its
  sibling `moondream/parakeet-redux` are real, public, non-gated Hugging Face repos,
  created 2026-09-22 and 2026-09-18. The research note that flagged "possible
  hallucination" was being appropriately cautious, but the other note that cited the
  card directly was right.
- **It is a post-train of `nvidia/parakeet-tdt-0.6b-v3`**, confirmed by the card's own
  text ("A post-trained version of parakeet-tdt-0.6b-v3 ... Same architecture, same
  tokenizer, same 0.6B parameters") and independently by a third party
  (`FluidInference/parakeet-ultra-coreml`, which re-exports Ultra and calls it a
  "drop-in for the v3 component contract").
- **Format mismatch is the whole story**: Ultra/Redux ship only as **Hugging Face
  Transformers `safetensors` checkpoints** (`architectures: ["ParakeetForTDT"]`,
  `model_type: "parakeet_tdt"`), **not** as a NeMo `.nemo` file. The sherpa-onnx export
  recipe that produces the existing v3 ONNX files loads the model through
  `nemo.collections.asr.models.ASRModel` (the NeMo toolkit), which expects a `.nemo`
  checkpoint or NVIDIA's own `from_pretrained("nvidia/...")` path. There is no
  `.nemo` file anywhere in either Moondream repo, so **the v3 recipe does not run
  unchanged against Ultra/Redux** — someone would have to write a new exporter against
  the HF Transformers module tree instead.
- **No ONNX, sherpa-onnx, or GGUF/whisper.cpp export of Ultra or Redux exists anywhere**,
  official or third-party. I checked `csukuangfj/*`, `onnx-community/*`, `k2-fsa/*` on
  Hugging Face, and `ggml-org/parakeet-GGUF` plus whisper.cpp's release notes back to
  v1.9.0 — all of them stop at v3 (or older v2). The only third-party re-export of Ultra
  is `FluidInference/parakeet-ultra-coreml`, Apple Core ML only, no Linux x86 relevance.
- **Redux (the 178 MB ternary model) only runs in Moondream's own closed "Photon"
  engine** (Python package `moondream`, backed by a component the package calls
  "Kestrel"), which is PyTorch-based and not sherpa-onnx, onnx-asr, or whisper.cpp.
  WhisperAloud's stack has no PyTorch dependency today; pulling one in just to run a
  178 MB CPU model would be a heavy, out-of-character addition.
- **The headline numbers are genuine, self-reported, but independently corroborated for
  direction (not for the exact FLEURS-es figure).** FLEURS-es 2.72 and Open ASR
  Leaderboard English mean 5.80 both come straight from the Ultra model card and its
  machine-readable `.eval_results/open_asr_leaderboard.yaml` (mean_wer 5.32 across all
  8 sets incl. long-form; the card's "5.80" is explicitly the mean of 7 sets excluding
  long-form, stated in the YAML's own comment). No independent party has reproduced the
  exact FLEURS-es 2.72 figure. FluidInference's separate Core ML re-export does
  independently confirm the *qualitative* claim — Ultra beats v3 on every one of 24
  FLEURS languages in their own from-scratch measurement — which is good evidence
  against cherry-picking or fabrication, but it used a different subset (100
  samples/language) and a different runtime (Core ML on Apple Silicon), so it is not a
  reproduction of the 2.72 number itself.
- **Recommendation: (b), with a scoped, non-trivial effort estimate — do not do it now.**
  Don't add Ultra to the catalog today (no ready URL/runtime exists, option a is out).
  Don't drop it either (option c) — the model is real, the architecture is a strict
  superset of what the project already ships, and the WER gain is real and sizeable in
  Spanish. The right next step, if and when this becomes a priority, is a dedicated
  export spike (not this one) that writes a new ONNX exporter against HF Transformers'
  `ParakeetEncoder` / `ParakeetRNNTDecoder` / `ParakeetTDTJointNetwork` submodules,
  modeled on sherpa-onnx's three-file (encoder/decoder/joiner) contract but without
  NeMo. Estimate: **3–5 focused engineer-days** for someone already familiar with the
  sherpa-onnx Parakeet runtime contract (see §3 for the breakdown and risks).

---

## 1. Does the Hugging Face model card exist?

Yes, verified via the HF API (`GET /api/models/<repo>` and `/tree/main`), not just a
web search summary.

**`moondream/parakeet-ultra`**
([card](https://huggingface.co/moondream/parakeet-ultra),
[tree](https://huggingface.co/api/models/moondream/parakeet-ultra/tree/main))

| file | size |
|---|---|
| `README.md` | 6,433 B |
| `config.json` | 1,153 B |
| `model.safetensors` | 1,255,353,386 B (≈1.17 GiB) |
| `tokenizer.json` | 1,159,960 B |
| `.eval_results/open_asr_leaderboard.yaml` | (eval metadata, not weights) |

- License field: `cc-by-4.0` (from both `cardData.license` and the `license:cc-by-4.0`
  tag).
- Created: 2026-09-22T00:23:15Z. Last modified 2026-09-23T00:03:54Z.
- `config.json`: `"architectures": ["ParakeetForTDT"]`, `"model_type": "parakeet_tdt"`,
  24-layer conformer-style encoder (hidden 1024, 8 heads, subsampling factor 8,
  128 mel bins), 2-layer RNNT decoder (hidden 640), vocab size 8193 — i.e. a Transformer
  (TDT/RNN-T) encoder-decoder-joint, **not** the plain seq2seq/attention-decoder shape of
  Whisper. This matches `nvidia/parakeet-tdt-0.6b-v3`'s published architecture
  (Fast Conformer encoder + TDT decoder/joiner).
- Format: **`safetensors` (HF Transformers checkpoint)**. There is no `.nemo` file and
  no ONNX file in the repo. (See §3 for why this matters.)
- Base model: the card states plainly, "A post-trained version of
  [parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3). Same
  architecture, same tokenizer, same 0.6B parameters in full precision."

**`moondream/parakeet-redux`**
([card](https://huggingface.co/moondream/parakeet-redux),
[tree](https://huggingface.co/api/models/moondream/parakeet-redux/tree/main))

| file | size |
|---|---|
| `README.md` | 8,832 B |
| `config.json` | 12,988 B |
| `model.safetensors` | 177,774,490 B (≈169.5 MiB — this is the "178 MB" the notes cite) |
| `ternary.json` | 57,970 B (packing metadata for the 1.58-bit/ternary weights) |
| `tokenizer.json` | 1,159,960 B |

- License: `cc-by-4.0`. Created 2026-09-18T11:02:15Z.
- Same base model and architecture claim as Ultra ("A 1.58-bit version of
  parakeet-tdt-0.6b-v3 ... every encoder weight is -1, 0 or +1").
- Format: `safetensors` + a custom `ternary.json` sidecar — Moondream's own packed
  ternary-weight format, readable only by their "Photon" runtime (see §4). Not ONNX,
  not GGUF, not `.nemo`.

**Answer to "is it actually a post-train of nvidia/parakeet-tdt-0.6b-v3?"**: yes, by the
card's own text and architecture, and independently corroborated by
[`FluidInference/parakeet-ultra-coreml`](https://huggingface.co/FluidInference/parakeet-ultra-coreml)
("Core ML build of moondream/parakeet-ultra, a full-precision post-training of
`nvidia/parakeet-tdt-0.6b-v3`. Same architecture ... drop-in for the v3 component
contract.").

**Architecture/format**: HF Transformers-native `ParakeetForTDT` (conformer encoder +
RNN-T/TDT decoder+joint), shipped as `.safetensors`. **Not** `.nemo`, **not** ONNX.

## 2. Are the claimed numbers reproducible?

**Where the numbers come from**: directly from the model card itself, and from a
machine-readable `.eval_results/open_asr_leaderboard.yaml` file in the repo that is
clearly meant to feed the HF Open ASR Leaderboard Space. Full Ultra card tables (fetched
verbatim from `https://huggingface.co/moondream/parakeet-ultra/raw/main/README.md`):

| | parakeet-tdt-0.6b-v3 | parakeet-ultra |
|---|---|---|
| Open ASR Leaderboard, 7 English sets (WER %) | 6.26 | **5.80** |
| FLEURS, 25 languages (WER %) | 11.62 | **9.55** |
| Business speech, AA-WER style (WER %) | 6.15 | **5.79** |
| Background noise, 9 MUSAN conditions (WER %) | 6.72 | **5.82** |
| TED-LIUM long-form (WER %) | 2.71 | **1.94** |

FLEURS-es specifically: v3 **3.12** → Ultra **2.72** (matches the research note exactly).

The YAML (`.eval_results/open_asr_leaderboard.yaml`) carries a dataset-by-dataset
breakdown and a comment clarifying normalization: *"The leaderboard's TED-LIUM split is
no longer distributed; tedlium_wer scores the same eleven TED-LIUM 3 test talks in full
... and mean_wer averages all eight sets. The card's 'Open ASR' figure (5.80) is the
mean of the other seven."* mean_wer across all 8 (incl. long-form) is 5.32.

**Is this the card itself, a paper, or a third party?** The card itself and Moondream's
own eval pipeline — self-reported, first-party. There is no arXiv paper for Ultra/Redux
(unlike, say, Qwen3-ASR). I found no mention of Parakeet Ultra in the HF Open ASR
Leaderboard paper (arXiv 2510.06961, v4, dated 2026-03-30 — before Ultra existed) or in
the live leaderboard Space (not checked live; same gap the original research notes
flagged).

**Reproducibility**: partial. Moondream documents its methodology precisely enough to
be credible (states it reused "the Open ASR Leaderboard's own pipeline ... as of
September 2026", notes the exact TED-LIUM substitution, and reports both the original
v3 and Ultra numbers measured by the *same* harness rather than quoting NVIDIA's card
for v3 — that's good practice and explains small v3-number discrepancies vs. NVIDIA's
own card, e.g. 6.26 vs. 6.32–6.34 elsewhere).

The one independent reproduction I found is **not of the FLEURS-es number**: it's
`FluidInference/parakeet-ultra-coreml`'s own from-scratch Core ML benchmark (100
samples/language, 24 languages, M-series Mac, "corpus-level WER"), which found Ultra
mean FLEURS WER 11.67% vs v3's 14.81%, **winning all 24 languages** — qualitatively
consistent with Moondream's own 9.55% vs 11.62% (different subset/runtime so the
absolute numbers don't match, but the direction and rough magnitude do). That's a real
piece of independent, disinterested evidence that Ultra genuinely improves on v3; it is
not proof of the specific 2.72 FLEURS-es figure.

**Verdict**: the numbers are genuine self-reported first-party claims from a real,
existing, versioned model card, with a machine-readable eval-results file and
methodology notes, and the direction of improvement is independently corroborated by an
unrelated party (FluidInference) using a different export and different data subset.
They are not peer-reviewed, not leaderboard-verified (the live Open ASR Leaderboard
Space was not re-checked in this spike), and the exact FLEURS-es 2.72 figure has not
been reproduced by anyone outside Moondream.

## 3. ONNX / sherpa-onnx / whisper.cpp exports

**No ONNX or sherpa-onnx export of Ultra or Redux exists, upstream or third-party.**
Checked directly:

- `csukuangfj/*` on HF (the person who publishes essentially all of sherpa-onnx's model
  zoo): has exports for parakeet v2 and **v3**
  (`csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3[-int8|-fp16]`), Nemotron 3.5, Canary,
  GigaAM, etc. — but nothing matching "ultra" or "redux".
- `onnx-community/*` on HF: only `parakeet-tdt-0.6b-v2-ONNX` and `parakeet-ctc-0.6b-ONNX`.
  No v3, no Ultra.
- `k2-fsa` as an HF author: no models returned by the search API (k2-fsa publishes model
  binaries as GitHub release assets under `asr-models`, not as its own HF org).
- `ggml-org/parakeet-GGUF` (the repo whisper.cpp's `parakeet-cli` documentation points
  to): contains only `ggml-parakeet-tdt-0.6b-v3-{f32,f16,q8_0,q4_0,q4_k}.bin`. No Ultra,
  no Redux. whisper.cpp's own release notes (checked back through v1.9.0, the version
  that added Parakeet TDT support per the project's `report.md`) mention no Ultra/Redux
  support either.

**The sherpa-onnx v3 export recipe**, for reference
([`scripts/nemo/parakeet-tdt-0.6b-v3/export_onnx.py`](https://github.com/k2-fsa/sherpa-onnx/blob/master/scripts/nemo/parakeet-tdt-0.6b-v3/export_onnx.py)
in `k2-fsa/sherpa-onnx`, fetched verbatim):

1. Loads the model via `nemo.collections.asr.models.ASRModel.from_pretrained(
   model_name="nvidia/parakeet-tdt-0.6b-v3")` (or a local `.nemo` file) — i.e. it
   requires the **NeMo toolkit** and a **NeMo checkpoint**.
2. Writes `tokens.txt` from `asr_model.joint.vocabulary` and a `bpe.vocab` for hotword
   support.
3. Calls NeMo's own convenience exporters: `asr_model.encoder.export("encoder.onnx")`,
   `asr_model.decoder.export("decoder.onnx")`, `asr_model.joint.export("joiner.onnx")`.
4. Dynamically int8-quantizes all three with `onnxruntime.quantization.quantize_dynamic`
   (`QUInt8` for the encoder, `QInt8` for decoder/joiner).
5. Stamps ONNX metadata (`vocab_size`, `pred_rnn_layers`, `pred_hidden`,
   `subsampling_factor=8`, `model_type="EncDecRNNTBPEModel"`, etc.) that sherpa-onnx's
   C++ runtime reads at load time to configure the transducer decode loop.

**Does this recipe apply unchanged to Ultra?** No — **and this is the crux of the
spike**. The recipe's very first line, `nemo_asr.models.ASRModel.restore_from(...)` /
`.from_pretrained(...)`, needs a `.nemo` checkpoint. Ultra and Redux are published only
as HF Transformers `safetensors` checkpoints with `architectures: ["ParakeetForTDT"]` —
a **different, newer, parallel implementation of the same architecture that now lives in
mainline `transformers`** (confirmed by fetching
`transformers/models/parakeet/modeling_parakeet.py` from the `huggingface/transformers`
GitHub `main` branch: it defines `ParakeetEncoder`, `ParakeetRNNTDecoder`,
`ParakeetRNNTJointNetwork`/`ParakeetTDTJointNetwork`, and `ParakeetForTDT`, auto-generated
from `modular_parakeet.py` and shipped in a recent `transformers` release). There is no
`nemo_asr.models.ASRModel.from_pretrained("moondream/parakeet-ultra")` path — NeMo does
not know this repo exists, and there is no `.nemo` artifact to convert.

**Does it apply with modification?** Structurally, mostly yes — not in the "swap one
line" sense, but in the sense that the three-module shape (encoder / RNNT-or-TDT decoder
/ joint network) is the same contract sherpa-onnx expects, and HF Transformers'
`ParakeetEncoder.forward`, `ParakeetRNNTDecoder.forward`, and
`ParakeetTDTJointNetwork.forward` are the direct analogues of NeMo's
`asr_model.encoder`, `.decoder`, `.joint`. Someone would need to:

1. Load the checkpoint with `transformers.AutoModel` (or the specific `ParakeetForTDT`
   class) instead of NeMo.
2. Write fresh `torch.onnx.export()` calls against the three submodules directly (HF
   Transformers objects don't have NeMo's convenience `.export()` method), matching
   input/output tensor shapes and dynamic axes to what sherpa-onnx's C++ decode loop
   expects (fixed by sherpa-onnx's runtime code, documented informally by the existing
   v2/v3 export scripts' `test_onnx.py`).
3. Regenerate `tokens.txt` from `tokenizer.json` (format differs from NeMo's
   `vocabulary` list — needs a small conversion, not a blocker).
4. Re-derive and re-stamp the ONNX metadata block (`subsampling_factor`, `pred_hidden`,
   `pred_rnn_layers`, etc.) from the HF `config.json` fields instead of NeMo's
   `asr_model.cfg`.
5. Validate against sherpa-onnx's existing `OfflineRecognizer`/transducer decode path
   (ideally bit-identical greedy output to a reference `transformers` forward pass on a
   few test utterances) before trusting any WER number.
6. Re-run int8 dynamic quantization the same way (this part transfers unchanged).

None of this is exotic — it is "write ~150-300 lines against a documented, open
architecture using a library (`transformers`) WhisperAloud doesn't currently depend on
for ASR" — but it is also not "download a file," and nobody has published the result.
**Effort estimate for a competent engineer already familiar with both sherpa-onnx's
Parakeet runtime contract and the `transformers` Parakeet module: 3–5 focused days**,
split roughly: 1 day reading both implementations and matching tensor contracts, 1–2
days writing and debugging the export script against a small audio test set, 0.5–1 day
quantization + metadata + tokens.txt plumbing, 0.5–1 day validating WER parity against
the Moondream-reported numbers on a FLEURS-es subset. Main risk: if HF Transformers'
`ParakeetEncoder`/`ParakeetRNNTDecoder` use ops that don't trace/export cleanly to ONNX
(custom attention kernels, dynamic control flow in the TDT duration-prediction loop),
the estimate could grow; I did not attempt a trial export in this spike (out of scope —
no inference, no local export, per the brief), so this risk is unverified either way.

## 4. Does the ternary "Redux" model run in any runtime available to us?

**No.** Redux's own README is explicit about this. Its "Performance" section frames
`parakeet.cpp` (ggml), `sherpa-onnx`, `onnx-asr`, and `parakeet-mlx` as **"the fastest
other ways we found to run Parakeet"** and benchmarks them *against* Redux as separate,
unrelated incumbents running the **original v3 weights** (int8 ONNX, 0.67 GB) — not
against Redux itself, because none of them can load Redux's ternary format. Redux's
usage section only shows:

```python
import moondream as md
with md.photon("moondream/parakeet-redux", device="cpu") as speech:
    ...
```

`moondream`'s PyPI package (`moondream` 2.6.1, inspected via the PyPI JSON API and the
wheel's file listing — not installed or executed) is a thin Python client: its
`photon_client.py` is explicitly "Local Photon model client backed by Kestrel" and
imports `torch` to pick a device (CUDA → MPS → CPU). **Photon/Kestrel is Moondream's own
closed inference engine**, not sherpa-onnx, not ONNX Runtime, not whisper.cpp, and it
requires PyTorch — a dependency WhisperAloud's engine stack (faster-whisper/ctranslate2,
sherpa-onnx) does not currently carry.

The ternary `model.safetensors` + `ternary.json` pair is also not a documented or
standard HF format (no generic `transformers` loader for "ternary parakeet" exists in
mainline `transformers` as of this check) — reading it requires Photon's proprietary
unpacking code.

**Conclusion**: Redux runs today only through Moondream's own closed, PyTorch-based
engine, which is not available in, and is architecturally foreign to, WhisperAloud's
runtime stack. There is no path to running it in sherpa-onnx, onnx-asr, whisper.cpp, or
faster-whisper without Moondream (or someone else) first publishing a decoder for the
ternary format in one of those runtimes — which, per the README's own comparison table,
has not happened.

## 5. Recommendation

**(b) — produce our own export, but not now; scope it as a separate, dedicated ticket.**

- **(a) Add to catalog now** is not available: there is no ready ONNX/sherpa-onnx/GGUF
  URL anywhere for Ultra or Redux. The catalog entry would have nothing to point at.
- **(c) Drop it** is too strong: the model is real, verified from first principles (HF
  API, not a web summary), is architecturally a strict continuation of the model
  WhisperAloud already ships (same sherpa-onnx-compatible encoder/decoder/joint shape,
  same tokenizer, same CC-BY-4.0 license — no new legal review needed), and the WER
  improvement is real (−0.4pp FLEURS-es, independently corroborated in direction by
  FluidInference's unrelated Core ML re-export). Dropping it means re-discovering the
  same opportunity later from scratch.
- **(b) Our own export** is the right shape of work, but it is a genuine, multi-day
  engineering effort — not a quick registration — because Ultra/Redux ship in HF
  Transformers' new native `ParakeetForTDT` format, not NeMo's `.nemo`, so the existing
  sherpa-onnx `export_onnx.py` cannot be pointed at it; a new exporter must be written
  against the `transformers` module tree. Suggested framing for a follow-up ticket: a
  bounded spike (not full production integration) that (1) writes the ONNX export
  script against HF Transformers' `ParakeetEncoder`/`ParakeetRNNTDecoder`/
  `ParakeetTDTJointNetwork`, (2) validates it decodes correctly against a handful of
  known-transcript utterances, (3) runs it through the same FLEURS-es + rioplatense
  benchmark the project already uses for Parakeet v3, and (4) only then decides whether
  to promote it to the model catalog. Estimated effort: **3–5 engineer-days**, with the
  dominant risk being whether the TDT duration-prediction decode loop traces cleanly to
  ONNX (unverified in this spike — no local export was attempted, per the brief's "no
  inference" constraint).

## Sources

- [`moondream/parakeet-ultra`](https://huggingface.co/moondream/parakeet-ultra) — card,
  `config.json`, file tree, `.eval_results/open_asr_leaderboard.yaml` (all fetched via
  the HF API/raw endpoints in this session)
- [`moondream/parakeet-redux`](https://huggingface.co/moondream/parakeet-redux) — card,
  file tree (same method)
- [`nvidia/parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) —
  base model
- [`FluidInference/parakeet-ultra-coreml`](https://huggingface.co/FluidInference/parakeet-ultra-coreml) —
  independent third-party re-export and benchmark
- [`csukuangfj` HF models](https://huggingface.co/csukuangfj) — sherpa-onnx model zoo
  author; searched for "parakeet"
- [`onnx-community` HF models](https://huggingface.co/onnx-community) — searched for
  "parakeet"
- [`ggml-org/parakeet-GGUF`](https://huggingface.co/ggml-org/parakeet-GGUF) — whisper.cpp
  GGUF conversions (v3 only)
- [`k2-fsa/sherpa-onnx` export script for v3](https://github.com/k2-fsa/sherpa-onnx/blob/master/scripts/nemo/parakeet-tdt-0.6b-v3/export_onnx.py)
  and [its `run.sh`](https://github.com/k2-fsa/sherpa-onnx/tree/master/scripts/nemo/parakeet-tdt-0.6b-v3)
- [`huggingface/transformers` Parakeet modeling code](https://github.com/huggingface/transformers/blob/main/src/transformers/models/parakeet/modeling_parakeet.py) —
  confirms the native HF Transformers `ParakeetForTDT` implementation exists and its
  module shape
- [`moondream` PyPI package](https://pypi.org/pypi/moondream/json) and its wheel's
  `photon_client.py` — confirms Photon/Kestrel is a closed, PyTorch-backed engine
- Project's own prior research:
  [`docs/research/2026-10-01-sota-stt-offline/notes/models.md`](../../research/2026-10-01-sota-stt-offline/notes/models.md),
  [`docs/research/2026-10-01-sota-stt-offline/report.md`](../../research/2026-10-01-sota-stt-offline/report.md)
