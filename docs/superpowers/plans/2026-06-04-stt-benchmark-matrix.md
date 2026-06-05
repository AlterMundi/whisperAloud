# STT Benchmark Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, publishable STT performance matrix (`language × model × engine × hardware`) as a formal sub-project, backed by a shared `STTEngine` interface that also advances the WHISP-35/36 production refactor.

**Architecture:** A new `src/whisper_aloud/stt/` package defines the `STTEngine` interface plus production-bound `faster-whisper` and `parakeet` adapters. A `bench/` harness (datasets, hardware probe, runner CLI, append-only JSONL ledger, generated `MATRIX.md`) consumes those engines through a registry, with a bench-local `whispercpp` adapter and an experimental escape hatch. Results carry full hardware provenance and merge across machines by concatenating ledger lines.

**Tech Stack:** Python 3.10+, faster-whisper/CTranslate2, sherpa-onnx (Parakeet), whisper.cpp CLI (Vulkan), jiwer (WER), HuggingFace `datasets` (FLEURS), psutil, pytest.

**Spec:** `docs/superpowers/specs/2026-06-04-stt-benchmark-matrix-design.md`
**Branch:** `feat/WHISP-41-stt-benchmark-matrix`
**Lattice:** WHISP-41

---

## File Structure

**New package — production-bound engines (consumed by bench AND future daemon):**
- `src/whisper_aloud/stt/__init__.py` — exports `STTEngine`, `Transcript`, `EngineCapabilities`.
- `src/whisper_aloud/stt/base.py` — interface + dataclasses + `transcribe_file` helper.
- `src/whisper_aloud/stt/faster_whisper_engine.py` — wraps `faster_whisper.WhisperModel`.
- `src/whisper_aloud/stt/parakeet_engine.py` — wraps `sherpa_onnx.OfflineRecognizer`.

**Benchmark harness:**
- `bench/__init__.py`
- `bench/wer.py` — text normalization + WER (jiwer).
- `bench/hwprobe.py` — hardware provenance auto-capture.
- `bench/ledger.py` — append/read the JSONL results ledger.
- `bench/report.py` — ledger → `MATRIX.md`.
- `bench/datasets/__init__.py`, `bench/datasets/fleurs.py` — fetch/normalize FLEURS, write manifests.
- `bench/datasets/manifests/{es,en}.jsonl` — committed references (audio gitignored).
- `bench/engines/__init__.py` — `ENGINE_REGISTRY` + `resolve()`.
- `bench/engines/whispercpp.py` — bench-local `whisper-cli` wrapper (CPU + Vulkan).
- `bench/engines/experimental/__init__.py` — escape hatch (empty placeholder).
- `bench/runner.py` — CLI orchestrator.
- `bench/scripts/run_remote.sh` — rsync + remote run on inference01.
- `bench/README.md`, `bench/requirements-bench.txt`, `bench/results/ledger.jsonl`, `bench/MATRIX.md`.

**Tests:** `tests/stt/test_base.py`, `tests/stt/test_faster_whisper_engine.py`, `tests/stt/test_parakeet_engine.py`, `tests/bench/test_wer.py`, `tests/bench/test_hwprobe.py`, `tests/bench/test_ledger.py`, `tests/bench/test_report.py`.

**Removed at the end (superseded by WHISP-33 migration):** `bench/run_bench.py`, `bench/fetch_audio.py`, `bench/RESULTS_es_cpu.md`, `bench/results/*.json`.

---

## Task 1: STTEngine interface and dataclasses

**Files:**
- Create: `src/whisper_aloud/stt/__init__.py`
- Create: `src/whisper_aloud/stt/base.py`
- Test: `tests/stt/test_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stt/test_base.py
import numpy as np
import pytest
from whisper_aloud.stt import STTEngine, Transcript, EngineCapabilities


class DummyEngine(STTEngine):
    name = "dummy"

    def load(self, model, device, compute_type):
        self._loaded = (model, device, compute_type)

    def transcribe_numpy(self, samples, sample_rate, language=None):
        return Transcript(text=f"{len(samples)}@{sample_rate}")

    @property
    def capabilities(self):
        return EngineCapabilities(
            streaming=False, languages=["es", "en"],
            native_punctuation=True, devices=["cpu"],
        )


def test_transcript_defaults():
    t = Transcript(text="hola")
    assert t.text == "hola"
    assert t.segments is None


def test_capabilities_fields():
    caps = EngineCapabilities(streaming=True, languages="multilingual",
                              native_punctuation=False, devices=["cpu", "cuda"])
    assert caps.streaming is True
    assert caps.languages == "multilingual"
    assert "cuda" in caps.devices


def test_transcribe_file_delegates_to_numpy(tmp_path):
    import soundfile as sf
    p = tmp_path / "a.wav"
    sf.write(p, np.zeros(16000, dtype=np.float32), 16000)
    eng = DummyEngine()
    eng.load("m", "cpu", "int8")
    result = eng.transcribe_file(str(p))
    assert result.text == "16000@16000"


def test_unload_default_is_noop():
    DummyEngine().unload()  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/stt/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'whisper_aloud.stt'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/whisper_aloud/stt/base.py
"""Shared STT engine interface consumed by the benchmark and (later) the daemon."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import soundfile as sf


@dataclass
class Transcript:
    """Result of a transcription. `segments` is optional, engine-dependent."""
    text: str
    segments: list | None = None


@dataclass
class EngineCapabilities:
    """What an engine can do — used by the runner to pick devices and by docs."""
    streaming: bool
    languages: list[str] | str          # explicit list, or the string "multilingual"
    native_punctuation: bool
    devices: list[str]                  # subset of {cpu, cuda, vulkan, openvino, coral}


class STTEngine(ABC):
    """Contract every engine adapter implements. The daemon will depend on this in WHISP-35."""

    name: str = "stt-engine"

    @abstractmethod
    def load(self, model: str, device: str, compute_type: str) -> None:
        """Load the model onto the given device. Idempotent re-load is allowed."""

    @abstractmethod
    def transcribe_numpy(
        self, samples: np.ndarray, sample_rate: int, language: str | None = None
    ) -> Transcript:
        """Transcribe mono float32 PCM samples. The one method the pipeline needs."""

    @property
    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        ...

    def transcribe_file(self, path: str, language: str | None = None) -> Transcript:
        """Read a WAV and delegate to transcribe_numpy. Concrete helper, not abstract."""
        samples, sr = sf.read(path, dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        return self.transcribe_numpy(samples, sr, language=language)

    def unload(self) -> None:
        """Free the model. Default no-op; adapters override when they hold resources."""
        return None
```

