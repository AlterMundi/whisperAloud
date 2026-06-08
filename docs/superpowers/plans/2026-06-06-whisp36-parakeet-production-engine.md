# WHISP-36 — Parakeet Production Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make NVIDIA Parakeet TDT 0.6b v3 a config-selectable production speech engine (`engine="parakeet"`), with model download and the daemon reload-condition fix — faster-whisper behavior unchanged.

**Architecture:** Engine-aware config validation selects valid `model.name` per engine. A new `ParakeetSpeechEngine` implements the WHISP-35 `SpeechEngine` Protocol over sherpa-onnx (lazy-imported), returning the rich `TranscriptionResult`. A `model_download` module + `download-model` CLI fetch the sherpa-onnx tarball (with lazy fallback on first load). The `create_engine` factory dispatches to it. The daemon's reload trigger switches to the authoritative `detect_config_changes(...).requires_model_reload` so an engine change reloads.

**Tech Stack:** Python 3.10+, `typing.Protocol`, sherpa-onnx (optional dep), numpy/scipy/soundfile, urllib+tarfile, pytest.

**Spec:** `docs/superpowers/specs/2026-06-06-whisp36-parakeet-production-engine-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/whisper_aloud/config.py` | `ENGINE_MODELS` map; engine-conditional `model.name` validation; `parakeet` in valid engines | Modify |
| `src/whisper_aloud/stt/model_download.py` | name→sherpa tarball map, cache dir, idempotent `ensure_model` | Create |
| `src/whisper_aloud/__main__.py` | `download-model <name>` CLI subcommand + handler | Modify |
| `src/whisper_aloud/stt/parakeet_speech_engine.py` | `ParakeetSpeechEngine` implementing `SpeechEngine` over sherpa-onnx | Create |
| `src/whisper_aloud/stt/engine.py` | `create_engine` dispatches `parakeet` → `ParakeetSpeechEngine` | Modify |
| `src/whisper_aloud/service/daemon.py` | reload trigger → `detect_config_changes(...).requires_model_reload` | Modify |
| `pyproject.toml` | `sherpa-onnx` as an optional `[parakeet]` extra | Modify |
| `tests/test_config.py`, `tests/stt/test_model_download.py`, `tests/test_cli.py`, `tests/stt/test_parakeet_speech_engine.py`, `tests/stt/test_engine_factory.py`, `tests/test_daemon.py` | tests | Modify/Create |

**Invariants:** faster-whisper path never imports sherpa-onnx; `ParakeetSpeechEngine` imports `sherpa_onnx` only inside `load_model` (so the factory + isinstance work without it installed); all pre-existing tests keep passing.

Run pytest as `python -m pytest` inside `~/.venvs/whisper_aloud` (system pytest lacks deps).

---

## Task 1: Engine-aware config validation

**Files:**
- Modify: `src/whisper_aloud/config.py` (add `ENGINE_MODELS` near `MODEL_RELOAD_FIELDS` at line ~87; rewrite the engine + model-name checks in `validate()` lines ~496-510)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_parakeet_model_validates():
    """engine=parakeet with its canonical model passes validation."""
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "parakeet-tdt-0.6b-v3"
    config.validate()  # must not raise


def test_parakeet_rejects_whisper_model():
    """engine=parakeet with a whisper model name is rejected, naming the engine."""
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "base"
    with pytest.raises(ConfigurationError, match="for engine 'parakeet'"):
        config.validate()


def test_faster_whisper_rejects_parakeet_model():
    """engine=faster-whisper with a parakeet model name is rejected."""
    config = WhisperAloudConfig()
    config.model.engine = "faster-whisper"
    config.model.name = "parakeet-tdt-0.6b-v3"
    with pytest.raises(ConfigurationError, match="for engine 'faster-whisper'"):
        config.validate()


def test_parakeet_in_valid_engines():
    """engine=parakeet is now an accepted engine."""
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "parakeet-tdt-0.6b-v3"
    config.validate()  # engine accepted (no "Invalid engine" raised)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_parakeet_model_validates tests/test_config.py::test_parakeet_rejects_whisper_model tests/test_config.py::test_faster_whisper_rejects_parakeet_model tests/test_config.py::test_parakeet_in_valid_engines -v`
