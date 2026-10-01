# WHISP-46 — E2E Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pytest e2e harness described in the design doc — a real daemon subprocess on
a private `dbus-run-session` bus, fake audio, and a fake deterministic STT engine — so WHISP-47
can write real scenario tests against it. **This plan belongs to a follow-up ticket** (WHISP-47
is expected to execute it); WHISP-46 itself produced only the design doc and this plan, per its
brief (design-only, no production/test code changes).

**Architecture:** One new env var (`WHISPER_ALOUD_TEST_MODE`) gates two tiny production
read-sites (`GainController` wpctl-probe skip, `stt/catalog.py` conditional `"fake"` entry). A
new `FakeSpeechEngine` and a new `_FakeInputStream` are wired through the *existing*
`WHISPER_ALOUD_MODEL_ENGINE`/new `WHISPER_ALOUD_FAKE_AUDIO_WAV` env vars — no D-Bus contract
changes, no `to_dict()`/`GetConfig()` changes. `tests/e2e/` gets its own `conftest.py` that
restores the real (private) `DBUS_SESSION_BUS_ADDRESS` `dbus-run-session` provided, which the
root `conftest.py` otherwise clobbers for the unit suite's isolation.

**Spec:** `docs/superpowers/specs/2026-10-01-whisp46-e2e-harness-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/whisper_aloud/service/media_control.py` | `GainController.__post_init__` skips the `wpctl` probe under `WHISPER_ALOUD_TEST_MODE=1` | Modify |
| `src/whisper_aloud/stt/catalog.py` | conditional hidden `"fake"` `ModelEntry` + `ENGINES["fake"]`, test-mode only | Modify |
| `src/whisper_aloud/stt/fake_engine.py` | `FakeSpeechEngine` (Protocol-conforming, deterministic, latency/error injection) | Create |
| `src/whisper_aloud/stt/engine.py` | `create_engine` dispatches `"fake"` → `FakeSpeechEngine` | Modify |
| `src/whisper_aloud/audio/recorder.py` | `AudioRecorder` uses a `_FakeInputStream` instead of `sd.InputStream` when `WHISPER_ALOUD_FAKE_AUDIO_WAV` is set (test mode only) | Modify |
| `conftest.py` | capture the real `DBUS_SESSION_BUS_ADDRESS` before poisoning it, expose via `_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS` | Modify |
| `tests/e2e/__init__.py` | package marker | Create |
| `tests/e2e/conftest.py` | bus restoration, `daemon`/`client`/`audio_fixture`/`signal_capture` fixtures, `real_model` skip hook | Create |
| `tests/e2e/fixtures/audio/*.wav`, `*.txt` | synthetic deterministic audio fixtures + expected transcripts | Create |
| `scripts/test_e2e.sh` | `dbus-run-session`-wrapped e2e runner, self-re-exec guard | Create |
| `pyproject.toml` | register `e2e` and `real_model` markers | Modify |
| `tests/stt/test_fake_engine.py` | unit tests for `FakeSpeechEngine` | Create |
| `tests/test_audio_recorder_fake_stream.py` | unit tests for `_FakeInputStream` / the recorder's fake-audio branch | Create |
| `tests/stt/test_catalog.py` | test for conditional `"fake"` entry visibility | Modify |
| `tests/test_media_control.py` | test for `GainController` test-mode gating | Modify |
| `tests/e2e/test_harness_smoke.py` | first real end-to-end smoke test proving the whole stack (daemon up, client connects, Quit shuts it down cleanly) | Create |

**Invariants:** no change to the D-Bus introspection XML / `tests/contracts/org.fede.whisperaloud.Control.xml`
snapshot (nothing in this plan adds/removes a D-Bus method or signal); `WHISPER_ALOUD_TEST_MODE`
unset reproduces today's behavior bit-for-bit in every modified file; `scripts/test_ci.sh`'s
existing profile is untouched and stays green throughout.

Run pytest as `python -m pytest` inside `~/.venvs/whisper_aloud` (system pytest lacks deps). Every
task's tests must pass under `git merge-base --is-ancestor f839796 HEAD` (already true on this
branch, which forked from current `master`).

---

## Task 1: Capture the real D-Bus address before the unit-test conftest poisons it