```python
# src/whisper_aloud/stt/__init__.py
"""Pluggable STT engines with a shared interface."""
from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript

__all__ = ["STTEngine", "Transcript", "EngineCapabilities"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/stt/test_base.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/__init__.py src/whisper_aloud/stt/base.py tests/stt/test_base.py
git commit -m "feat(stt): add STTEngine interface, Transcript, EngineCapabilities"
```

---

## Task 2: faster-whisper engine adapter

**Files:**
- Create: `src/whisper_aloud/stt/faster_whisper_engine.py`
- Test: `tests/stt/test_faster_whisper_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stt/test_faster_whisper_engine.py
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from whisper_aloud.stt.faster_whisper_engine import FasterWhisperEngine
from whisper_aloud.stt import Transcript, EngineCapabilities


def test_capabilities():
    eng = FasterWhisperEngine()
    caps = eng.capabilities
    assert isinstance(caps, EngineCapabilities)
    assert caps.streaming is False
    assert caps.languages == "multilingual"
    assert "cpu" in caps.devices and "cuda" in caps.devices


def test_transcribe_numpy_joins_segments():
    eng = FasterWhisperEngine()
    seg1 = MagicMock(); seg1.text = "hola "
    seg2 = MagicMock(); seg2.text = "mundo"
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([seg1, seg2], MagicMock())
    eng._model = fake_model  # inject loaded model
    out = eng.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000, language="es")
    assert isinstance(out, Transcript)
    assert out.text == "hola mundo"
    fake_model.transcribe.assert_called_once()


def test_transcribe_without_load_raises():
    with pytest.raises(RuntimeError):
        FasterWhisperEngine().transcribe_numpy(np.zeros(10, dtype=np.float32), 16000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/stt/test_faster_whisper_engine.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/whisper_aloud/stt/faster_whisper_engine.py
"""faster-whisper (CTranslate2) adapter."""
from __future__ import annotations

import numpy as np

from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript


class FasterWhisperEngine(STTEngine):
    name = "faster-whisper"

    def __init__(self) -> None:
        self._model = None
        self._beam_size = 5

    def load(self, model: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe_numpy(self, samples, sample_rate, language=None) -> Transcript:
        if self._model is None:
            raise RuntimeError("FasterWhisperEngine.load() must be called before transcribe")
        if sample_rate != 16000:
            import scipy.signal as ss
            samples = ss.resample(samples, int(len(samples) * 16000 / sample_rate)).astype(np.float32)
        segments, _ = self._model.transcribe(
            samples, language=language, beam_size=self._beam_size, vad_filter=False
        )
        seglist = list(segments)
        text = "".join(s.text for s in seglist).strip()
        return Transcript(text=text, segments=seglist)

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            streaming=False, languages="multilingual",
            native_punctuation=True, devices=["cpu", "cuda"],
        )

    def unload(self) -> None:
        self._model = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/stt/test_faster_whisper_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/faster_whisper_engine.py tests/stt/test_faster_whisper_engine.py
git commit -m "feat(stt): add faster-whisper engine adapter"
```

---

## Task 3: Parakeet (sherpa-onnx) engine adapter

