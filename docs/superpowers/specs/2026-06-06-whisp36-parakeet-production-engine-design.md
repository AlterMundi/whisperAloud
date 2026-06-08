# WHISP-36 — Parakeet as a Selectable Production Engine

- **Lattice:** WHISP-36 (subtask of WHISP-32; unblocked by WHISP-35)
- **Date:** 2026-06-06
- **Status:** approved design, pre-implementation
- **Builds on:** WHISP-35 (the `SpeechEngine` Protocol + `create_engine` factory + `ModelConfig.engine` field already exist and the daemon constructs its engine via the factory).

## 1. Purpose

Make **NVIDIA Parakeet TDT 0.6b v3** usable as a production speech engine, selectable
via config (`engine = "parakeet"`). The locally-measured benchmark (WHISP-41) is the
justification: on CPU int8, Parakeet scores **3.2% WER on Spanish** (vs faster-whisper
medium's 5.3%) with native punctuation/casing, at a faster RTF (0.15 vs 0.49). It loses
on English (10.5%), so engine choice is the user's — this task makes the choice *possible*,
not automatic.

### Scope (decided)
Production Parakeet `SpeechEngine` adapter + model download/management + engine-aware
config validation + the daemon reload-condition fix. Selectable via `config.json`,
`WHISPER_ALOUD_MODEL_ENGINE`, and the existing CLI. **No Settings-UI selector** (deferred
to a follow-up task).

### Non-goals (YAGNI)
- No GTK4 Settings engine selector (follow-up task).
- No auto-per-language engine switching (decided: a single manually-configured engine).
- No whisper.cpp engine (that is WHISP-38).
- No mid-decode cancellation for Parakeet (see §3.4 — best-effort pre-decode only).
- No Parakeet model *selection* beyond the one canonical bundle (one model that matters today).

## 2. Engine-aware config (`src/whisper_aloud/config.py`)

`validate()` becomes **engine-conditional** for `model.name`:

- `engine == "faster-whisper"` → `model.name` ∈ the existing whisper list
  (`tiny`/`base`/`small`/`medium`/`large-v*`/`turbo`/…). Unchanged behavior.
- `engine == "parakeet"` → `model.name` ∈ `{"parakeet-tdt-0.6b-v3"}` (a short canonical
  id; int8 is implied by the bundle).
- An invalid `(engine, name)` pair raises `ConfigurationError` naming the valid models
  **for that engine** (e.g. `"Invalid model 'base' for engine 'parakeet'. Valid: parakeet-tdt-0.6b-v3"`).

`valid_engines` in `validate()` grows to `["faster-whisper", "parakeet"]`.

`compute_type` stays whisper-specific. For `parakeet` it is **accepted but ignored**
(the int8 quantization is fixed in the model bundle); validation does not reject any
existing compute_type value when engine is parakeet. Documented in a code comment.

**No default-swap magic:** choosing `parakeet` requires the user to also set a valid
parakeet `model.name`. The error message guides them. (The download CLI and docs use the
canonical name, so the path is obvious.)

Helper for reuse (both validate() and the engine/download code need it): a module-level
mapping `ENGINE_MODELS: dict[str, list[str]]` =
`{"faster-whisper": [...existing...], "parakeet": ["parakeet-tdt-0.6b-v3"]}`. `validate()`
selects the list by `self.model.engine`.

## 3. Production `ParakeetSpeechEngine` (`src/whisper_aloud/stt/parakeet_speech_engine.py`, new)

Implements the WHISP-35 `SpeechEngine` Protocol. **Distinct from** the benchmark-side
`ParakeetEngine(STTEngine)` in `stt/parakeet_engine.py` (different surface: that one is
`load()/capabilities`→`Transcript` for the bench harness). The module docstring states this
distinction explicitly. The two may share the sherpa-onnx encoder/decoder/joiner/tokens
glob+load logic via a small private helper, but the production class returns the rich
`TranscriptionResult` and carries daemon lifecycle state.

Named `ParakeetSpeechEngine` (NOT `ParakeetEngine`) precisely to avoid a class-name
collision with the bench `ParakeetEngine` in `parakeet_engine.py`.

```python
class ParakeetSpeechEngine:  # structurally satisfies SpeechEngine
    def __init__(self, config: WhisperAloudConfig) -> None: ...
    @property
    def is_loaded(self) -> bool: ...
    @property
    def is_cancelling(self) -> bool: ...
    def load_model(self) -> None: ...
    def unload_model(self) -> None: ...
    def cancel_transcription(self) -> None: ...
    def transcribe_numpy(self, audio, sample_rate=16000, **kwargs) -> TranscriptionResult: ...
    def transcribe_file(self, audio_path, **kwargs) -> TranscriptionResult: ...
```

### 3.1 `load_model()`
- Resolve the model directory via the §4 resolver for `config.model.name`
  (`parakeet-tdt-0.6b-v3` → `~/.cache/whisper_aloud/models/parakeet-tdt-0.6b-v3/`),
  **downloading it if absent** (lazy fallback, §4), logging a clear line.
- Build `sherpa_onnx.OfflineRecognizer.from_transducer(encoder, decoder, joiner, tokens,
  num_threads=os.cpu_count() or 4, model_type="nemo_transducer",
  provider="cuda" if config.model.device == "cuda" else "cpu")` (encoder/decoder/joiner
  resolved by glob, tokens=`tokens.txt`, mirroring the bench adapter).
- **CUDA→CPU fallback:** if `device == "cuda"` and recognizer construction raises (no GPU
  sherpa-onnx build / no CUDA), log a warning and retry with `provider="cpu"` — mirroring
  `Transcriber.load_model()`'s fallback. Sets an internal `device_in_use` for logging.
- Idempotent: a second call when already loaded is a no-op.

### 3.2 `transcribe_numpy(audio, sample_rate=16000, **kwargs) -> TranscriptionResult`
- If not loaded, `load_model()` first (matches Transcriber's lazy behavior).
- If `is_cancelling`, return early (see §3.4).
- Resample to 16 kHz if `sample_rate != 16000` (scipy.signal.resample, as the bench adapter).
- `stream = rec.create_stream(); stream.accept_waveform(16000, audio); rec.decode_stream(stream)`.
- Build `TranscriptionResult(text=stream.result.text.strip(), language=config.transcription.language,
  segments=[], confidence=0.0, duration=len(audio)/16000, processing_time=<measured>)`.
  - `segments=[]` and `confidence=0.0` are honest placeholders: sherpa-onnx's offline
    transducer result does not expose per-segment dicts or a calibrated confidence in the
    shape the rest of the app uses. Documented in a comment. `text` carries Parakeet's
    native punctuation/casing. The daemon consumes `.text`, `.language`, `.duration` — all real.

### 3.3 `transcribe_file(audio_path, **kwargs)`
- Read the WAV (soundfile) to a float32 numpy array + sample rate, delegate to
  `transcribe_numpy`. Mirrors the existing Transcriber helper.

### 3.4 Lifecycle + cancellation
- `is_loaded` → recognizer is not None. `unload_model()` drops the recognizer reference
  (frees memory); `is_loaded` then False.
- `cancel_transcription()` sets `_cancelling = True`; `is_cancelling` returns it.
  **Best-effort only:** it is checked *before* `decode_stream` begins. sherpa-onnx offline
  decoding is a single blocking call (a dictation clip decodes at RTF ~0.15), so there is no
  mid-decode interruption. The flag is reset at the start of each `transcribe_numpy`.
  **Documented limitation** — full streaming cancellation is WHISP-37 territory.

## 4. Model download (`src/whisper_aloud/stt/model_download.py`, new)

- `MODEL_SOURCES: dict[str, str]` maps a canonical name → sherpa-onnx GitHub release tarball:
  `"parakeet-tdt-0.6b-v3"` → `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2`.
- `model_dir(name) -> Path`: `~/.cache/whisper_aloud/models/<name>/` (honors `$XDG_CACHE_HOME`).
- `ensure_model(name) -> Path`: if the dir exists and contains `tokens.txt` + an `encoder*.onnx`,
  return it (idempotent skip). Otherwise download the tarball to a temp file, extract into the
  model dir (flattening the single top-level dir the sherpa tarball ships), verify the expected
  files, and return the dir. Raises a clear error on unknown name / download failure / missing
  files post-extract. Streams the download with a simple progress log (no extra deps — `urllib`).
- The Parakeet engine's `load_model()` calls `ensure_model(name)` (lazy fallback).

### 4.1 CLI: `whisper-aloud download-model <name>`
- New subparser in `__main__.py` `build_parser()`: `sub.add_parser("download-model", ...)`
  with a positional `name`. A handler `cmd_download_model(args)` calls `ensure_model(args.name)`,
  prints the resolved path on success or a clear error (and lists known names if the name is
  unknown). Registered in `COMMAND_MAP` alongside the existing commands. This path does NOT
  need the daemon running (pure local download).

## 5. Daemon reload-condition fix (`src/whisper_aloud/service/daemon.py`)

`_apply_config_changes` currently reloads the model only when `model.name` or `model.device`
change:

```python
if (new_config.model.name != self.config.model.name or
        new_config.model.device != self.config.model.device):
```

This misses `engine` (and `compute_type`) — so switching to Parakeet at runtime via a config
reload would silently no-op. Replace the condition with the authoritative change detector
(which already knows `MODEL_RELOAD_FIELDS = {engine, name, device, compute_type}`):

```python
from ..config import detect_config_changes  # if not already imported
...
if detect_config_changes(self.config, new_config).requires_model_reload:
    logger.info("Model config changed, reloading...")
    self.transcriber = create_engine(new_config)
    self.transcriber.load_model()
    self._model_loaded = True
```

Behavior for name/device changes is unchanged (they're in MODEL_RELOAD_FIELDS); engine and
compute_type changes now also trigger a reload, which is the point.

## 6. Factory wiring (`src/whisper_aloud/stt/engine.py`)

Extend `create_engine`:

```python
    if engine == "faster-whisper":
        from ..transcriber import Transcriber
        return Transcriber(config)
    if engine == "parakeet":
        from .parakeet_speech_engine import ParakeetSpeechEngine
        return ParakeetSpeechEngine(config)
    # defensive backstop: config.validate() rejects unknown engines upstream
    from ..exceptions import ConfigurationError
    raise ConfigurationError(f"Unknown STT engine '{engine}'")
```

Lazy import keeps sherpa-onnx out of the import path unless Parakeet is actually selected.

## 7. Data flow (unchanged from the daemon's view)
Recording → `self.transcriber.transcribe_numpy(audio)` → `TranscriptionResult` → history +
D-Bus `TranscriptionReady`. The daemon doesn't know or care which engine produced the result.

## 8. Error handling
- Unknown engine: rejected at `validate()`; `create_engine` backstop.
- Invalid `(engine, model.name)`: rejected at `validate()` with an engine-specific message.
- Missing model at load: lazy download; if download fails, `load_model()` raises with the
  download error (and the CLI command is suggested in the message).
- CUDA unavailable for Parakeet: warning + CPU fallback (no crash).
- `sherpa_onnx` not installed: `load_model()` raises a clear ImportError-derived message
  telling the user to install the Parakeet engine extra/dependency. (sherpa-onnx is an
  optional dependency — faster-whisper users don't need it.)

## 9. Testing
- **Config** (`tests/test_config.py`): `(parakeet, parakeet-tdt-0.6b-v3)` validates;
  `(parakeet, base)` raises ConfigurationError matching "for engine 'parakeet'";
  `(faster-whisper, parakeet-tdt-0.6b-v3)` raises; existing whisper validation unchanged;
  `parakeet` accepted in valid_engines.
- **model_download** (`tests/stt/test_model_download.py`): `model_dir` honors XDG_CACHE_HOME;
  `ensure_model` returns existing dir without downloading when `tokens.txt`+`encoder*.onnx`
  present (idempotent — patch the downloader and assert it is NOT called); unknown name raises
  with the known-names list; the actual network fetch is mocked (no real download in tests).
- **ParakeetSpeechEngine** (`tests/stt/test_parakeet_speech_engine.py`): `isinstance(engine, SpeechEngine)`
  conformance; `transcribe_numpy` on a 1 s synthetic clip returns a well-formed
  `TranscriptionResult` (text str, language set, duration≈1.0, segments==[], confidence==0.0) —
  **skipped if sherpa_onnx or the model is absent** (CI-safe); `cancel_transcription` sets
  `is_cancelling` and a pre-decode call returns early.
- **Factory** (`tests/stt/test_engine_factory.py`): `create_engine` with `engine=parakeet`
  returns a `ParakeetSpeechEngine` that is a `SpeechEngine`. (faster-whisper case unchanged.) This
  test must not require sherpa_onnx for collection — guard the import or skip if absent.
- **Daemon reload** (`tests/test_config_reload_integration.py` or `tests/test_daemon.py`):
  changing `engine` triggers a model reload via `_apply_config_changes` (assert `create_engine`
  re-called) — the §5 fix; a no-op change does not reload. Mock `create_engine`/engine so no
  real model loads.

## 10. Acceptance criteria
- [ ] `engine="parakeet"` is valid; `model.name` validation is engine-conditional with clear errors.
- [ ] `ParakeetSpeechEngine` implements `SpeechEngine` (runtime_checkable isinstance test passes).
- [ ] `create_engine` returns a `ParakeetSpeechEngine` for `engine=parakeet`.
- [ ] `whisper-aloud download-model parakeet-tdt-0.6b-v3` fetches+extracts the model; lazy
      download works on first `load_model()`; both idempotent.
- [ ] Daemon reloads the model when `engine` changes at runtime (reload-condition fix).
- [ ] sherpa-onnx is an optional dependency: nothing in the faster-whisper path imports it.
- [ ] All pre-existing tests still pass (faster-whisper behavior unchanged).

## 11. Dependencies / packaging
- `sherpa-onnx` becomes an **optional** runtime dependency (extra, e.g. `pip install
  whisper-aloud[parakeet]`). The faster-whisper default path must not import it. Document the
  extra in the README and the Parakeet error message. (The Debian `whisper-aloud-engine-*`
  split packaging already anticipates per-engine vendoring; full Debian packaging of the
  Parakeet engine is out of scope here.)

## 12. Risks / follow-ups
- **Cancellation** is best-effort (pre-decode) for Parakeet — accepted; full cancel needs
  streaming (WHISP-37).
- **`confidence`/`segments`** are placeholders for Parakeet; if history/UI starts relying on
  them, revisit. (Today the daemon uses text/language/duration.)
- **CUDA Parakeet** requires a GPU sherpa-onnx build; CPU is the reliable path. The benchmark
  Spanish win (3.2%) is on CPU, so CPU is fully viable for the headline use case.
- **Settings-UI selector** is the natural next follow-up so non-CLI users can switch engines.
- **`compute_type` ignored for Parakeet** — accepted; revisit if multiple Parakeet quantizations
  ship.
