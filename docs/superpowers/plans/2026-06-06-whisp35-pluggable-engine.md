# WHISP-35 — Pluggable Production Speech Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daemon construct its speech engine through a `SpeechEngine` protocol + `create_engine` factory instead of `Transcriber` directly, with faster-whisper as the only engine — a pure, regression-safe refactor that unblocks WHISP-36 (Parakeet in production).

**Architecture:** `Transcriber` already exposes exactly the surface the daemon uses (`load_model`, `unload_model`, `is_loaded`, `is_cancelling`, `cancel_transcription`, `transcribe_numpy`, `transcribe_file` → `TranscriptionResult`). We declare that surface as a `runtime_checkable` `Protocol` (`SpeechEngine`) in a new `src/whisper_aloud/stt/engine.py`, add a `create_engine(config)` factory selecting on a new `ModelConfig.engine` field, and swap the daemon's two `Transcriber(config)` constructions for `create_engine(config)`. `Transcriber` satisfies the Protocol structurally — no inheritance change. Behavior is identical; the existing daemon/transcriber/config test suites are the regression gate.

**Tech Stack:** Python 3.10+, `typing.Protocol` / `runtime_checkable`, dataclasses, pytest. faster-whisper / CTranslate2 (unchanged, behind the engine).

**Spec:** `docs/superpowers/specs/2026-06-05-whisp35-pluggable-engine-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/whisper_aloud/config.py` | `ModelConfig.engine` field, validation, serialization, env override, reload detection | Modify |
| `src/whisper_aloud/stt/engine.py` | `SpeechEngine` Protocol + `create_engine` factory (the production contract) | Create |
| `src/whisper_aloud/service/daemon.py` | Construct engine via `create_engine`; type hint via `SpeechEngine` | Modify |
| `tests/test_config.py` | engine field default, validation, roundtrip, env override, reload trigger | Modify |
| `tests/stt/test_engine_factory.py` | factory returns a `SpeechEngine`/`Transcriber`; unknown engine raises | Create |

**Invariants enforced by every task:** faster-whisper stays the only valid engine; no change to `Transcriber` internals, `TranscriptionResult`, or daemon logic; the pre-existing `tests/test_config.py`, `tests/test_daemon.py`, `tests/test_transcriber.py` must keep passing.

---

## Task 1: `ModelConfig.engine` field + reload-trigger registration

**Files:**
- Modify: `src/whisper_aloud/config.py:80-87`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_model_engine_defaults_to_faster_whisper():
    """ModelConfig.engine defaults to 'faster-whisper'."""
    config = WhisperAloudConfig()
    assert config.model.engine == "faster-whisper"


def test_engine_is_a_reload_field():
    """Changing the engine must trigger a model reload."""
    from whisper_aloud.config import MODEL_RELOAD_FIELDS
    assert "engine" in MODEL_RELOAD_FIELDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::test_model_engine_defaults_to_faster_whisper tests/test_config.py::test_engine_is_a_reload_field -v`
Expected: FAIL — `AttributeError: 'ModelConfig' object has no attribute 'engine'` and `assert 'engine' in {...}` is False.

- [ ] **Step 3: Add the field and register it for reload**

In `src/whisper_aloud/config.py`, edit the `ModelConfig` dataclass (currently lines 78-83) so `engine` is the first field, and add `"engine"` to `MODEL_RELOAD_FIELDS`:

```python
@dataclass
class ModelConfig:
    """Configuration for the Whisper model."""
    engine: str = "faster-whisper"
    name: str = "base"
    device: str = "auto"
    compute_type: str = "int8"
    download_root: Optional[str] = None