**Files:**
- Create: `src/whisper_aloud/stt/parakeet_engine.py`
- Test: `tests/stt/test_parakeet_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stt/test_parakeet_engine.py
import numpy as np
import pytest
from unittest.mock import MagicMock
from whisper_aloud.stt.parakeet_engine import ParakeetEngine
from whisper_aloud.stt import Transcript, EngineCapabilities


def test_capabilities():
    caps = ParakeetEngine().capabilities
    assert isinstance(caps, EngineCapabilities)
    assert caps.native_punctuation is True
    assert "cpu" in caps.devices and "cuda" in caps.devices


def test_transcribe_numpy_uses_recognizer():
    eng = ParakeetEngine()
    fake_stream = MagicMock()
    fake_stream.result.text = "hola mundo"
    fake_rec = MagicMock()
    fake_rec.create_stream.return_value = fake_stream
    eng._rec = fake_rec  # inject loaded recognizer
    out = eng.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000, language="es")
    assert isinstance(out, Transcript)
    assert out.text == "hola mundo"
    fake_stream.accept_waveform.assert_called_once()
    fake_rec.decode_stream.assert_called_once_with(fake_stream)


def test_transcribe_without_load_raises():
    with pytest.raises(RuntimeError):
        ParakeetEngine().transcribe_numpy(np.zeros(10, dtype=np.float32), 16000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/stt/test_parakeet_engine.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/whisper_aloud/stt/parakeet_engine.py
"""NVIDIA Parakeet TDT transducer adapter via sherpa-onnx.

`model` is a directory containing encoder*.onnx, decoder*.onnx, joiner*.onnx, tokens.txt
(e.g. the sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 release).
"""
from __future__ import annotations

import glob
import os

import numpy as np

from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript


class ParakeetEngine(STTEngine):
    name = "parakeet"

    def __init__(self) -> None:
        self._rec = None
        self._model_dir = None

    def load(self, model: str, device: str, compute_type: str) -> None:
        import sherpa_onnx
        enc = glob.glob(os.path.join(model, "encoder*.onnx"))[0]
        dec = glob.glob(os.path.join(model, "decoder*.onnx"))[0]
        joi = glob.glob(os.path.join(model, "joiner*.onnx"))[0]
        tokens = os.path.join(model, "tokens.txt")
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=enc, decoder=dec, joiner=joi, tokens=tokens,
            num_threads=os.cpu_count() or 4, model_type="nemo_transducer",
            provider=("cuda" if device == "cuda" else "cpu"),
        )
        self._model_dir = model

    def transcribe_numpy(self, samples, sample_rate, language=None) -> Transcript:
        if self._rec is None:
            raise RuntimeError("ParakeetEngine.load() must be called before transcribe")
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, np.asarray(samples, dtype=np.float32))
        self._rec.decode_stream(stream)
        return Transcript(text=stream.result.text.strip())

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            streaming=False,   # offline mode here; streaming variants are WHISP-37
            languages=["es", "en", "fr", "de", "it", "pt"],  # v3 = 25 EU langs (subset listed)
            native_punctuation=True, devices=["cpu", "cuda"],
        )

    def unload(self) -> None:
        self._rec = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/stt/test_parakeet_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/whisper_aloud/stt/parakeet_engine.py tests/stt/test_parakeet_engine.py
git commit -m "feat(stt): add Parakeet (sherpa-onnx) engine adapter"
```

---

## Task 4: WER normalization utility

**Files:**
- Create: `bench/__init__.py` (empty), `bench/wer.py`
- Test: `tests/bench/test_wer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_wer.py
from bench.wer import wer


def test_identical_is_zero():
    assert wer("Hola, mundo.", "hola mundo") == 0.0


def test_one_substitution():
    # 2 reference words, 1 wrong -> 0.5
    assert wer("hola mundo", "hola tierra") == 0.5


def test_punctuation_and_case_ignored():
    assert wer("El gato.", "el GATO") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_wer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.wer'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/__init__.py
```

```python
# bench/wer.py
"""Word Error Rate with normalization identical across all engines."""
import jiwer

_norm = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
    jiwer.ReduceToListOfListOfWords(),
])


def wer(reference: str, hypothesis: str) -> float:
    """Normalized WER for a single ref/hyp pair (lowercase, no punctuation)."""
    return jiwer.wer(
        reference=[reference], hypothesis=[hypothesis],
        reference_transform=_norm, hypothesis_transform=_norm,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_wer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/__init__.py bench/wer.py tests/bench/test_wer.py
git commit -m "feat(bench): add normalized WER utility"
```

---

## Task 5: Hardware provenance probe

**Files:**
- Create: `bench/hwprobe.py`
- Test: `tests/bench/test_hwprobe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_hwprobe.py
from bench.hwprobe import parse_cpu_model, parse_nvidia_smi, probe


def test_parse_cpu_model():
    cpuinfo = "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i7-1360P\nflags\t: ...\n"
    assert parse_cpu_model(cpuinfo) == "13th Gen Intel(R) Core(TM) i7-1360P"


def test_parse_cpu_model_missing():
    assert parse_cpu_model("processor\t: 0\n") is None


def test_parse_nvidia_smi():
    out = "NVIDIA GeForce RTX 4090, 24564 MiB\n"
    name, vram_gb = parse_nvidia_smi(out)
    assert name == "NVIDIA GeForce RTX 4090"
    assert vram_gb == 24  # 24564 MiB rounded to GB


def test_parse_nvidia_smi_empty():
    assert parse_nvidia_smi("") == (None, None)


def test_probe_returns_required_keys():
    hw = probe(hw_id="testbox")
    for key in ("id", "kind", "gpu", "vram_gb", "cpu", "cores", "ram_gb", "os"):
        assert key in hw
    assert hw["id"] == "testbox"
    assert hw["kind"] in ("cpu", "gpu")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_hwprobe.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/hwprobe.py
"""Best-effort hardware provenance capture. Missing tools -> None, never crash."""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess


def parse_cpu_model(cpuinfo: str) -> str | None:
    for line in cpuinfo.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def parse_nvidia_smi(out: str) -> tuple[str | None, int | None]:
    line = out.strip().splitlines()[0] if out.strip() else ""
    if not line:
        return (None, None)
    parts = [p.strip() for p in line.split(",")]
    name = parts[0] if parts else None
    vram_gb = None
    if len(parts) > 1:
        m = re.search(r"(\d+)", parts[1])
        if m:
            vram_gb = round(int(m.group(1)) / 1024)
    return (name, vram_gb)


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def probe(hw_id: str | None = None) -> dict:
    cpuinfo = ""
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
    except OSError:
        pass
    cpu = parse_cpu_model(cpuinfo)

    ram_gb = None
    try:
        ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9)
    except (ValueError, OSError):
        pass

    gpu, vram_gb = (None, None)
    if shutil.which("nvidia-smi"):
        gpu, vram_gb = parse_nvidia_smi(
            _run(["nvidia-smi", "--query-gpu=name,memory.total",
                  "--format=csv,noheader"])
        )

    return {
        "id": hw_id or socket.gethostname(),
        "kind": "gpu" if gpu else "cpu",
        "gpu": gpu,
        "vram_gb": vram_gb,
        "cpu": cpu,
        "cores": os.cpu_count(),
        "ram_gb": ram_gb,
        "os": f"{platform.system()} {platform.release()}",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_hwprobe.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/hwprobe.py tests/bench/test_hwprobe.py
git commit -m "feat(bench): add hardware provenance probe"
```