Expected: FAIL — `parakeet` is rejected by the current `valid_engines = ["faster-whisper"]`, and the model list isn't engine-aware.

- [ ] **Step 3: Add the `ENGINE_MODELS` map**

In `src/whisper_aloud/config.py`, right after the `MODEL_RELOAD_FIELDS` definition (line ~87), add:

```python
# Valid model names per engine (used by validate() and engine/download code).
ENGINE_MODELS: Dict[str, list] = {
    "faster-whisper": [
        "tiny", "base", "small", "medium",
        "large-v1", "large-v2", "large-v3", "large",
        "large-v3-turbo", "turbo",
    ],
    "parakeet": ["parakeet-tdt-0.6b-v3"],
}
```

(`Dict` is already imported in config.py.)

- [ ] **Step 4: Make `validate()` engine-aware**

In `validate()`, replace the existing engine check + model-name check (lines ~496-510) with:

```python
        # Validate engine
        valid_engines = list(ENGINE_MODELS.keys())
        if self.model.engine not in valid_engines:
            raise ConfigurationError(
                f"Invalid engine '{self.model.engine}'. Valid: {', '.join(valid_engines)}"
            )

        # Validate model name for the selected engine
        valid_models = ENGINE_MODELS[self.model.engine]
        if self.model.name not in valid_models:
            raise ConfigurationError(
                f"Invalid model name '{self.model.name}' for engine '{self.model.engine}'. "
                f"Valid: {', '.join(valid_models)}"
            )
```

Note: the message keeps the `"Invalid model name"` prefix, so the pre-existing `test_invalid_model_name` (which matches `"Invalid model name"`) still passes — while adding the `for engine '<engine>'` clause the new tests assert.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS — the 4 new tests plus all pre-existing config tests (the engine-validation and model-name tests from WHISP-35 still match).

- [ ] **Step 6: Commit**

```bash
git add src/whisper_aloud/config.py tests/test_config.py
git commit -m "feat(config): engine-aware model.name validation (adds parakeet)"
```

---

## Task 2: Model download module

**Files:**
- Create: `src/whisper_aloud/stt/model_download.py`
- Test: `tests/stt/test_model_download.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/stt/test_model_download.py`:

```python
"""Tests for sherpa-onnx model download/management (WHISP-36)."""
import tarfile
from pathlib import Path

import pytest

from whisper_aloud.stt import model_download as md


def test_model_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    assert d == tmp_path / "whisper_aloud" / "models" / "parakeet-tdt-0.6b-v3"


def test_unknown_model_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="Unknown model"):
        md.ensure_model("does-not-exist")


def test_ensure_model_idempotent_skips_download(monkeypatch, tmp_path):
    """If the model files already exist, ensure_model returns the dir without downloading."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    (d / "encoder.onnx").write_bytes(b"\x00")
    called = {"download": False}
    def _fake_download(url, dest):
        called["download"] = True
    monkeypatch.setattr(md, "_download", _fake_download)
    result = md.ensure_model("parakeet-tdt-0.6b-v3")
    assert result == d
    assert called["download"] is False


def test_ensure_model_downloads_and_flattens(monkeypatch, tmp_path):
    """ensure_model downloads the tarball, extracts, and flattens the single top dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # Build a fixture tarball mirroring sherpa's layout: one top dir with the model files.
    src = tmp_path / "src" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
    src.mkdir(parents=True)
    (src / "tokens.txt").write_text("<blk> 0\n")
    (src / "encoder.onnx").write_bytes(b"\x00")
    (src / "decoder.onnx").write_bytes(b"\x00")
    (src / "joiner.onnx").write_bytes(b"\x00")
    fixture = tmp_path / "fixture.tar.bz2"
    with tarfile.open(fixture, "w:bz2") as tf:
        tf.add(src, arcname=src.name)

    def _fake_download(url, dest):
        Path(dest).write_bytes(fixture.read_bytes())
    monkeypatch.setattr(md, "_download", _fake_download)

    d = md.ensure_model("parakeet-tdt-0.6b-v3")
    assert (d / "tokens.txt").exists()
    assert (d / "encoder.onnx").exists()
    assert d == md.model_dir("parakeet-tdt-0.6b-v3")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/stt/test_model_download.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'whisper_aloud.stt.model_download'`.

