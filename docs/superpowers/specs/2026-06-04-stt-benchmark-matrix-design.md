# STT Benchmark Matrix — Design Spec

- **Lattice:** WHISP-41 (subtask of WHISP-32; related to WHISP-35/36/38)
- **Date:** 2026-06-04
- **Status:** approved design, pre-implementation
- **Origin:** Strategic detour from the WHISP-35 engine refactor, requested to give the
  benchmark standalone reference value. Builds on the `bench/` prototype from WHISP-33.

## 1. Purpose & ambition

A **reproducible, publishable STT performance matrix** tracking combinations of
`language × model × engine × hardware`. Two goals, in priority order:

1. **Decision support** for WhisperAloud's engine choices (WHISP-35/36/37/38) — every
   "use engine X by default" claim is backed by locally-measured numbers, not vendor PR.
2. **Honest community reference** for Linux STT performance across consumer and server hardware.

Ambition level (decided): **internal-publishable harness** — well-structured, reproducible,
published as an honest reference. *Not* a community leaderboard with third-party result
submission or a versioned-dataset contribution model. The ledger schema is designed so it
*could* grow into that later, but that overhead is explicitly out of scope now.

### Metrics tracked
WER, RTF (real-time factor = proc_s / audio_s), peak RAM, peak VRAM, model size on disk,
load time. Every measurement carries full hardware + version provenance.

### Non-goals (YAGNI)
- No leaderboard UI, no result-submission API, no auth.
- No streaming/partial-latency measurement here (that is WHISP-34/37; this is one-shot WER + RTF).
- No daemon/config integration (that is WHISP-35/36 proper — see §3).

## 2. Architecture overview

Two cooperating pieces:

- **`src/whisper_aloud/stt/`** — a new package defining the shared `STTEngine` interface and
  the *serious* engine adapters (faster-whisper, Parakeet). Production will consume these in
  WHISP-35/36. The benchmark consumes the same code → the detour advances the refactor instead
  of duplicating it.
- **`bench/`** — the benchmark harness: datasets, hardware probe, runner CLI, append-only
  results ledger, and a generated markdown matrix. Bench engine adapters wrap the `src/` engines
  via a registry, with an **escape hatch** (`bench/engines/experimental/`) for exploratory
  engines/hardware that do not yet belong in `src/`.

### Scope boundary with WHISP-35/36 (important)
This work delivers **the `STTEngine` interface + faster-whisper & Parakeet adapters** in `src/`.
It does **not** rewire `daemon.py`/`transcriber.py` to use them, nor add the config/UI engine
selector — those remain WHISP-35 (daemon integration) and WHISP-36 (Parakeet in production).
The existing `transcriber.py` keeps working unchanged; the new package sits alongside it until
WHISP-35 switches the daemon over.

## 3. Directory layout

```
src/whisper_aloud/stt/
  __init__.py
  base.py                  # STTEngine (ABC) + Transcript + EngineCapabilities dataclasses
  faster_whisper_engine.py # wraps current faster-whisper/CTranslate2 logic
  parakeet_engine.py       # sherpa-onnx Parakeet TDT transducer

bench/
  README.md                # methodology, run instructions, how to read MATRIX.md, per-hw setup
  requirements-bench.txt    # bench-only deps: jiwer, datasets, sherpa-onnx, psutil (already), ...
  datasets/
    fleurs.py              # fetch + normalize FLEURS es_419 / en_us -> WAV 16k mono + manifest
    manifests/
      es.jsonl             # committed references: {file, duration_s, ref}
      en.jsonl
  engines/
    __init__.py            # ENGINE_REGISTRY: name -> factory; resolves src/ engines + bench-local + experimental
    whispercpp.py          # bench-local adapter wrapping the whisper.cpp CLI binary (CPU + Vulkan).
                           #   Lives here (not src/) until promoted to a src/ STTEngine in WHISP-38.
    experimental/
      __init__.py          # escape hatch; e.g. coral_tpu.py later (NOT imported by src/)
  hwprobe.py               # auto-capture hardware provenance (see §5)
  runner.py                # CLI: probe hw, run engine×model×lang, append rows to ledger
  report.py                # ledger.jsonl -> MATRIX.md
  results/
    ledger.jsonl           # append-only; one row per (engine,model,lang,device,hw) run
  MATRIX.md                # generated published matrix (committed)
  scripts/
    run_remote.sh          # rsync bench/ + manifests to inference01, run, pull back ledger rows
  .gitignore               # models + audio (regenerable); ledger & manifests are committed
```