**Files:**
- Modify: `conftest.py` (root)
- Test: `tests/test_conftest_dbus_capture.py` (new, tiny)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_conftest_dbus_capture.py
"""Root conftest must preserve the pre-poisoning DBUS_SESSION_BUS_ADDRESS so
tests/e2e/conftest.py can restore it. Runs conftest.py's capture logic in a
subprocess with a controlled starting env, since the running test process
already has conftest.py's module-level code executed once at collection."""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_conftest_preserves_real_dbus_address_for_restoration():
    env = dict(os.environ)
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/fake-real-bus-for-test"
    code = (
        "import conftest, os; "
        "print(os.environ['_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "unix:path=/tmp/fake-real-bus-for-test"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_conftest_dbus_capture.py -v`
Expected: FAIL — `KeyError: '_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS'` (root `conftest.py` doesn't
set it yet).

- [ ] **Step 3: Implement**

In `conftest.py`, replace:

```python
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/whisper-aloud-tests-bus"
```

with:

```python
os.environ["_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS"] = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/whisper-aloud-tests-bus"
```

Add a one-line comment pointing at `tests/e2e/conftest.py` as the consumer.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_conftest_dbus_capture.py -v`
Expected: PASS. Then run `python -m pytest -q` (full suite) to confirm no regression — this
touches a file every test implicitly depends on.

- [ ] **Step 5: Commit**

`test(conftest): preserve real DBUS_SESSION_BUS_ADDRESS before isolation poisons it`

---

## Task 2: `WHISPER_ALOUD_TEST_MODE` gate in `GainController`

**Files:**
- Modify: `src/whisper_aloud/service/media_control.py`
- Test: `tests/test_media_control.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_gain_controller_skips_wpctl_probe_in_test_mode(monkeypatch):
    """WHISPER_ALOUD_TEST_MODE=1 must prevent wpctl detection even if present."""
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl")
    controller = GainController()
    assert controller._wpctl is None


def test_gain_controller_restore_from_crash_file_is_noop_in_test_mode(monkeypatch, tmp_path):
    """Even if a real recovery file exists, test mode must never read/act on it."""
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl")
    controller = GainController()
    # Should not raise, should not touch _RECOVERY_FILE at all.
    controller.restore_from_crash_file()
    assert controller._snapshot is None


def test_gain_controller_probes_wpctl_without_test_mode(monkeypatch):
    """Unset WHISPER_ALOUD_TEST_MODE reproduces today's behavior exactly."""
    monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl" if name == "wpctl" else None)
    controller = GainController()
    assert controller._wpctl == "/usr/bin/wpctl"
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_media_control.py -k gain_controller_skips -v`
Expected: FAIL — `_wpctl` is currently `/usr/bin/wpctl` regardless of env.

- [ ] **Step 3: Implement**

In `GainController.__post_init__`:

```python
def __post_init__(self) -> None:
    if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
        self._wpctl = None
        logger.info("GainController: WHISPER_ALOUD_TEST_MODE=1, gain control disabled")
        return
    self._wpctl = shutil.which("wpctl")
    ...  # unchanged
```

(`os` is already imported in `media_control.py` — confirm before adding the import.) No change
needed to `restore_from_crash_file()`/`raise_to()`/`restore()` — they already short-circuit on
`if not self._wpctl`.

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_media_control.py -v` — all pass, including the pre-existing
ones (confirms the "without test mode" default path is untouched).

- [ ] **Step 5: Commit**

`fix(media-control): gate wpctl probe behind WHISPER_ALOUD_TEST_MODE`

---

## Task 3: Conditional `"fake"` catalog entry

**Files:**
- Modify: `src/whisper_aloud/stt/catalog.py`
- Test: `tests/stt/test_catalog.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_fake_engine_absent_by_default(monkeypatch):
    monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
    # Re-import catalog fresh to re-run its module-level conditional.
    import importlib
    from whisper_aloud.stt import catalog as catalog_module
    importlib.reload(catalog_module)
    assert "fake" not in catalog_module.engines()
    assert catalog_module.models_for("fake") == []
    assert "fake" not in catalog_module.engine_models()


def test_fake_engine_present_in_test_mode(monkeypatch):
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    import importlib
    from whisper_aloud.stt import catalog as catalog_module
    importlib.reload(catalog_module)
    try:
        assert "fake" in catalog_module.engines()
        assert catalog_module.get("fake", "fake") is not None
        assert "fake" in catalog_module.engine_models()
    finally:
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        importlib.reload(catalog_module)  # restore module state for later tests
```

Note: reloading the module is necessary and sufficient here *because* registration is a
module-import-time decision (see design rationale) — this also documents that contract for
future readers.

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/stt/test_catalog.py -k fake_engine -v`
Expected: FAIL — `"fake"` not recognized at all today.

- [ ] **Step 3: Implement**

In `stt/catalog.py`, after the `_ENTRIES` tuple definition, before `def entries()`:

```python
import os

if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
    _ENTRIES = _ENTRIES + (
        ModelEntry(
            engine="fake",
            name="fake",
            label="Fake (test only)",
            size_mb=0,
            languages=None,
            streaming=False,
            license="N/A",
            devices=("cpu",),
            tier="fast",
            recommended=False,
            notes="Deterministic test double — reads a sidecar .txt next to a "
                  "fixture WAV. Never shown to real users.",
        ),
    )
    ENGINES["fake"] = "Fake (test only)"
```

(Move the `import os` to the top of the module with the other imports rather than inline — shown
inline above only to pinpoint the insertion context.)

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/stt/test_catalog.py -v` — all pass, including pre-existing catalog
tests (confirms default/production catalog is byte-identical when the env var is unset).
Also run: `python -m pytest tests/test_config.py -v` (config.py imports `ENGINE_MODELS` from this
module at import time — confirm no change in the common no-env-var case).

- [ ] **Step 5: Commit**

`feat(stt): register a hidden "fake" catalog entry under WHISPER_ALOUD_TEST_MODE`

---

## Task 4: `FakeSpeechEngine`

**Files:**
- Create: `src/whisper_aloud/stt/fake_engine.py`
- Modify: `src/whisper_aloud/stt/engine.py` (`create_engine` dispatch)
- Test: `tests/stt/test_fake_engine.py`, `tests/stt/test_engine_factory.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# tests/stt/test_fake_engine.py
import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import ConfigurationError, TranscriptionError
from whisper_aloud.stt.fake_engine import FakeSpeechEngine


def _config():
    c = WhisperAloudConfig()
    c.model.engine = "fake"
    c.model.name = "fake"
    return c


def test_reads_sidecar_txt(monkeypatch, tmp_path):
    wav = tmp_path / "hello.wav"
    wav.write_bytes(b"RIFF....")  # engine never parses the WAV itself
    (tmp_path / "hello.txt").write_text("hello world")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    result = engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))
    assert result.text == "hello world"


def test_missing_sidecar_raises(monkeypatch, tmp_path):
    wav = tmp_path / "missing.wav"
    wav.write_bytes(b"")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    with pytest.raises(ConfigurationError, match="sidecar"):
        engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))


def test_latency_injection(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"")
    (tmp_path / "a.txt").write_text("a")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S", "0.05")

    import time
    engine = FakeSpeechEngine(_config())
    engine.load_model()
    start = time.monotonic()
    engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))
    assert time.monotonic() - start >= 0.05


def test_error_injection(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"")
    (tmp_path / "a.txt").write_text("a")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_ENGINE_ERROR", "boom")

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    with pytest.raises(TranscriptionError, match="boom"):
        engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))


def test_cancel_and_lifecycle_flags():
    engine = FakeSpeechEngine(_config())
    assert engine.is_loaded is False
    engine.load_model()
    assert engine.is_loaded is True
    assert engine.is_cancelling is False
    engine.cancel_transcription()
    assert engine.is_cancelling is True
    engine.unload_model()
    assert engine.is_loaded is False
```

```python
# tests/stt/test_engine_factory.py — append
def test_create_engine_dispatches_fake(monkeypatch):
    from whisper_aloud.stt.engine import create_engine
    config = WhisperAloudConfig()
    config.model.engine = "fake"
    config.model.name = "fake"
    from whisper_aloud.stt.fake_engine import FakeSpeechEngine
    assert isinstance(create_engine(config), FakeSpeechEngine)
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/stt/test_fake_engine.py tests/stt/test_engine_factory.py -k fake -v`
Expected: FAIL — `ModuleNotFoundError: whisper_aloud.stt.fake_engine`.

- [ ] **Step 3: Implement `FakeSpeechEngine`**

```python
# src/whisper_aloud/stt/fake_engine.py
"""Deterministic test-only speech engine (WHISP-46/47 e2e harness).

Never reachable outside WHISPER_ALOUD_TEST_MODE=1 — see stt/catalog.py's
conditional registration and config.validate()'s engine allowlist, which is
derived from the catalog. This module itself has no test-mode guard of its
own; the guard lives at the registration boundary (catalog + create_engine),
consistent with how every other engine is just "reachable or not" via config.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..exceptions import ConfigurationError, TranscriptionError

if TYPE_CHECKING:
    import numpy as np

    from ..config import WhisperAloudConfig
    from ..transcriber import TranscriptionResult


class FakeSpeechEngine:
    """Deterministic SpeechEngine double. Reads the expected transcript from a
    sidecar `<wav>.txt` next to WHISPER_ALOUD_FAKE_AUDIO_WAV, re-read fresh on
    every call (not cached at construction) so a test can rewrite it between
    daemon calls if it ever needs to."""

    def __init__(self, config: "WhisperAloudConfig") -> None:
        self._config = config
        self._loaded = False
        self._cancelling = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_cancelling(self) -> bool:
        return self._cancelling

    def load_model(self) -> None:
        self._loaded = True

    def unload_model(self) -> None:
        self._loaded = False

    def cancel_transcription(self) -> None:
        self._cancelling = True

    def transcribe_numpy(
        self, audio: "np.ndarray", sample_rate: int = 16000, **kwargs
    ) -> "TranscriptionResult":
        from ..transcriber import TranscriptionResult  # local import, avoid cycle

        latency = os.environ.get("WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S")
        if latency:
            time.sleep(float(latency))

        error = os.environ.get("WHISPER_ALOUD_FAKE_ENGINE_ERROR")
        if error:
            raise TranscriptionError(error)

        wav_path = os.environ.get("WHISPER_ALOUD_FAKE_AUDIO_WAV")
        if not wav_path:
            raise ConfigurationError(
                "FakeSpeechEngine requires WHISPER_ALOUD_FAKE_AUDIO_WAV to be set"
            )
        sidecar = Path(wav_path).with_suffix(".txt")
        if not sidecar.exists():
            raise ConfigurationError(
                f"FakeSpeechEngine: no sidecar transcript at {sidecar}"
            )
        text = sidecar.read_text().strip()

        duration = len(audio) / sample_rate if sample_rate else 0.0
        self._cancelling = False
        return TranscriptionResult(
            text=text,
            language=self._config.transcription.language or "en",
            duration=duration,
            confidence=1.0,
            processing_time=float(latency) if latency else 0.0,
        )

    def transcribe_file(self, audio_path: str, **kwargs) -> "TranscriptionResult":
        import numpy as np

        return self.transcribe_numpy(np.zeros(1, dtype="float32"), **kwargs)
```

Check `TranscriptionResult`'s actual constructor signature in `src/whisper_aloud/transcriber.py`
before finalizing field names (`duration`/`confidence`/`processing_time`/`language`/`text`) —
match it exactly rather than guessing; adjust the dataclass call above if field names differ.

- [ ] **Step 4: Wire `create_engine`**

In `stt/engine.py`, add before the `ConfigurationError` backstop:

```python
    if engine == "fake":
        from .fake_engine import FakeSpeechEngine

        return FakeSpeechEngine(config)
```

- [ ] **Step 5: Verify**

Run: `python -m pytest tests/stt/test_fake_engine.py tests/stt/test_engine_factory.py -v`
Expected: PASS. Then `python -m pytest tests/test_config.py tests/stt/test_catalog.py -v` to
confirm Task 3 + this task compose correctly (engine="fake" validates only when the catalog
entry from Task 3 is present).

- [ ] **Step 6: Commit**

`feat(stt): add deterministic FakeSpeechEngine for the e2e harness`

---

## Task 5: Fake audio input stream in `AudioRecorder`

**Files:**
- Modify: `src/whisper_aloud/audio/recorder.py`
- Test: `tests/test_audio_recorder_fake_stream.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audio_recorder_fake_stream.py
import numpy as np
import pytest
import soundfile as sf

from whisper_aloud.audio.recorder import AudioRecorder
from whisper_aloud.config import AudioConfig


def _write_wav(path, seconds=0.5, sample_rate=16000, freq=440.0):
    t = np.linspace(0, seconds, int(seconds * sample_rate), endpoint=False)
    data = (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), data, sample_rate)
    return data


def test_fake_stream_feeds_callback_without_real_device(monkeypatch, tmp_path):
    """No real sd.InputStream/device query happens when the fake-audio env is set."""
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path)
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav_path))

    import sounddevice as sd
    def _boom(*a, **k):
        raise AssertionError("real sd.InputStream must not be constructed in fake-audio mode")
    monkeypatch.setattr(sd, "InputStream", _boom)

    recorder = AudioRecorder(AudioConfig())
    recorder.start()
    import time
    time.sleep(0.6)  # let the fake stream push the whole 0.5s fixture
    audio = recorder.stop()
    assert len(audio) > 0


def test_fake_stream_pads_silence_past_eof(monkeypatch, tmp_path):
    """A recording held open longer than the fixture gets trailing silence, not a loop."""
    wav_path = tmp_path / "short.wav"
    _write_wav(wav_path, seconds=0.1)
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav_path))

    recorder = AudioRecorder(AudioConfig())
    recorder.start()
    import time
    time.sleep(0.4)
    audio = recorder.stop()
    # At 16kHz, 0.4s should be fully populated (padded), not truncated at ~0.1s.
    assert len(audio) >= int(0.3 * 16000)


def test_without_env_var_uses_real_sounddevice(monkeypatch):
    """Default path is untouched when the fake-audio env var is absent."""
    monkeypatch.delenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", raising=False)
    recorder = AudioRecorder(AudioConfig())
    assert recorder._fake_wav_path is None
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_audio_recorder_fake_stream.py -v`
Expected: FAIL — `AudioRecorder` has no `_fake_wav_path` attribute yet, and `start()` always
calls `DeviceManager.validate_device`/`sd.InputStream`.

- [ ] **Step 3: Implement**

In `audio/recorder.py`:

```python
class _FakeInputStream:
    """Drop-in stand-in for sd.InputStream that feeds a pre-loaded WAV's
    samples to the recorder's real audio callback on a background thread,
    chunked at the configured blocksize, padding with silence past EOF
    instead of looping (see design doc §5 for rationale)."""

    def __init__(self, samples, blocksize, callback):
        self._samples = samples
        self._blocksize = blocksize
        self._callback = callback
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        pos = 0
        n = len(self._samples)
        period = self._blocksize / 16000.0
        while not self._stop_event.is_set():
            if pos + self._blocksize <= n:
                chunk = self._samples[pos:pos + self._blocksize]
                pos += self._blocksize
            elif pos < n:
                chunk = np.concatenate([
                    self._samples[pos:],
                    np.zeros(self._blocksize - (n - pos), dtype=np.float32),
                ])
                pos = n
            else:
                chunk = np.zeros(self._blocksize, dtype=np.float32)
            self._callback(chunk.reshape(-1, 1), self._blocksize, None, None)
            time.sleep(period)

    def stop(self):
        self._stop_event.set()

    def close(self):
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
```

In `AudioRecorder.__init__`, add:

```python
        self._fake_wav_path = (
            os.environ.get("WHISPER_ALOUD_FAKE_AUDIO_WAV")
            if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1"
            else None
        )
```

(add `import os` at the top if not already present — it is not, currently). In `start()`, branch
before the `DeviceManager.validate_device(...)` call:

```python
        if self._fake_wav_path:
            self._start_fake(device_id)
            return
```

with a new private method:

```python
    def _start_fake(self, device_id) -> None:
        import soundfile as sf

        self._frames = []
        self._level_meter.reset()
        self._start_time = time.time()

        data, file_rate = sf.read(self._fake_wav_path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = AudioProcessor.stereo_to_mono(data)
        if file_rate != self.config.sample_rate:
            data = AudioProcessor.resample(data, file_rate, self.config.sample_rate)

        blocksize = int(self.config.chunk_duration * self.config.sample_rate)
        self._stream = _FakeInputStream(data, blocksize, self._audio_callback)
        self._stream.start()
        self._set_state(RecordingState.RECORDING)
        logger.info(f"Fake recording started from {self._fake_wav_path}")
```

`stop()`/`cancel()` need no changes — they already only call `.stop()`/`.close()` on
`self._stream` and read `self._frames`, both of which `_FakeInputStream` + the real callback
populate identically to the production path.

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/test_audio_recorder_fake_stream.py -v`. Then run the full existing
`tests/test_audio_recorder.py` (if present under that or a similar name — check
`tests/test_audio*.py`) to confirm the default (no env var) path is unchanged. Then
`python -m pytest tests/test_daemon.py -v` (daemon owns an `AudioRecorder` — confirm no import
or construction regression).

- [ ] **Step 5: Commit**

`feat(audio): add fake WAV-backed input stream for the e2e harness`

---

## Task 6: `tests/e2e/` package, fixtures, and bus restoration

**Files:**
- Create: `tests/e2e/__init__.py`, `tests/e2e/conftest.py`
- Create: `tests/e2e/fixtures/audio/silence.wav` + `.txt`, `tests/e2e/fixtures/audio/tone.wav` + `.txt`
  (generated by a small `tests/e2e/fixtures/generate_fixtures.py` script, committed alongside its
  output so CI doesn't need to regenerate them)
- Modify: `pyproject.toml` (register `e2e`, `real_model` markers)

- [ ] **Step 1: Write the failing test (harness self-check)**

```python
# tests/e2e/test_harness_smoke.py
"""First e2e test: proves the harness itself works end-to-end before WHISP-47
builds scenario tests on top of it."""
import pytest

pytestmark = pytest.mark.e2e


def test_daemon_starts_and_responds_to_status(client):
    status = client.get_status()
    assert status["state"] == "idle"
    assert status["hotkey_backend"] == "none"


def test_daemon_stops_cleanly_via_quit(daemon):
    handle = daemon()
    assert handle.proc.poll() is None  # still running
    handle.stop()
    assert handle.proc.poll() is not None  # exited


def test_fake_audio_round_trip(client, audio_fixture):
    fixture = audio_fixture("tone")
    assert client.start_recording() is True
    import time
    time.sleep(0.3)
    text = client.stop_recording()
    assert text == "transcribing"  # StopRecording's immediate return value
```

- [ ] **Step 2: Run to verify collection/setup failures**

Run: `./scripts/test_e2e.sh -k test_harness_smoke -v` (does not exist yet — this step is really
"attempt a manual run and confirm it fails for the *expected* reason," i.e. missing fixtures, not
an unrelated error): `dbus-run-session -- python -m pytest -m e2e tests/e2e -v`
Expected: FAIL — no `client`/`daemon`/`audio_fixture` fixtures defined yet.

- [ ] **Step 3: Implement `tests/e2e/conftest.py`**

Follow the signatures in the design doc §7 exactly (`DaemonHandle`, `daemon`, `client`,
`audio_fixture`, `SignalCapture`, `signal_capture`, `_attach_daemon_log_on_failure`). Key
implementation points to get right:

- At module import, restore the real bus address:
  ```python
  _real = os.environ.get("_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS", "")
  if not _real:
      pytest.exit(
          "tests/e2e requires running under dbus-run-session "
          "(use scripts/test_e2e.sh, not pytest directly)",
          returncode=1,
      )
  os.environ["DBUS_SESSION_BUS_ADDRESS"] = _real
  ```
- `daemon()`'s `_start` builds the env dict from the design doc §4 table, applies
  `config_overrides` on top (each key becomes `f"WHISPER_ALOUD_{key}"`), creates a fresh
  `tmp_path`-scoped `HOME`/XDG tree, spawns
  `Popen([sys.executable, "-m", "whisper_aloud", "--daemon"], env=env, stdout=PIPE, stderr=STDOUT, text=True)`,
  then polls `bus.get("org.fede.whisperaloud")` in a bounded retry loop (e.g. 50 × 100 ms) before
  returning the `DaemonHandle`; registers `request.addfinalizer(handle.stop)`.
- `DaemonHandle.stop()` implements the Quit → wait → terminate → wait → kill ladder from design
  doc §3.
- `audio_fixture()` resolves `tests/e2e/fixtures/audio/<name>.wav`/`.txt` relative to this
  conftest's directory; raises a clear `pytest.fail` (not a bare `FileNotFoundError`) if the
  sidecar is missing.
- `signal_capture` wraps `client._proxy` signal connection with a `GLib.MainContext` pump loop,
  per design doc §7.
- `real_model` skip: a `pytest_collection_modifyitems(config, items)` hook that checks a cache
  probe (reuse whatever `stt/model_download.py` exposes for "is this model cached" — inspect it
  during this task rather than re-deriving the logic) and applies `item.add_marker(pytest.mark.skip(...))`
  for any item marked `real_model` when the configured model isn't cached.

- [ ] **Step 4: Generate fixture WAVs**

```python
# tests/e2e/fixtures/generate_fixtures.py — run once, commit its output
import numpy as np
import soundfile as sf
from pathlib import Path

HERE = Path(__file__).parent / "audio"
HERE.mkdir(exist_ok=True)

sf.write(HERE / "silence.wav", np.zeros(16000 // 2, dtype=np.float32), 16000)
(HERE / "silence.txt").write_text("")

t = np.linspace(0, 1.0, 16000, endpoint=False)
tone = (0.2 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
sf.write(HERE / "tone.wav", tone, 16000)
(HERE / "tone.txt").write_text("the quick brown fox")
```

Run it once (`python tests/e2e/fixtures/generate_fixtures.py`) and commit the resulting `.wav`/
`.txt` files alongside the generator script (so CI doesn't regenerate binary fixtures on every
run, and diffs are reviewable).

- [ ] **Step 5: Register markers**

In `pyproject.toml`'s `markers` list, add:

```
    "e2e: Full daemon-subprocess tests on a private dbus-run-session bus (see tests/e2e/).",
    "real_model: Opt-in e2e tests against a real STT model; skipped unless the model is cached.",
```

- [ ] **Step 6: `scripts/test_e2e.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

if [ "${_WHISPER_ALOUD_UNDER_DBUS_RUN_SESSION:-}" != "1" ]; then
  exec env _WHISPER_ALOUD_UNDER_DBUS_RUN_SESSION=1 dbus-run-session -- "$0" "$@"
fi

python -m pytest -m e2e tests/e2e "$@"
```

`chmod +x scripts/test_e2e.sh`.

- [ ] **Step 7: Verify**

Run: `./scripts/test_e2e.sh -v`
Expected: all three smoke tests in `tests/e2e/test_harness_smoke.py` PASS. Then run
`python -m pytest -q` (the full non-e2e suite, which does NOT run under `dbus-run-session`) to
confirm `tests/e2e/` is excluded by default (no `-m e2e` passed) and the root conftest's
isolation guarantee is unaffected for every other test file. Then run
`./scripts/test_ci.sh -q` to confirm the existing fast profile is unaffected.

- [ ] **Step 8: Commit**

`test(e2e): add dbus-run-session harness with daemon/client/audio_fixture fixtures`

(Split into two commits if the diff is large: one for `conftest.py` + fixtures/markers/script,
one for the smoke test — reviewer's call at implementation time.)

---

## Task 7: CI wiring note (tracking only — WHISP-49 owns execution)

**Files:** none modified by this plan — this task is a placeholder to make the dependency
explicit, not an action item for the WHISP-47 implementer.

- [ ] Confirm `dbus-run-session` is present on the `ubuntu-latest` GitHub Actions image (it
  typically is, via the `dbus` package); if not, WHISP-49 adds `sudo apt-get install -y dbus-x11`
  to the new `e2e` job, alongside `full-deps`'s existing `libportaudio2`/`portaudio19-dev`/
  `python3-gi`/`gir1.2-gtk-4.0`.
- [ ] WHISP-49 adds the `e2e` job (`needs: fast`, `timeout-minutes: 10`) running
  `./scripts/test_e2e.sh -q`.
- [ ] WHISP-49 decides whether `real_model` tests ever run in CI (likely: no, or a separate
  nightly job with a pre-warmed model cache — out of scope here).

---

## Verification checklist (run before declaring WHISP-47 complete)

- [ ] `python -m pytest -q` (full unit suite, unchanged count of passing tests plus the new unit
  tests from Tasks 1–5) — green.
- [ ] `./scripts/test_ci.sh -q` — green, unchanged.
- [ ] `./scripts/test_e2e.sh -q` — green, new.
- [ ] `WHISPER_ALOUD_TEST_MODE` unset anywhere outside `tests/e2e/` — grep to confirm no
  production code path depends on it being set for normal operation.
- [ ] `tests/contracts/test_dbus_contract.py` (WHISP-48's snapshot test) still passes unmodified —
  confirms this plan added no D-Bus methods/signals.