- [ ] **Step 3: Create the module**

Create `src/whisper_aloud/stt/model_download.py`:

```python
"""Download + manage sherpa-onnx speech models (WHISP-36).

Maps a canonical model name to a sherpa-onnx GitHub release tarball, downloads it
to the user cache, extracts and flattens it, and verifies the expected files. The
`_download` function is module-level so tests can monkeypatch it (no real network).
"""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_SOURCES: dict[str, str] = {
    "parakeet-tdt-0.6b-v3": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
    ),
}


def model_dir(name: str) -> Path:
    """Cache directory for a model (honors $XDG_CACHE_HOME)."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return cache / "whisper_aloud" / "models" / name


def _is_present(d: Path) -> bool:
    return (d / "tokens.txt").exists() and any(d.glob("encoder*.onnx"))


def _download(url: str, dest: str) -> None:
    """Download url to dest with a coarse progress log. Monkeypatched in tests."""
    def _hook(block_num, block_size, total):
        if total > 0:
            pct = min(100, block_num * block_size * 100 // total)
            logger.info("downloading model: %d%%", pct)
    urllib.request.urlretrieve(url, dest, _hook)


def ensure_model(name: str) -> Path:
    """Return the local model dir, downloading + extracting it if absent. Idempotent."""
    if name not in MODEL_SOURCES:
        raise ValueError(
            f"Unknown model '{name}'. Known: {', '.join(sorted(MODEL_SOURCES))}"
        )
    d = model_dir(name)
    if _is_present(d):
        return d
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tarball = os.path.join(tmp, "model.tar.bz2")
        logger.info("fetching model '%s' from %s", name, MODEL_SOURCES[name])
        _download(MODEL_SOURCES[name], tarball)
        extract_root = os.path.join(tmp, "extract")
        os.makedirs(extract_root, exist_ok=True)
        with tarfile.open(tarball) as tf:
            tf.extractall(extract_root)
        # sherpa tarballs ship a single top-level dir; flatten its contents into d.
        entries = [p for p in Path(extract_root).iterdir()]
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(extract_root)
        for item in top.iterdir():
            target = d / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))
    if not _is_present(d):
        raise RuntimeError(
            f"model '{name}' extracted but is missing tokens.txt / encoder*.onnx in {d}"
        )
    return d
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/stt/test_model_download.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/model_download.py tests/stt/test_model_download.py
git commit -m "feat(stt): sherpa-onnx model download/management module"
```

---

## Task 3: `download-model` CLI command

**Files:**
- Modify: `src/whisper_aloud/__main__.py` (add a subparser in `build_parser` ~line 451; add handler `cmd_download_model`; register in `COMMAND_MAP` ~line 504)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (this file already imports/exercises `main`; use the same style):

```python
def test_download_model_success(monkeypatch, capsys):
    """download-model calls ensure_model and reports the resolved path."""
    import whisper_aloud.stt.model_download as md
    from whisper_aloud.__main__ import main
    monkeypatch.setattr(md, "ensure_model", lambda name: f"/cache/{name}")
    rc = main(["download-model", "parakeet-tdt-0.6b-v3"])
    assert rc == 0
    assert "/cache/parakeet-tdt-0.6b-v3" in capsys.readouterr().out


def test_download_model_unknown(monkeypatch, capsys):
    """An unknown model name exits non-zero with an error."""
    import whisper_aloud.stt.model_download as md
    from whisper_aloud.__main__ import main
    def _raise(name):
        raise ValueError("Unknown model 'nope'. Known: parakeet-tdt-0.6b-v3")
    monkeypatch.setattr(md, "ensure_model", _raise)
    rc = main(["download-model", "nope"])
    assert rc == 1
    assert "Unknown model" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py::test_download_model_success tests/test_cli.py::test_download_model_unknown -v`
Expected: FAIL — `download-model` is not a known command (no subparser / handler).