# Fields that trigger model reload when changed (class-level constant)
MODEL_RELOAD_FIELDS: Set[str] = {"engine", "name", "device", "compute_type"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::test_model_engine_defaults_to_faster_whisper tests/test_config.py::test_engine_is_a_reload_field -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/config.py tests/test_config.py
git commit -m "feat(config): add ModelConfig.engine field (default faster-whisper)"
```

---

## Task 2: Engine serialization, env override, and change detection

**Files:**
- Modify: `src/whisper_aloud/config.py:217-222` (to_dict), `:386-392` (_apply_env_overrides), `:601-608` (compute_changes)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_engine_serialization_roundtrip():
    """engine survives a to_dict / from_dict roundtrip."""
    config = WhisperAloudConfig()
    config.model.engine = "faster-whisper"
    data = config.to_dict()
    assert data["model"]["engine"] == "faster-whisper"
    restored = WhisperAloudConfig.from_dict(data)
    assert restored.model.engine == "faster-whisper"


def test_engine_env_override():
    """WHISPER_ALOUD_MODEL_ENGINE is applied by _apply_env_overrides.

    Uses a sentinel value (not the default) to prove the wiring, and calls
    _apply_env_overrides directly so it bypasses validate() — faster-whisper is
    currently the only value that would survive WhisperAloudConfig.load().
    """
    os.environ['WHISPER_ALOUD_MODEL_ENGINE'] = 'sentinel-engine'
    try:
        config = WhisperAloudConfig()
        config._apply_env_overrides()
        assert config.model.engine == "sentinel-engine"
    finally:
        os.environ.pop('WHISPER_ALOUD_MODEL_ENGINE', None)


def test_engine_change_requires_reload():
    """compute_changes flags an engine change as reload-requiring."""
    from whisper_aloud.config import compute_changes
    old = WhisperAloudConfig()
    new = WhisperAloudConfig()
    new.model.engine = "some-other-engine"
    changes = compute_changes(old, new)
    assert "engine" in changes.changed_fields["model"]
    assert changes.requires_model_reload is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::test_engine_serialization_roundtrip tests/test_config.py::test_engine_env_override tests/test_config.py::test_engine_change_requires_reload -v`
Expected: FAIL — roundtrip `KeyError: 'engine'` in `data["model"]`; env-override test fails because `_apply_env_overrides` doesn't yet read `WHISPER_ALOUD_MODEL_ENGINE` (engine stays `"faster-whisper"`, not `"sentinel-engine"`); change-detection `KeyError: 'model'` on `changes.changed_fields` (engine diff not recorded).

- [ ] **Step 3a: Serialize `engine` in to_dict**

In `src/whisper_aloud/config.py`, the `to_dict` model block (lines 217-222) — add `engine` first:

```python
            "model": {
                "engine": self.model.engine,
                "name": self.model.name,
                "device": self.model.device,
                "compute_type": self.model.compute_type,
                "download_root": self.model.download_root,
            },
```

(`from_dict` needs no change — it already loops with `setattr`/`hasattr` over `data["model"]`, so the new key is restored automatically.)

- [ ] **Step 3b: Add the env override**

In `_apply_env_overrides` (after line 389, alongside the other model overrides):

```python
        self.model.engine = os.getenv('WHISPER_ALOUD_MODEL_ENGINE', self.model.engine)
```

- [ ] **Step 3c: Detect engine changes in compute_changes**

In `compute_changes` (after line 603, before the `name` check), add:

```python
    if old.model.engine != new.model.engine:
        model_changes.add("engine")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::test_engine_serialization_roundtrip tests/test_config.py::test_engine_env_override tests/test_config.py::test_engine_change_requires_reload -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/config.py tests/test_config.py
git commit -m "feat(config): serialize, env-override, and reload-detect ModelConfig.engine"
```

---

## Task 3: Validate the engine value

**Files:**
- Modify: `src/whisper_aloud/config.py:491-505` (validate)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_valid_engine_passes_validation():
    """The default engine validates cleanly."""
    config = WhisperAloudConfig()
    config.validate()  # must not raise


def test_invalid_engine_rejected():
    """validate() rejects an unknown engine."""
    config = WhisperAloudConfig()
    config.model.engine = "nope-engine"
    with pytest.raises(ConfigurationError, match="Invalid engine"):
        config.validate()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::test_valid_engine_passes_validation tests/test_config.py::test_invalid_engine_rejected -v`
Expected: `test_invalid_engine_rejected` FAILS (no `ConfigurationError` raised — engine is currently unvalidated). `test_valid_engine_passes_validation` passes.

- [ ] **Step 3: Add engine validation**

In `validate()`, insert the engine check at the top of the method (before the model-name check at line 493), mirroring the `valid_devices` pattern:

```python
        # Validate engine (only faster-whisper in production today; WHISP-36 adds parakeet)
        valid_engines = ["faster-whisper"]
        if self.model.engine not in valid_engines:
            raise ConfigurationError(
                f"Invalid engine '{self.model.engine}'. Valid: {', '.join(valid_engines)}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::test_valid_engine_passes_validation tests/test_config.py::test_invalid_engine_rejected -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/config.py tests/test_config.py
git commit -m "feat(config): validate ModelConfig.engine against known engines"
```

---

## Task 4: `SpeechEngine` Protocol + `create_engine` factory

**Files:**
- Create: `src/whisper_aloud/stt/engine.py`
- Test: `tests/stt/test_engine_factory.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/stt/test_engine_factory.py`:

```python
"""Tests for the production SpeechEngine protocol + create_engine factory (WHISP-35)."""
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import ConfigurationError
from whisper_aloud.stt.engine import SpeechEngine, create_engine
from whisper_aloud.transcriber import Transcriber


def test_transcriber_satisfies_speech_engine_protocol():
    """Transcriber structurally conforms to the SpeechEngine protocol."""
    config = WhisperAloudConfig()
    engine = Transcriber(config)
    assert isinstance(engine, SpeechEngine)


def test_create_engine_returns_transcriber_for_faster_whisper():
    """The factory builds a Transcriber for the faster-whisper engine."""
    config = WhisperAloudConfig()
    config.model.engine = "faster-whisper"
    engine = create_engine(config)
    assert isinstance(engine, Transcriber)
    assert isinstance(engine, SpeechEngine)


def test_create_engine_rejects_unknown_engine():
    """The factory raises ConfigurationError for an unknown engine."""
    config = WhisperAloudConfig()
    config.model.engine = "totally-unknown"
    with pytest.raises(ConfigurationError, match="Unknown STT engine"):
        create_engine(config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/stt/test_engine_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'whisper_aloud.stt.engine'`.

- [ ] **Step 3: Create the Protocol + factory**

Create `src/whisper_aloud/stt/engine.py`:

```python
"""Production speech-engine contract for the daemon (WHISP-35).

The daemon depends on this Protocol via `create_engine` instead of constructing
`Transcriber` directly, so future engines (Parakeet, WHISP-36) become selectable
through `config.model.engine` without touching daemon code. `Transcriber` already
satisfies this Protocol structurally — no inheritance change required.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from ..config import WhisperAloudConfig
    from ..transcriber import TranscriptionResult


@runtime_checkable
class SpeechEngine(Protocol):
    """The STT surface the daemon consumes. Transcriber conforms structurally."""

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
    """Build the production engine selected by `config.model.engine`.

    Lazy-imports the concrete engine to avoid import cost and cycles.
    """
    engine = config.model.engine
    if engine == "faster-whisper":
        from ..transcriber import Transcriber

        return Transcriber(config)

    from ..exceptions import ConfigurationError

    raise ConfigurationError(f"Unknown STT engine '{engine}'")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/stt/test_engine_factory.py -v`
Expected: PASS (3 passed). The `isinstance(..., SpeechEngine)` checks confirm `Transcriber`'s signatures match the Protocol.

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/engine.py tests/stt/test_engine_factory.py
git commit -m "feat(stt): add SpeechEngine protocol + create_engine factory"
```

---

## Task 5: Rewire the daemon through `create_engine`

**Files:**
- Modify: `src/whisper_aloud/service/daemon.py:21` (import), `:125` (type hint), `:240` and `:565` (construction)
- Test (regression gate): `tests/test_daemon.py`, `tests/test_config_reload_integration.py`

- [ ] **Step 1: Run the daemon regression suite to capture the green baseline**

Run: `pytest tests/test_daemon.py tests/test_daemon_auto_paste.py tests/test_config_reload_integration.py -q`
Expected: PASS. Record the count — it must be identical after the change (this is the regression gate; behavior must not change).

- [ ] **Step 2: Replace the import**

In `src/whisper_aloud/service/daemon.py`, line 21, replace:

```python
from ..transcriber import Transcriber
```

with:

```python
from ..stt.engine import SpeechEngine, create_engine
```

- [ ] **Step 3: Update the attribute type hint**

Line 125, replace:

```python
        self.transcriber: Optional[Transcriber] = None
```

with:

```python
        self.transcriber: Optional[SpeechEngine] = None
```

- [ ] **Step 4: Swap the two construction sites**

Line 240, replace `self.transcriber = Transcriber(self.config)` with:

```python
            self.transcriber = create_engine(self.config)
```

Line 565, replace `self.transcriber = Transcriber(new_config)` with:

```python
            self.transcriber = create_engine(new_config)
```

(All other `self.transcriber.*` call sites — lines 261, 422, 728, 837-838 — stay unchanged; the Protocol declares every method they use.)

- [ ] **Step 5: Verify no direct `Transcriber(` construction remains in the daemon**

Run: `grep -n "Transcriber" src/whisper_aloud/service/daemon.py`
Expected: no output (zero matches) — the daemon no longer references `Transcriber` by name.

- [ ] **Step 6: Run the regression suite again — must match the baseline**

Run: `pytest tests/test_daemon.py tests/test_daemon_auto_paste.py tests/test_config_reload_integration.py -q`
Expected: PASS with the same count as Step 1. If any daemon test references `Transcriber` via patching (e.g. `patch("whisper_aloud.service.daemon.Transcriber")`), update that patch target to `whisper_aloud.service.daemon.create_engine` (or `whisper_aloud.transcriber.Transcriber`) so the mock still intercepts construction; re-run until green.

- [ ] **Step 7: Commit**

```bash
git add src/whisper_aloud/service/daemon.py
git commit -m "refactor(daemon): construct speech engine via create_engine factory"
```

---

## Task 6: Full-suite regression + lint verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/test_config.py tests/test_transcriber.py tests/test_daemon.py tests/stt/ tests/test_config_reload_integration.py -q`
Expected: PASS — all config, transcriber, daemon, and stt tests green. This confirms the refactor is behavior-preserving.

- [ ] **Step 2: Lint + format + type-check the touched files**

Run:
```bash
black --check src/whisper_aloud/config.py src/whisper_aloud/stt/engine.py src/whisper_aloud/service/daemon.py
ruff check src/whisper_aloud/config.py src/whisper_aloud/stt/engine.py src/whisper_aloud/service/daemon.py
mypy src/whisper_aloud/stt/engine.py
```
Expected: all clean. If `black --check` fails, run `black` on the files, review the diff, and amend the relevant commit.

- [ ] **Step 3: Confirm acceptance criteria (spec §7)**

Verify each is true and check it off:
- `SpeechEngine` protocol + `create_engine` factory exist in `src/whisper_aloud/stt/engine.py` (Task 4).
- `Transcriber` satisfies `SpeechEngine` via the runtime_checkable isinstance test (Task 4 Step 4).
- `ModelConfig.engine` is added, validated, serialized, env-overridable (Tasks 1-3).
- The daemon constructs its engine only via `create_engine`; no direct `Transcriber(...)` remains in `daemon.py` (Task 5 Step 5).
- All pre-existing daemon/transcriber/config tests pass unchanged (Task 5 Step 6, Task 6 Step 1).

---

## Definition of Done

- All 6 tasks committed on `feat/WHISP-35-pluggable-engine`.
- Full suite green; lint/format/type-check clean on touched files.
- Daemon contains zero direct `Transcriber` references; engine construction flows through `create_engine`.
- faster-whisper remains the only valid engine — observable daemon behavior is identical to before (regression gate held).
- WHISP-35 ready to move to `review`, then merge to `master`, unblocking WHISP-36 (Parakeet production engine).