---

## Task 6: Results ledger (append/read)

**Files:**
- Create: `bench/ledger.py`
- Test: `tests/bench/test_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_ledger.py
from bench.ledger import append_row, read_rows, make_row


def test_make_row_has_schema_keys():
    row = make_row(
        lang="es", dataset="fleurs/es_419", n_clips=15,
        engine="parakeet", model="tdt-0.6b-v3", compute_type="int8", device="cpu",
        hw={"id": "box"}, metrics={"wer": 0.034, "rtf": 0.16},
        git_sha="abc123",
    )
    for key in ("ts", "git_sha", "bench_version", "lang", "dataset", "n_clips",
                "engine", "model", "compute_type", "device", "hw", "metrics", "notes"):
        assert key in row
    assert row["bench_version"] == 1


def test_append_and_read_roundtrip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    r1 = make_row(lang="es", dataset="d", n_clips=1, engine="e", model="m",
                  compute_type="int8", device="cpu", hw={}, metrics={}, git_sha="x")
    r2 = make_row(lang="en", dataset="d", n_clips=1, engine="e", model="m",
                  compute_type="int8", device="cuda", hw={}, metrics={}, git_sha="y")
    append_row(path, r1)
    append_row(path, r2)
    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[0]["lang"] == "es"
    assert rows[1]["device"] == "cuda"


def test_read_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "nope.jsonl") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/ledger.py
"""Append-only JSONL results ledger. One row per (engine,model,lang,device,hw) run."""
from __future__ import annotations

import datetime
import json
import os

BENCH_VERSION = 1


def make_row(*, lang, dataset, n_clips, engine, model, compute_type, device,
             hw, metrics, git_sha, notes="", ts=None):
    """Build one ledger row. `ts` defaults to current UTC (pass explicitly in tests)."""
    if ts is None:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ts": ts, "git_sha": git_sha, "bench_version": BENCH_VERSION,
        "lang": lang, "dataset": dataset, "n_clips": n_clips,
        "engine": engine, "model": model, "compute_type": compute_type, "device": device,
        "hw": hw, "metrics": metrics, "notes": notes,
    }


def append_row(path, row) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path) -> list:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_ledger.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/ledger.py tests/bench/test_ledger.py
git commit -m "feat(bench): add append-only results ledger"
```

---

## Task 7: Matrix report generator

**Files:**
- Create: `bench/report.py`
- Test: `tests/bench/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_report.py
from bench.report import generate_matrix


def _row(lang, engine, model, device, hw_id, wer, rtf):
    return {
        "lang": lang, "engine": engine, "model": model, "device": device,
        "compute_type": "int8", "hw": {"id": hw_id},
        "metrics": {"wer": wer, "rtf": rtf, "peak_rss_mb": 500,
                    "peak_vram_mb": None, "model_size_mb": 600},
        "dataset": "fleurs/" + lang, "n_clips": 15,
    }


def test_generate_matrix_contains_rows_and_headers():
    rows = [
        _row("es", "parakeet", "tdt-0.6b-v3", "cpu", "i7-1360P", 0.034, 0.16),
        _row("es", "faster-whisper", "small", "cpu", "i7-1360P", 0.055, 0.16),
    ]
    md = generate_matrix(rows)
    assert "# STT Benchmark Matrix" in md
    assert "parakeet" in md and "faster-whisper" in md
    assert "3.4" in md      # WER rendered as percentage
    assert "i7-1360P" in md


def test_generate_matrix_empty():
    md = generate_matrix([])
    assert "# STT Benchmark Matrix" in md
    assert "No results" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/report.py
"""Render the results ledger into MATRIX.md. Pure function of the ledger rows."""
from __future__ import annotations

_HEADER = "# STT Benchmark Matrix\n\n"
_CAVEAT = (
    "> Generated from `bench/results/ledger.jsonl` by `python -m bench.report`. "
    "FLEURS is read-speech: absolute WER is optimistic vs real mic dictation, but "
    "relative ordering is reliable. GPU numbers are machine-specific (see `hw.id`).\n\n"
)
_COLS = ["lang", "engine", "model", "compute", "device", "hw.id",
         "WER %", "RTF", "RAM MB", "VRAM MB", "size MB"]


def _fmt(v):
    return "—" if v is None else v


def generate_matrix(rows: list) -> str:
    if not rows:
        return _HEADER + _CAVEAT + "_No results yet. Run `python -m bench.runner`._\n"
    lines = [_HEADER, _CAVEAT, "| " + " | ".join(_COLS) + " |",
             "|" + "|".join(["---"] * len(_COLS)) + "|"]
    # sort for stable output: lang, then WER ascending
    for r in sorted(rows, key=lambda r: (r["lang"], r["metrics"].get("wer", 1.0))):
        m = r["metrics"]
        wer_pct = f"{m['wer'] * 100:.1f}" if m.get("wer") is not None else "—"
        cells = [
            r["lang"], r["engine"], r["model"], r.get("compute_type", "—"),
            r["device"], r["hw"].get("id", "—"),
            wer_pct,
            f"{m.get('rtf'):.3f}" if m.get("rtf") is not None else "—",
            _fmt(m.get("peak_rss_mb")), _fmt(m.get("peak_vram_mb")),
            _fmt(m.get("model_size_mb")),
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def main():
    import os
    from bench.ledger import read_rows
    here = os.path.dirname(__file__)
    rows = read_rows(os.path.join(here, "results", "ledger.jsonl"))
    md = generate_matrix(rows)
    with open(os.path.join(here, "MATRIX.md"), "w") as f:
        f.write(md)
    print(f"Wrote MATRIX.md with {len(rows)} rows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_report.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/report.py tests/bench/test_report.py
git commit -m "feat(bench): add MATRIX.md report generator"
```