- [ ] **Step 3: Add the handler**

In `src/whisper_aloud/__main__.py`, add this handler near the other `cmd_*` functions (e.g. after `cmd_history_search`):

```python
def cmd_download_model(args) -> int:
    """Download a speech model bundle to the local cache (no daemon required)."""
    from .stt.model_download import ensure_model
    try:
        path = ensure_model(args.name)
    except ValueError as e:
        return _error(str(e))
    except Exception as e:  # network / extract failures
        return _error(f"download failed: {e}")
    print(f"Model ready: {path}")
    return 0
```

- [ ] **Step 4: Register the subparser and command**

In `build_parser()`, after the simple subparsers (e.g. after the `"quit"` parser at line ~451), add:

```python
    p_download = sub.add_parser("download-model", help="download a speech model to the cache")
    p_download.add_argument("name", help="model name, e.g. parakeet-tdt-0.6b-v3")
```

In the `COMMAND_MAP` dict (line ~504), add the entry:

```python
    "download-model": cmd_download_model,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -q`
Expected: PASS — the 2 new tests plus all pre-existing CLI tests.

- [ ] **Step 6: Commit**

```bash
git add src/whisper_aloud/__main__.py tests/test_cli.py
git commit -m "feat(cli): add download-model command"
```

---

## Task 4: `ParakeetSpeechEngine` adapter

**Files:**
- Create: `src/whisper_aloud/stt/parakeet_speech_engine.py`
- Test: `tests/stt/test_parakeet_speech_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/stt/test_parakeet_speech_engine.py`:

```python
"""Tests for the production Parakeet engine (WHISP-36)."""
import importlib.util

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.stt.engine import SpeechEngine
from whisper_aloud.stt.parakeet_speech_engine import ParakeetSpeechEngine

_HAS_SHERPA = importlib.util.find_spec("sherpa_onnx") is not None


def _cfg():
    c = WhisperAloudConfig()
    c.model.engine = "parakeet"
    c.model.name = "parakeet-tdt-0.6b-v3"
    return c


def test_parakeet_satisfies_speech_engine_protocol():
    """ParakeetSpeechEngine structurally conforms to SpeechEngine (no sherpa needed)."""
    engine = ParakeetSpeechEngine(_cfg())
    assert isinstance(engine, SpeechEngine)


def test_cancel_before_transcribe_returns_empty():
    """A pending cancel makes transcribe_numpy return an empty result and clears the flag."""
    engine = ParakeetSpeechEngine(_cfg())
    engine._rec = object()  # pretend loaded so load_model isn't invoked
    engine.cancel_transcription()
    assert engine.is_cancelling
    out = engine.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000)
    assert out.text == ""
    assert out.duration == pytest.approx(1.0, abs=0.05)
    assert engine.is_cancelling is False


def test_unload_clears_loaded_state():
    engine = ParakeetSpeechEngine(_cfg())
    engine._rec = object()
    assert engine.is_loaded
    engine.unload_model()
    assert engine.is_loaded is False


@pytest.mark.skipif(not _HAS_SHERPA, reason="sherpa-onnx not installed")
def test_transcribe_numpy_smoke():
    """Real decode on a synthetic clip — skipped unless sherpa-onnx AND model are present."""
    from whisper_aloud.stt.model_download import model_dir
    engine = ParakeetSpeechEngine(_cfg())
    if not (model_dir("parakeet-tdt-0.6b-v3") / "tokens.txt").exists():
        pytest.skip("parakeet model not downloaded")
    out = engine.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000)
    assert isinstance(out.text, str)
    assert out.language == engine.config.transcription.language
    assert out.segments == []
    assert out.confidence == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/stt/test_parakeet_speech_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'whisper_aloud.stt.parakeet_speech_engine'`.

- [ ] **Step 3: Create the engine**

Create `src/whisper_aloud/stt/parakeet_speech_engine.py`:

