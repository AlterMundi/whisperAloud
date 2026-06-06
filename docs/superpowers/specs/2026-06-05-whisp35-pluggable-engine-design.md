# WHISP-35 — Pluggable Production Speech Engine (regression-safe refactor)

- **Lattice:** WHISP-35 (subtask of WHISP-32; unblocks WHISP-36/37/38 in production)
- **Date:** 2026-06-05
- **Status:** approved design, pre-implementation
- **Builds on:** WHISP-41 (the `src/whisper_aloud/stt/` package with the bench-level
  `STTEngine` interface already exists; this adds the production contract).

## 1. Purpose

Make the daemon depend on a **`SpeechEngine` protocol via a factory** instead of
constructing `Transcriber` directly, so that WHISP-36 can add Parakeet as a selectable
production engine. This task is a **pure refactor: behavior must be identical** —
faster-whisper remains the only engine and the daemon's observable behavior does not change.

**Decomposition note:** This is the first of two. WHISP-35 (here) wires the daemon to a
factory with faster-whisper only. WHISP-36 (separate) adds the Parakeet engine, model
download, and the Settings UI selector. Splitting keeps this change regression-safe.

### Non-goals (YAGNI)
- No Parakeet engine, no model download, no UI selector (all WHISP-36).
- No change to `Transcriber` internals, `TranscriptionResult`, or the daemon's logic.
- No unification with the bench-level `STTEngine` (that interface stays separate; a future
  convergence is possible but out of scope).

## 2. Key fact driving the design

`Transcriber` (src/whisper_aloud/transcriber.py) **already implements everything the daemon
needs**: `load_model()` (with CUDA→CPU fallback), `unload_model()`, `is_loaded`,
`cancel_transcription()`, `is_cancelling`, and `transcribe_numpy()/transcribe_file()`
returning a rich `TranscriptionResult` (text, language, segments, confidence, duration,
processing_time). The daemon consumes `.text`, `.language`, `.duration`, and passes the
whole result to `history_manager.add_transcription`.

Therefore the production contract is "what `Transcriber` already exposes." We declare it as
a `Protocol`; `Transcriber` satisfies it structurally with no inheritance change.

## 3. Components

### 3.1 `src/whisper_aloud/stt/engine.py` (new)
The production contract + factory.

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np
    from ..config import WhisperAloudConfig
    from ..transcriber import TranscriptionResult


@runtime_checkable
class SpeechEngine(Protocol):
    """Production STT contract the daemon depends on. Transcriber satisfies it structurally."""

    @property
    def is_loaded(self) -> bool: ...

    @property
    def is_cancelling(self) -> bool: ...

    def load_model(self) -> None: ...

    def unload_model(self) -> None: ...

    def cancel_transcription(self) -> None: ...

    def transcribe_numpy(
        self, audio: "np.ndarray", sample_rate: int = 16000, **kwargs
    ) -> "TranscriptionResult": ...

    def transcribe_file(self, audio_path: str, **kwargs) -> "TranscriptionResult": ...


def create_engine(config: "WhisperAloudConfig") -> "SpeechEngine":
    """Build the production engine selected by config.model.engine.
    Lazy-imports the concrete engine to avoid import cost/cycles."""
    engine = config.model.engine
    if engine == "faster-whisper":
        from ..transcriber import Transcriber
        return Transcriber(config)
    from ..exceptions import ConfigurationError
    raise ConfigurationError(f"Unknown STT engine '{engine}'")
```

Notes:
- `TranscriptionResult` is referenced only in type hints (TYPE_CHECKING) — **no runtime
  import cycle**; it stays defined in `transcriber.py`.
- `runtime_checkable` lets a test assert `isinstance(Transcriber(cfg), SpeechEngine)`.

### 3.2 `src/whisper_aloud/config.py`
- Add field to `ModelConfig`: `engine: str = "faster-whisper"` (placed before `name`).
- Validation: accept only `{"faster-whisper"}` for now; raise `ConfigurationError` otherwise
  (WHISP-36 adds `"parakeet"`). Mirror the existing `valid_models`/`valid_devices` pattern.
- `MODEL_RELOAD_FIELDS`: add `"engine"` (changing engine triggers a model reload, same as
  changing `name`/`device`/`compute_type`).
- Serialization (`to_dict`/from-dict round-trip): include `engine` next to `name`.
- Env override: `WHISPER_ALOUD_MODEL_ENGINE` → `self.model.engine` (mirror existing pattern
  near line 389).

### 3.3 `src/whisper_aloud/service/daemon.py`
- Import: replace `from ..transcriber import Transcriber` with
  `from ..stt.engine import SpeechEngine, create_engine`.
- Attribute type: `self.transcriber: Optional[SpeechEngine] = None` (keep the attribute name
  `transcriber` to minimize diff; it now holds a SpeechEngine).
- Line ~240: `self.transcriber = Transcriber(self.config)` → `create_engine(self.config)`.
- Line ~565: `self.transcriber = Transcriber(new_config)` → `create_engine(new_config)`.
- **All other ~9 call sites unchanged** (`.load_model()`, `.transcribe_numpy()`,
  `.cancel_transcription()`, `.unload_model()`, `.is_loaded`) — the Protocol declares them.

## 4. Data flow (unchanged)
Recording → daemon `_transcribe_audio` → `self.transcriber.transcribe_numpy(audio)` →
`TranscriptionResult` → history + D-Bus `TranscriptionReady`. Identical to today; only the
construction of `self.transcriber` is now indirected through `create_engine`.

## 5. Error handling
`create_engine` raises `ConfigurationError` for an unknown engine — consistent with the
existing config validation, which already rejects an invalid engine at config-load time
(so an unknown engine fails fast at validation, and `create_engine` is a defensive backstop).

## 6. Testing
- `tests/stt/test_engine_factory.py`:
  - `create_engine` with a config whose `model.engine == "faster-whisper"` returns an object
    that `isinstance(obj, SpeechEngine)` is True and is a `Transcriber`.
  - `create_engine` with an unknown engine raises `ConfigurationError`.
- `tests/test_config.py` additions:
  - `ModelConfig.engine` defaults to `"faster-whisper"`.
  - config validation rejects an unknown engine value.
  - serialization round-trip preserves `engine`.
  - `WHISPER_ALOUD_MODEL_ENGINE` env override applies.
- **Regression gate:** the existing `tests/test_daemon.py`, `tests/test_transcriber.py`,
  `tests/test_config.py` pass unchanged → confirms identical behavior.

## 7. Acceptance criteria
- [ ] `SpeechEngine` protocol + `create_engine` factory exist in `src/whisper_aloud/stt/engine.py`.
- [ ] `Transcriber` satisfies `SpeechEngine` (asserted by a runtime_checkable isinstance test).
- [ ] `ModelConfig.engine` field added, validated, serialized, env-overridable.
- [ ] Daemon constructs its engine only via `create_engine`; no direct `Transcriber(...)` left
      in `daemon.py`.
- [ ] All pre-existing daemon/transcriber/config tests pass unchanged (regression-safe).

## 8. Risks
- Low. The only behavioral surface is the new `engine` field default; everything else is an
  indirection. The regression gate (existing daemon tests) is the safety net.
- A Protocol mismatch (if `Transcriber`'s signatures drift from the Protocol) is caught by the
  `isinstance` conformance test.