---

## Task 8: FLEURS dataset fetcher

**Files:**
- Create: `bench/datasets/__init__.py` (empty), `bench/datasets/fleurs.py`
- Modify: `bench/.gitignore` (ensure `audio/` ignored, manifests kept)

This migrates the WHISP-33 `fetch_audio.py` into a multi-language fetcher. No unit test (it
is network I/O against HuggingFace); it is validated by running it in Task 11.

- [ ] **Step 1: Write the fetcher**

```python
# bench/datasets/__init__.py
```

```python
# bench/datasets/fleurs.py
"""Fetch + normalize FLEURS clips to 16kHz mono WAV and write a manifest.

Avoids torchcodec (heavy) by disabling datasets auto-decode and decoding bytes
with soundfile. Audio goes to bench/datasets/audio/<lang>/, references to
bench/datasets/manifests/<lang>.jsonl (manifest is committed; audio is gitignored).

Usage: python -m bench.datasets.fleurs --lang es,en --n 40
"""
from __future__ import annotations

import argparse
import io
import json
import os

import numpy as np
import soundfile as sf

HERE = os.path.dirname(__file__)
AUDIO_ROOT = os.path.join(HERE, "audio")
MANIFEST_ROOT = os.path.join(HERE, "manifests")

# FLEURS config name per language code
FLEURS_CONFIG = {"es": "es_419", "en": "en_us", "fr": "fr_fr",
                 "de": "de_de", "pt": "pt_br", "it": "it_it"}


def fetch_lang(lang: str, n: int, min_s: float = 4.0, max_s: float = 30.0) -> int:
    from datasets import Audio, load_dataset
    config = FLEURS_CONFIG[lang]
    out_dir = os.path.join(AUDIO_ROOT, lang)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(MANIFEST_ROOT, exist_ok=True)
    print(f"[{lang}] loading FLEURS {config} (test, streaming, no-decode)...", flush=True)
    ds = load_dataset("google/fleurs", config, split="test", streaming=True)
    ds = ds.cast_column("audio", Audio(decode=False))

    rows = []
    for ex in ds:
        if len(rows) >= n:
            break
        audio = ex["audio"]
        raw = audio.get("bytes")
        if raw is None and audio.get("path") and os.path.exists(audio["path"]):
            with open(audio["path"], "rb") as fh:
                raw = fh.read()
        if raw is None:
            continue
        arr, sr = sf.read(io.BytesIO(raw), dtype="float32")
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        if sr != 16000:
            import scipy.signal as ss
            arr = ss.resample(arr, int(len(arr) * 16000 / sr)).astype(np.float32)
            sr = 16000
        dur = len(arr) / sr
        if dur < min_s or dur > max_s:
            continue
        fn = f"{lang}_{len(rows):02d}.wav"
        sf.write(os.path.join(out_dir, fn), arr, sr)
        ref = ex.get("transcription") or ex.get("raw_transcription") or ""
        rows.append({"file": fn, "duration_s": round(dur, 2), "ref": ref})

    manifest = os.path.join(MANIFEST_ROOT, f"{lang}.jsonl")
    with open(manifest, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[{lang}] wrote {len(rows)} clips -> {out_dir} ; manifest -> {manifest}")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="es,en")
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()
    for lang in args.lang.split(","):
        fetch_lang(lang.strip(), args.n)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Update `bench/.gitignore`**

Replace the file contents with:

```gitignore
# Downloaded models (regenerate via the documented download command)
sherpa-onnx-*/
*.tar.bz2
# Eval audio (regenerate via `python -m bench.datasets.fleurs`)
datasets/audio/
# Legacy prototype audio dir (pre-WHISP-41)
audio_es/
```

- [ ] **Step 3: Commit**

```bash
git add bench/datasets/__init__.py bench/datasets/fleurs.py bench/.gitignore
git commit -m "feat(bench): add multi-language FLEURS dataset fetcher"
```

---

## Task 9: Engine registry + whisper.cpp bench-local adapter

**Files:**
- Create: `bench/engines/__init__.py`, `bench/engines/whispercpp.py`, `bench/engines/experimental/__init__.py`
- Test: extend `tests/bench/` via `tests/bench/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_registry.py
import pytest
from bench.engines import resolve, ENGINE_REGISTRY


def test_registry_has_core_engines():
    assert "faster-whisper" in ENGINE_REGISTRY
    assert "parakeet" in ENGINE_REGISTRY
    assert "whispercpp" in ENGINE_REGISTRY


def test_resolve_parses_engine_and_model():
    engine, model = resolve("faster-whisper:small")
    assert engine.name == "faster-whisper"
    assert model == "small"