```python
"""Production Parakeet engine (sherpa-onnx) implementing the SpeechEngine protocol (WHISP-36).

NOTE: distinct from the benchmark-side ``ParakeetEngine(STTEngine)`` in ``parakeet_engine.py``
(different surface — that one is load()/capabilities -> Transcript for the bench harness).
This class returns the rich ``TranscriptionResult`` and carries daemon lifecycle state.
``sherpa_onnx`` is imported lazily inside ``load_model`` so importing this module (for the
factory or isinstance checks) does not require sherpa-onnx to be installed.
"""
from __future__ import annotations

import glob
import logging
import os
import time
from typing import TYPE_CHECKING

import numpy as np

from ..transcriber import TranscriptionResult

if TYPE_CHECKING:
    from ..config import WhisperAloudConfig

logger = logging.getLogger(__name__)


class ParakeetSpeechEngine:
    """Parakeet TDT transducer engine satisfying the SpeechEngine protocol."""

    def __init__(self, config: "WhisperAloudConfig") -> None:
        self.config = config
        self._rec = None
        self._cancelling = False
        self._device_in_use: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._rec is not None

    @property
    def is_cancelling(self) -> bool:
        return self._cancelling

    def load_model(self) -> None:
        if self._rec is not None:
            return
        try:
            import sherpa_onnx
        except ImportError as e:
            raise ImportError(
                "The Parakeet engine requires sherpa-onnx. "
                "Install it with: pip install 'whisper-aloud[parakeet]'"
            ) from e

        from .model_download import ensure_model

        model_path = str(ensure_model(self.config.model.name))

        def _one(pattern: str) -> str:
            matches = glob.glob(os.path.join(model_path, pattern))
            if not matches:
                raise FileNotFoundError(f"{pattern} not found in model dir: {model_path}")
            return matches[0]

        encoder, decoder, joiner = _one("encoder*.onnx"), _one("decoder*.onnx"), _one("joiner*.onnx")
        tokens = os.path.join(model_path, "tokens.txt")
        requested = "cuda" if self.config.model.device == "cuda" else "cpu"

        def _build(provider: str):
            return sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                tokens=tokens,
                num_threads=os.cpu_count() or 4,
                model_type="nemo_transducer",
                provider=provider,
            )

        try:
            self._rec = _build(requested)
            self._device_in_use = requested
        except Exception as e:  # CUDA build/driver missing -> CPU fallback
            if requested == "cuda":
                logger.warning("Parakeet CUDA init failed (%s); falling back to CPU", e)
                self._rec = _build("cpu")
                self._device_in_use = "cpu"
            else:
                raise
        logger.info("Parakeet loaded on %s", self._device_in_use)

    def unload_model(self) -> None:
        self._rec = None
        self._device_in_use = None

    def cancel_transcription(self) -> None:
        self._cancelling = True

    def transcribe_numpy(
        self, audio: np.ndarray, sample_rate: int = 16000, **kwargs
    ) -> TranscriptionResult:
        t0 = time.time()
        samples = np.asarray(audio, dtype=np.float32)
        if sample_rate != 16000:
            import scipy.signal as ss

            n = int(len(samples) * 16000 / sample_rate)
            samples = ss.resample(samples, n).astype(np.float32)
        language = self.config.transcription.language
        duration = len(samples) / 16000
        # Best-effort cancellation: honored only before decode begins (sherpa offline
        # decode is a single blocking call). Clear the flag once handled.
        if self._cancelling:
            self._cancelling = False
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=duration, processing_time=0.0,
            )
        if self._rec is None:
            self.load_model()
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, samples)
        self._rec.decode_stream(stream)
        # segments/confidence are honest placeholders: sherpa-onnx offline transducer
        # does not expose per-segment dicts or a calibrated confidence in our shape.
        return TranscriptionResult(
            text=stream.result.text.strip(),
            language=language,
            segments=[],
            confidence=0.0,
            duration=duration,
            processing_time=round(time.time() - t0, 3),
        )

    def transcribe_file(self, audio_path: str, **kwargs) -> TranscriptionResult:
        import soundfile as sf

        audio, sample_rate = sf.read(audio_path, dtype="float32")
        return self.transcribe_numpy(audio, sample_rate, **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/stt/test_parakeet_speech_engine.py -v`