`bench/RESULTS_es_cpu.md` and the prototype `run_bench.py`/`fetch_audio.py` from WHISP-33 are
**superseded** by this structure; their numbers are migrated into the ledger as the first rows
(provenance: laptop CPU) and the prototype scripts are removed.

## 4. `STTEngine` interface (`src/whisper_aloud/stt/base.py`)

```python
@dataclass
class Transcript:
    text: str
    segments: list | None = None          # optional, engine-dependent

@dataclass
class EngineCapabilities:
    streaming: bool
    languages: list[str] | str            # explicit list or "multilingual"
    native_punctuation: bool
    devices: list[str]                    # subset of {cpu, cuda, vulkan, openvino, coral}

class STTEngine(ABC):
    name: str                              # e.g. "faster-whisper", "parakeet"
    @abstractmethod
    def load(self, model: str, device: str, compute_type: str) -> None: ...
    @abstractmethod
    def transcribe_numpy(self, samples: np.ndarray, sample_rate: int,
                         language: str | None = None) -> Transcript: ...
    @property
    @abstractmethod
    def capabilities(self) -> EngineCapabilities: ...
    def unload(self) -> None: ...          # default no-op; frees model
```

Design intent: this is the contract the daemon will depend on in WHISP-35. Keep it minimal —
`transcribe_numpy` is the one method the recording pipeline needs. `transcribe_file` is a thin
helper that reads a WAV and calls `transcribe_numpy` (lives in base, not abstract).

**Which engines implement it where:** `src/` ships the two production-bound adapters now,
`faster-whisper` and `parakeet`. **whisper.cpp** is a *bench-local* adapter for iteration 1
(`bench/engines/whispercpp.py`, wrapping the `whisper-cli` binary) — it is promoted to a `src/`
`STTEngine` in WHISP-38 once it earns production status. The benchmark registry treats all three
uniformly regardless of where the adapter lives.

## 5. Hardware provenance (`bench/hwprobe.py`)

Auto-captured so every ledger row is self-describing and comparable across machines. No manual
entry. Best-effort per field (missing tools → null, never crash):

- **CPU:** model from `/proc/cpuinfo`, logical cores, total RAM.
- **NVIDIA GPU:** `nvidia-smi --query-gpu=name,memory.total` → name + VRAM.
- **Intel/AMD GPU:** `lspci | grep VGA` and/or `vainfo` for iGPU identification.
- **OS/kernel:** distro + `uname -r`.
- **Stable `hw.id`:** short human label (`i7-1360P`, `inference01`) — defaults to hostname,
  overridable via `--hw-id` so remote runs are labeled clearly.

## 6. Ledger schema (`bench/results/ledger.jsonl`)

One JSON object per line, append-only. `ts`/`git_sha` stamped by the runner after the run.

```json
{
  "ts": "2026-06-04T19:00:00Z",
  "git_sha": "abc1234",
  "bench_version": 1,
  "lang": "es",
  "dataset": "fleurs/es_419",
  "n_clips": 40,
  "engine": "parakeet",
  "model": "tdt-0.6b-v3",
  "compute_type": "int8",
  "device": "cuda",
  "hw": {"id":"inference01","kind":"gpu","gpu":"<name>","vram_gb":24,
         "cpu":"<model>","cores":32,"ram_gb":128,"os":"<distro/kernel>"},
  "metrics": {"wer":0.034,"rtf":0.012,"load_s":1.9,
              "peak_rss_mb":724,"peak_vram_mb":1850,"model_size_mb":671},
  "notes": ""
}
```