def test_resolve_unknown_raises():
    with pytest.raises(KeyError):
        resolve("nope:x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.engines'`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/engines/experimental/__init__.py
# Escape hatch for experimental engines/hardware (e.g. Coral TPU).
# Adapters here are NOT imported by src/whisper_aloud and need not be production-quality.
```

```python
# bench/engines/whispercpp.py
"""Bench-local whisper.cpp adapter (wraps the whisper-cli binary; CPU + Vulkan).

Lives in bench/ (not src/) until promoted to a src/ STTEngine in WHISP-38.
`model` is a path to a GGUF model file. Requires `whisper-cli` on PATH or
WHISPERCPP_BIN env var pointing at it.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript


class WhisperCppEngine(STTEngine):
    name = "whispercpp"

    def __init__(self) -> None:
        self._model = None
        self._bin = os.environ.get("WHISPERCPP_BIN", "whisper-cli")

    def load(self, model: str, device: str, compute_type: str) -> None:
        if not os.path.exists(model):
            raise FileNotFoundError(f"whisper.cpp GGUF model not found: {model}")
        self._model = model

    def transcribe_numpy(self, samples, sample_rate, language=None) -> Transcript:
        if self._model is None:
            raise RuntimeError("WhisperCppEngine.load() must be called before transcribe")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name
        try:
            sf.write(wav_path, np.asarray(samples, dtype=np.float32), sample_rate)
            cmd = [self._bin, "-m", self._model, "-f", wav_path, "-nt", "-otxt", "-of", wav_path]
            if language:
                cmd += ["-l", language]
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
            txt_path = wav_path + ".txt"
            text = ""
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    text = f.read().strip()
                os.remove(txt_path)
            return Transcript(text=text)
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            streaming=False, languages="multilingual",
            native_punctuation=True, devices=["cpu", "vulkan"],
        )

    def unload(self) -> None:
        self._model = None
```

```python
# bench/engines/__init__.py
"""Engine registry: name -> factory. Resolves src/ engines + bench-local adapters."""
from __future__ import annotations

from whisper_aloud.stt.faster_whisper_engine import FasterWhisperEngine
from whisper_aloud.stt.parakeet_engine import ParakeetEngine
from bench.engines.whispercpp import WhisperCppEngine

ENGINE_REGISTRY = {
    "faster-whisper": FasterWhisperEngine,
    "parakeet": ParakeetEngine,
    "whispercpp": WhisperCppEngine,
}


def resolve(spec: str):
    """Parse 'engine:model' -> (engine_instance, model_str). Raises KeyError if unknown."""
    name, _, model = spec.partition(":")
    if name not in ENGINE_REGISTRY:
        raise KeyError(f"unknown engine '{name}'. Known: {sorted(ENGINE_REGISTRY)}")
    return ENGINE_REGISTRY[name](), model
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/engines/ tests/bench/test_registry.py
git commit -m "feat(bench): add engine registry and whisper.cpp bench adapter"
```

---

## Task 10: Runner CLI

**Files:**
- Create: `bench/runner.py`
- Test: `tests/bench/test_runner.py` (test the pure helpers; full run is integration, done in Task 11)

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_runner.py
import statistics
from bench.runner import median_rtf, pick_device, load_manifest


def test_median_rtf():
    assert median_rtf([0.15, 0.16, 0.17]) == 0.16


def test_pick_device_prefers_cuda_when_available_and_capable():
    assert pick_device(caps_devices=["cpu", "cuda"], hw_kind="gpu") == "cuda"


def test_pick_device_falls_back_to_cpu():
    assert pick_device(caps_devices=["cpu", "cuda"], hw_kind="cpu") == "cpu"


def test_pick_device_vulkan_only_engine_on_gpu_box():
    # whisper.cpp exposes cpu+vulkan; no cuda -> vulkan not chosen unless explicitly asked
    assert pick_device(caps_devices=["cpu", "vulkan"], hw_kind="gpu") == "cpu"


def test_load_manifest(tmp_path):
    m = tmp_path / "es.jsonl"
    m.write_text('{"file":"es_00.wav","duration_s":5.0,"ref":"hola"}\n')
    rows = load_manifest(m)
    assert rows[0]["ref"] == "hola"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# bench/runner.py
"""Benchmark runner CLI.

Example:
  python -m bench.runner --lang es,en \
    --engines parakeet:<model_dir>,faster-whisper:small,faster-whisper:medium \
    --device auto --reps 3 --hw-id inference01
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time

import numpy as np
import psutil
import soundfile as sf

from bench.engines import resolve
from bench.hwprobe import probe
from bench.ledger import append_row, make_row
from bench.wer import wer

HERE = os.path.dirname(__file__)
MANIFEST_ROOT = os.path.join(HERE, "datasets", "manifests")
AUDIO_ROOT = os.path.join(HERE, "datasets", "audio")
LEDGER = os.path.join(HERE, "results", "ledger.jsonl")


def median_rtf(values: list[float]) -> float:
    return round(statistics.median(values), 4)


def pick_device(caps_devices: list[str], hw_kind: str) -> str:
    """auto policy: cuda if engine supports it AND box has a GPU; else cpu.
    Non-cuda accelerators (vulkan/openvino/coral) are opt-in via --device, not auto."""
    if hw_kind == "gpu" and "cuda" in caps_devices:
        return "cuda"
    return "cpu"


def load_manifest(path) -> list:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _dirsize_mb(path) -> float | None:
    if not os.path.isdir(path):
        return None
    total = sum(os.path.getsize(os.path.join(r, fn))
                for r, _, fs in os.walk(path) for fn in fs)
    return round(total / 1e6, 1)


def run_combo(engine, model, lang, device, reps, hw, git_sha):
    manifest = os.path.join(MANIFEST_ROOT, f"{lang}.jsonl")
    audio_dir = os.path.join(AUDIO_ROOT, lang)
    clips = load_manifest(manifest)
    engine.load(model, device, "int8")

    # WER + per-clip times (deterministic transcription; time the first rep here)
    rep_times = []
    wers, total_audio = [], 0.0
    rss0 = psutil.Process().memory_info().rss / 1e6
    for rep in range(reps):
        t_rep = 0.0
        for c in clips:
            arr, sr = sf.read(os.path.join(audio_dir, c["file"]), dtype="float32")
            t = time.time()
            out = engine.transcribe_numpy(arr, sr, language=lang)
            t_rep += time.time() - t
            if rep == 0:
                wers.append(wer(c["ref"], out.text))
                total_audio += c["duration_s"]
        rep_times.append(t_rep)
    rss_peak = psutil.Process().memory_info().rss / 1e6

    metrics = {
        "wer": round(sum(wers) / len(wers), 4),
        "rtf": median_rtf([rt / total_audio for rt in rep_times]),
        "peak_rss_mb": round(rss_peak, 1),
        "rss_delta_mb": round(rss_peak - rss0, 1),
        "peak_vram_mb": None,
        "model_size_mb": _dirsize_mb(model),
    }
    engine.unload()
    return make_row(lang=lang, dataset=f"fleurs/{lang}", n_clips=len(clips),
                    engine=engine.name, model=os.path.basename(model.rstrip("/")) or model,
                    compute_type="int8", device=device, hw=hw, metrics=metrics, git_sha=git_sha)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="es,en")
    ap.add_argument("--engines", required=True,
                    help="comma list of engine:model, e.g. parakeet:/path,faster-whisper:small")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "vulkan"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--hw-id", default=None)
    args = ap.parse_args()

    hw = probe(hw_id=args.hw_id)
    git_sha = _git_sha()
    print(f"hw: {hw}")
    for spec in args.engines.split(","):
        engine, model = resolve(spec.strip())
        for lang in args.lang.split(","):
            lang = lang.strip()
            device = (pick_device(engine.capabilities.devices, hw["kind"])
                      if args.device == "auto" else args.device)
            try:
                row = run_combo(engine, model, lang, device, args.reps, hw, git_sha)
            except FileNotFoundError as e:
                print(f"SKIP {spec} {lang}: {e}")
                continue
            append_row(LEDGER, row)
            print(f"[{row['engine']}:{row['model']} {lang}/{device}] "
                  f"WER={row['metrics']['wer']:.1%} RTF={row['metrics']['rtf']:.3f}")
    print(f"appended to {LEDGER}. Run `python -m bench.report` to regenerate MATRIX.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_runner.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/runner.py tests/bench/test_runner.py
git commit -m "feat(bench): add runner CLI orchestrating engines×langs into the ledger"
```

---

## Task 11: First real run (CPU) + migrate WHISP-33, generate MATRIX.md

**Files:**
- Create: `bench/requirements-bench.txt`, `bench/README.md`
- Create (generated): `bench/results/ledger.jsonl`, `bench/MATRIX.md`
- Remove: `bench/run_bench.py`, `bench/fetch_audio.py`, `bench/RESULTS_es_cpu.md`, `bench/results/*.json`, `bench/refs.jsonl`

- [ ] **Step 1: Write `bench/requirements-bench.txt`**

```
jiwer
datasets
sherpa-onnx
psutil
soundfile
scipy
```

- [ ] **Step 2: Fetch datasets and run the CPU suite**

```bash
source ~/.venvs/whisper_aloud/bin/activate
pip install -r bench/requirements-bench.txt
python -m bench.datasets.fleurs --lang es,en --n 40
# Parakeet model (640MB, gitignored) — download if not present:
test -d bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 || (
  curl -sL -o m.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2 &&
  tar xjf m.tar.bz2 -C bench/ && rm m.tar.bz2 )
python -m bench.runner --lang es,en \
  --engines "parakeet:bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8,faster-whisper:small,faster-whisper:medium" \
  --device cpu --reps 3 --hw-id i7-1360P
python -m bench.report
```

Expected: `bench/results/ledger.jsonl` has 6 rows (3 engines × 2 langs), `bench/MATRIX.md` regenerated.

- [ ] **Step 3: Remove superseded prototype files**

```bash
git rm bench/run_bench.py bench/fetch_audio.py bench/RESULTS_es_cpu.md bench/refs.jsonl
git rm bench/results/fw-small-int8.json bench/results/fw-medium-int8.json bench/results/parakeet-tdt-0.6b-v3.json
```

- [ ] **Step 4: Write `bench/README.md`**

```markdown
# WhisperAloud STT Benchmark

Reproducible performance matrix: `language × model × engine × hardware`.
Metrics: WER, RTF (proc/audio), peak RAM/VRAM, model size, load time — with full
hardware provenance. See `MATRIX.md` for current results.

## Methodology & caveats
- Dataset: FLEURS (read-speech, CC-BY). Absolute WER is optimistic vs real mic
  dictation; **relative ordering across engines is the reliable signal.**
- WER normalization is identical for all engines (lowercase, no punctuation).
- RTF is the median of `--reps` (default 3) timed passes.
- GPU numbers are machine-specific — always read `hw.id`. Nothing here is vendor-reported.

## Reproduce
\`\`\`bash
source ~/.venvs/whisper_aloud/bin/activate
pip install -r bench/requirements-bench.txt
python -m bench.datasets.fleurs --lang es,en --n 40
python -m bench.runner --engines "faster-whisper:small,parakeet:<model_dir>" --device auto
python -m bench.report   # regenerates MATRIX.md
\`\`\`

## Hardware setup notes
- **CPU:** works out of the box.
- **NVIDIA:** install CUDA-enabled ctranslate2 + `sherpa-onnx` GPU build; run with `--device cuda`. Or use `bench/scripts/run_remote.sh` against inference01.
- **Intel iGPU (Vulkan):** build whisper.cpp with `-DGGML_VULKAN=1`, set `WHISPERCPP_BIN`, run `whispercpp:<gguf>` with `--device vulkan`.
- **AMD ROCm / Coral TPU:** schema-ready, not yet measured. Add an adapter under `bench/engines/experimental/` and a `device` value.

## Layout
- `src/whisper_aloud/stt/` — shared `STTEngine` interface + production engines.
- `bench/engines/` — registry + bench-local adapters (whisper.cpp) + `experimental/`.
- `bench/results/ledger.jsonl` — append-only results (committed). `MATRIX.md` — generated.
```

- [ ] **Step 5: Commit**

```bash
git add bench/requirements-bench.txt bench/README.md bench/MATRIX.md bench/results/ledger.jsonl bench/.gitignore
git add -u bench/
git commit -m "feat(bench): first CPU matrix run, migrate WHISP-33 results, drop prototype"
```

---

## Task 12: Remote NVIDIA run script (inference01)

**Files:**
- Create: `bench/scripts/run_remote.sh`

This is run manually; no unit test. It must fail loudly if the remote lacks CUDA rather than
silently producing CPU numbers labeled as GPU.

- [ ] **Step 1: Write the script**

```bash
# bench/scripts/run_remote.sh
#!/usr/bin/env bash
# Run the STT benchmark on a remote NVIDIA host and merge results back.
# Usage: bench/scripts/run_remote.sh <ssh-host> [remote-venv-path]
set -euo pipefail

HOST="${1:?usage: run_remote.sh <ssh-host> [remote-venv]}"
RVENV="${2:-~/.venvs/whisper_aloud_bench}"
RDIR="~/whisperaloud-bench"

echo ">> syncing bench code + manifests to $HOST (no audio/models)"
rsync -az --delete \
  --exclude 'datasets/audio' --exclude 'sherpa-onnx-*' --exclude 'results' \
  ./bench/ "$HOST:$RDIR/bench/"
rsync -az ./src/ "$HOST:$RDIR/src/"

echo ">> remote setup + GPU sanity check (fails loudly if no CUDA)"
ssh "$HOST" bash -lc "'
  set -euo pipefail
  cd $RDIR
  python3 -m venv $RVENV 2>/dev/null || true
  source $RVENV/bin/activate
  pip install -q -r bench/requirements-bench.txt
  pip install -q ctranslate2  # CUDA build expected on a GPU host
  nvidia-smi --query-gpu=name --format=csv,noheader || { echo \"NO NVIDIA GPU on $HOST\"; exit 3; }
  export PYTHONPATH=$RDIR/src
  python -m bench.datasets.fleurs --lang es,en --n 40
  test -d bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 || (
    curl -sL -o m.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2 &&
    tar xjf m.tar.bz2 -C bench/ && rm m.tar.bz2 )
  python -m bench.runner --lang es,en \
    --engines \"parakeet:bench/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8,faster-whisper:small,faster-whisper:medium\" \
    --device cuda --reps 3 --hw-id $HOST
'"

echo ">> pulling remote ledger rows and merging into local ledger"
ssh "$HOST" "cat $RDIR/bench/results/ledger.jsonl" >> bench/results/ledger.jsonl
python -m bench.report
echo ">> done. Review bench/results/ledger.jsonl for duplicates before committing."
```

- [ ] **Step 2: Make executable and smoke-check syntax**

Run: `chmod +x bench/scripts/run_remote.sh && bash -n bench/scripts/run_remote.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add bench/scripts/run_remote.sh
git commit -m "feat(bench): add remote NVIDIA benchmark runner for inference01"
```

- [ ] **Step 4 (manual, requires SSH): run on inference01 and regenerate matrix**

Run: `bench/scripts/run_remote.sh inference01` (adjust host alias), then review the ledger for
duplicate rows, `python -m bench.report`, and commit `bench/results/ledger.jsonl` + `bench/MATRIX.md`.

---

## Self-Review

**Spec coverage:**
- §2 shared interface + escape hatch → Tasks 1–3 (`src/` engines), Task 9 (registry + whisper.cpp bench-local + `experimental/`). ✅
- §3 layout → all files mapped across Tasks 1–12. ✅
- §4 `STTEngine` → Task 1 (exact signatures reused in Tasks 2,3,9). ✅
- §5 hwprobe → Task 5. ✅
- §6 ledger schema → Task 6 (`make_row` keys match spec). ✅
- §7 runner CLI → Task 10 (flags match spec). ✅
- §8 iteration-1 hardware → Task 11 (CPU), Task 12 (NVIDIA), Intel iGPU via whisper.cpp documented in Task 11 README. ✅
- §9 run_remote.sh → Task 12. ✅
- §10 testing → Tasks 1–10 each include tests; engine smoke tests use mock injection (CI-safe, no model needed). ✅
- §11 reproducibility/README/MATRIX → Task 11. ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✅

**Type consistency:** `Transcript(text, segments)`, `EngineCapabilities(streaming, languages, native_punctuation, devices)`, `STTEngine.load/transcribe_numpy/capabilities/transcribe_file/unload`, `make_row(...)` keys, `resolve()→(engine,model)`, `probe(hw_id)`, `generate_matrix(rows)`, `median_rtf/pick_device/load_manifest` — names consistent across Tasks 1–12. ✅

**Note on Intel iGPU (Vulkan):** measured run depends on a successful whisper.cpp Vulkan build; if it stalls, Task 11/README ship CPU+NVIDIA and leave the Vulkan row as a follow-up (per spec §12). The harness already supports `--device vulkan` and `whispercpp:` specs, so no code change is needed when the build is ready.