Expected: PASS — protocol conformance, cancel, and unload tests pass; the smoke test is skipped (no model/sherpa in CI).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/parakeet_speech_engine.py tests/stt/test_parakeet_speech_engine.py
git commit -m "feat(stt): ParakeetSpeechEngine production adapter (sherpa-onnx)"
```

---

## Task 5: Factory wiring

**Files:**
- Modify: `src/whisper_aloud/stt/engine.py` (the `create_engine` function)
- Test: `tests/stt/test_engine_factory.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/stt/test_engine_factory.py`:

```python
def test_create_engine_returns_parakeet_for_parakeet():
    """The factory builds a ParakeetSpeechEngine for engine=parakeet (no sherpa needed)."""
    from whisper_aloud.stt.parakeet_speech_engine import ParakeetSpeechEngine
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "parakeet-tdt-0.6b-v3"
    engine = create_engine(config)
    assert isinstance(engine, ParakeetSpeechEngine)
    assert isinstance(engine, SpeechEngine)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/stt/test_engine_factory.py::test_create_engine_returns_parakeet_for_parakeet -v`
Expected: FAIL — `create_engine` raises `ConfigurationError("Unknown STT engine 'parakeet'")` (parakeet not yet wired).

- [ ] **Step 3: Add the parakeet branch**

In `src/whisper_aloud/stt/engine.py`, in `create_engine`, add the parakeet branch after the faster-whisper branch:

```python
    if engine == "faster-whisper":
        from ..transcriber import Transcriber

        return Transcriber(config)
    if engine == "parakeet":
        from .parakeet_speech_engine import ParakeetSpeechEngine

        return ParakeetSpeechEngine(config)
    # Defensive backstop: config.validate() already rejects unknown engines upstream,
    # but guard here in case a caller constructs a config without validating.
    from ..exceptions import ConfigurationError

    raise ConfigurationError(f"Unknown STT engine '{engine}'")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/stt/test_engine_factory.py -v`
Expected: PASS (4 passed — the original 3 plus the new parakeet test).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/engine.py tests/stt/test_engine_factory.py
git commit -m "feat(stt): wire parakeet into create_engine factory"
```

---

## Task 6: Daemon reload-condition fix

**Files:**
- Modify: `src/whisper_aloud/service/daemon.py` (`_apply_config_changes`, the model-reload `if` at lines ~561-563; add `detect_config_changes` import)
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon.py` (the module already imports `daemon_module` and defines `_make_daemon`; `WhisperAloudConfig` and `patch` are available in the file — if not, import them at top):

```python
def test_engine_change_triggers_model_reload():
    """Changing model.engine at runtime reloads the engine via create_engine."""
    daemon = _make_daemon()
    daemon.config = WhisperAloudConfig()  # engine=faster-whisper
    new = WhisperAloudConfig()
    new.model.engine = "parakeet"
    new.model.name = "parakeet-tdt-0.6b-v3"
    with patch.object(daemon._daemon_module, "create_engine") as mock_ce:
        daemon._apply_config_changes(new)
    mock_ce.assert_called_once_with(new)


def test_no_model_change_does_not_reload():
    """An identical model config does not trigger a reload."""
    daemon = _make_daemon()
    daemon.config = WhisperAloudConfig()
    new = WhisperAloudConfig()  # identical model fields
    with patch.object(daemon._daemon_module, "create_engine") as mock_ce:
        daemon._apply_config_changes(new)
    mock_ce.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon.py::test_engine_change_triggers_model_reload tests/test_daemon.py::test_no_model_change_does_not_reload -v`
Expected: `test_engine_change_triggers_model_reload` FAILS — the current condition checks only `model.name`/`model.device`, so an engine-only change does not call `create_engine`. (`test_no_model_change_does_not_reload` already passes.)

- [ ] **Step 3: Switch the reload trigger to the authoritative detector**

In `src/whisper_aloud/service/daemon.py`, ensure `detect_config_changes` is importable. The daemon already imports from config (`from ..stt.engine import ...` and config types); add at the top alongside the other config imports:

```python
from ..config import detect_config_changes
```

Then replace the model-reload condition in `_apply_config_changes` (lines ~561-563):

```python
        # Check if model config changed
        if (new_config.model.name != self.config.model.name or
                new_config.model.device != self.config.model.device):