- `device ∈ {cpu, cuda, vulkan, openvino, coral}`.
- Merging results from another machine = concatenating its ledger lines (git merge of JSONL).
- VRAM: `torch.cuda.max_memory_allocated` when CUDA is the engine's backend; else `nvidia-smi`
  sampling around the run; null on CPU/non-NVIDIA.

## 7. Runner CLI (`bench/runner.py`)

```
python -m bench.runner \
  --lang es,en \
  --engines parakeet:tdt-0.6b-v3,faster-whisper:small,faster-whisper:medium \
  --device auto \
  [--reps 3] [--hw-id inference01]
```

- `--device auto` → each engine runs on its best available device per its capabilities + hwprobe.
- For each (engine, model, lang): WER over all clips (deterministic), RTF = **median of `--reps`**
  (default 3) timed runs, peak RAM/VRAM captured. Append one ledger row per combination.
- Skips combinations whose model files are absent, logging a clear "missing model: <download cmd>".

`python -m bench.report` regenerates `MATRIX.md` from the ledger (pure function of the ledger;
safe to re-run).

## 8. Iteration 1 — what we actually run

| Axis | Hardware | Engines | Path |
|---|---|---|---|
| CPU consumo | i7-1360P (this laptop) | fw small/medium, Parakeet v3 | already working (WHISP-33) |
| Intel iGPU | Iris Xe (this laptop) | whisper.cpp Vulkan | build whisper.cpp w/ Vulkan |
| GPU NVIDIA | inference01 (SSH) | fw small/medium, Parakeet v3 | run_remote.sh |

FLEURS **es** and **en**, ~40 clips/language (dictation-range duration 4–30 s).
Intel discrete / AMD ROCm / Coral TPU: schema-ready, documented in README, **not measured** in it.1.

## 9. Remote execution (`bench/scripts/run_remote.sh`)

rsync `bench/` (code + manifests, not audio/models) to `inference01`, set up/reuse a venv there,
download models on the remote, run the suite with `--hw-id inference01`, then pull the new ledger
rows back and append to the local `results/ledger.jsonl`. Honest constraints documented in README:
remote needs CUDA-enabled ctranslate2 + `sherpa-onnx` GPU build; first run downloads models there.
(Decided: rsync+venv over Docker — lighter for a benchmark; containerization deferred.)

## 10. Testing

- `tests/bench/test_ledger.py` — append/read roundtrip; schema keys present.
- `tests/bench/test_report.py` — MATRIX.md generated from a fixture ledger matches expected table.
- `tests/bench/test_hwprobe.py` — parse fixture `/proc/cpuinfo` and `nvidia-smi` strings → correct hw dict.
- `tests/stt/test_engines.py` — STTEngine adapters: capabilities are well-formed; smoke
  `transcribe_numpy` on a 1 s synthetic clip, **skipped if model files absent** (CI-safe).
- WER normalization (jiwer compose) unit-tested for known ref/hyp pairs.

## 11. Reproducibility & published artifact

- `MATRIX.md` committed (generated, with provenance footnotes + methodology caveats).
- Audio gitignored, **manifests committed**; `requirements-bench.txt` pins bench deps.
- `README.md` states caveats explicitly: FLEURS is read-speech (absolute WER optimistic vs real
  mic dictation; relative ordering reliable), small N, nothing vendor-reported, GPU axis machine-specific.
- Single documented command per hardware axis to reproduce.

## 12. Open risks / follow-ups (not blocking it.1)

- whisper.cpp Vulkan build on Iris Xe may need driver/Mesa work → if it stalls, log it and ship
  CPU+NVIDIA, leave Intel iGPU as a follow-up row.
- inference01 GPU/driver/CUDA versions unknown until first SSH run → run_remote.sh must fail loudly
  with diagnostics, not silently produce CPU numbers labeled as GPU.
- Larger N + real mic-dictation set: deferred (would strengthen product decisions; WHISP-33 noted it).
- OpenVINO path for Intel: second iteration after Vulkan.