```

with:

```python
        # Reload when any model-reload field changed (engine/name/device/compute_type).
        if detect_config_changes(self.config, new_config).requires_model_reload:
```

(Leave the body — `self.transcriber = create_engine(new_config); self.transcriber.load_model(); self._model_loaded = True` — unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon.py tests/test_config_reload_integration.py -q`
Expected: PASS — the two new tests plus all pre-existing daemon/reload tests (name/device changes still trigger reload because they're in MODEL_RELOAD_FIELDS).

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/service/daemon.py tests/test_daemon.py
git commit -m "fix(daemon): reload model on any MODEL_RELOAD_FIELDS change (incl. engine)"
```

---

## Task 7: Optional dependency + full regression + acceptance

**Files:**
- Modify: `pyproject.toml` (add a `[parakeet]` optional-dependency extra)
- Verification only otherwise.

- [ ] **Step 1: Add sherpa-onnx as an optional extra**

In `pyproject.toml`, under `[project.optional-dependencies]` (create the table if absent; `[dev]` already exists there), add:

```toml
parakeet = ["sherpa-onnx"]
```

- [ ] **Step 2: Verify the faster-whisper path never imports sherpa-onnx**

Run:
```bash
python -c "import sys; import whisper_aloud.stt.engine, whisper_aloud.config, whisper_aloud.service.daemon; assert 'sherpa_onnx' not in sys.modules, sorted(m for m in sys.modules if 'sherpa' in m); print('OK: sherpa-onnx not imported by the default path')"
```
Expected: `OK: ...`. (Importing the engine factory, config, and daemon must not pull in sherpa-onnx — it's lazy-imported inside `ParakeetSpeechEngine.load_model`.)

- [ ] **Step 3: Full relevant suite**

Run:
```bash
python -m pytest tests/test_config.py tests/test_cli.py tests/test_transcriber.py tests/test_daemon.py tests/test_daemon_auto_paste.py tests/stt/ tests/test_config_reload_integration.py -q
```
Expected: all green.

- [ ] **Step 4: Lint touched files**

Run:
```bash
ruff check src/whisper_aloud/config.py src/whisper_aloud/stt/model_download.py src/whisper_aloud/stt/parakeet_speech_engine.py src/whisper_aloud/stt/engine.py src/whisper_aloud/service/daemon.py src/whisper_aloud/__main__.py
```
Expected: clean for the WHISP-36 changes. (black/mypy are not installed in this venv; ruff is the project linter. If ruff flags a pre-existing issue unrelated to these changes, list it and leave it.)

- [ ] **Step 5: Confirm acceptance criteria (report each PASS/FAIL with evidence)**

1. `engine="parakeet"` valid; engine-conditional model validation with clear errors (Task 1).
2. `ParakeetSpeechEngine` satisfies `SpeechEngine` — isinstance test passes (Task 4).
3. `create_engine` returns `ParakeetSpeechEngine` for `engine=parakeet` (Task 5).
4. `download-model` CLI + lazy download both work and are idempotent (Tasks 2-3; lazy path is `load_model`→`ensure_model`).
5. Daemon reloads on `engine` change (Task 6).
6. sherpa-onnx is optional; default path doesn't import it (Step 2).
7. All pre-existing tests pass (Step 3).

- [ ] **Step 6: Commit the packaging change**

```bash
git add pyproject.toml
git commit -m "build: add optional [parakeet] extra (sherpa-onnx)"
```

---

## Definition of Done

- All 7 tasks committed on `feat/WHISP-36-parakeet-production-engine`.
- Full suite green; ruff clean on touched files; sherpa-onnx not imported by the faster-whisper path.
- `whisper-aloud download-model parakeet-tdt-0.6b-v3` fetches the model; setting `engine=parakeet` + `model.name=parakeet-tdt-0.6b-v3` runs Parakeet end-to-end (lazy-downloading the model if absent); switching engine at runtime reloads.
- WHISP-36 ready for `review` → merge. Settings-UI engine selector remains the documented follow-up.
