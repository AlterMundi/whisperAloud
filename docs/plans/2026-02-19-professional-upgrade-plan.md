# WhisperAloud Professional Upgrade — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform WhisperAloud from a functional core into a daemon-first, professionally packaged Linux desktop app with AGC audio pipeline, global hotkeys, and system tray integration.

**Architecture:** Daemon-first — the D-Bus daemon owns all core logic (recording, transcription, history, clipboard). GUI, CLI, and tray are thin D-Bus clients. Systemd user service for lifecycle. See `docs/plans/2026-02-19-professional-upgrade-design.md` for full design.

**Tech Stack:** Python 3.10+, GTK4, pydbus, AyatanaAppIndicator3, XDG Desktop Portal (libportal), noisereduce, sounddevice, faster-whisper, debhelper/dh-python.

**Branch:** `devel`

**Ref files:**
- Current daemon: `src/whisper_aloud/service/daemon.py`
- Current audio processor: `src/whisper_aloud/audio/audio_processor.py`
- Current config: `src/whisper_aloud/config.py`
- Current GUI window: `src/whisper_aloud/ui/main_window.py`
- Current GUI app: `src/whisper_aloud/ui/app.py`
- Current tests: `tests/conftest.py`, `tests/test_*.py`
- Design doc: `docs/plans/2026-02-19-professional-upgrade-design.md`

---

## Phase 1: Audio Pipeline (AGC + Noise Gate + Denoising + Limiter)

Independent of daemon refactor. Improves the existing `AudioProcessor` with a stateful pipeline.

### Task 1.1: Noise Gate

**Files:**
- Create: `tests/test_audio_pipeline.py`
- Modify: `src/whisper_aloud/audio/audio_processor.py`

**Step 1: Write the failing test**

```python
# tests/test_audio_pipeline.py
import numpy as np
import pytest


class TestNoiseGate:
    """Tests for noise gate processing stage."""

    def test_gate_silences_below_threshold(self):
        """Audio below threshold should be silenced."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        # Quiet noise at ~-60 dBFS
        quiet = np.random.randn(1600).astype(np.float32) * 0.001
        result = gate.process(quiet, sample_rate=16000)
        rms = np.sqrt(np.mean(result ** 2))
        assert rms < 0.0005, f"Gate should silence quiet audio, got RMS {rms}"

    def test_gate_passes_above_threshold(self):
        """Audio above threshold should pass through."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        # Loud signal at ~-10 dBFS
        loud = np.sin(np.linspace(0, 440 * 2 * np.pi, 1600)).astype(np.float32) * 0.3
        result = gate.process(loud, sample_rate=16000)
        # Output should be close to input
        np.testing.assert_allclose(result, loud, atol=0.05)

    def test_gate_smooth_attack_release(self):
        """Gate should not produce clicks (smooth transitions)."""
        from whisper_aloud.audio.audio_processor import NoiseGate

        gate = NoiseGate(threshold_db=-40.0)
        sr = 16000
        # Silence then loud signal
        silence = np.zeros(800, dtype=np.float32)
        loud = np.sin(np.linspace(0, 440 * 2 * np.pi, 800)).astype(np.float32) * 0.3
        audio = np.concatenate([silence, loud])
        result = gate.process(audio, sample_rate=sr)
        # Check for no sudden jumps > 0.1 between consecutive samples
        diffs = np.abs(np.diff(result))
        assert np.max(diffs) < 0.15, f"Click detected: max diff {np.max(diffs)}"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_pipeline.py::TestNoiseGate -v`
Expected: FAIL with `ImportError: cannot import name 'NoiseGate'`

**Step 3: Write minimal implementation**

Add to `src/whisper_aloud/audio/audio_processor.py`:

```python
class NoiseGate:
    """Noise gate with smooth attack/release."""

    def __init__(self, threshold_db: float = -40.0, attack_ms: float = 5.0, release_ms: float = 50.0):
        self.threshold_db = threshold_db
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self._envelope = 0.0  # Smoothed gate state

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply noise gate to audio chunk."""
        if audio.size == 0:
            return audio

        threshold_linear = 10 ** (self.threshold_db / 20.0)
        attack_coeff = np.exp(-1.0 / (self.attack_ms * sample_rate / 1000.0))
        release_coeff = np.exp(-1.0 / (self.release_ms * sample_rate / 1000.0))

        result = np.copy(audio)
        envelope = self._envelope

        for i in range(len(result)):
            level = abs(result[i])
            if level > threshold_linear:
                envelope = attack_coeff * envelope + (1 - attack_coeff) * 1.0
            else:
                envelope = release_coeff * envelope + (1 - release_coeff) * 0.0
            result[i] *= envelope

        self._envelope = envelope
        return result
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio_pipeline.py::TestNoiseGate -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_audio_pipeline.py src/whisper_aloud/audio/audio_processor.py
git commit -m "feat(audio): add noise gate with smooth attack/release"
```

---

### Task 1.2: AGC (Automatic Gain Control)

**Files:**
- Modify: `tests/test_audio_pipeline.py`
- Modify: `src/whisper_aloud/audio/audio_processor.py`

**Step 1: Write the failing test**

Append to `tests/test_audio_pipeline.py`:

```python
class TestAGC:
    """Tests for automatic gain control."""

    def test_agc_boosts_quiet_audio(self):
        """Quiet audio should be boosted toward target."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0, max_gain_db=30.0)
        # Very quiet signal at ~-50 dBFS
        quiet = np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)).astype(np.float32) * 0.003
        result = agc.process(quiet, sample_rate=16000)
        input_rms = np.sqrt(np.mean(quiet ** 2))
        output_rms = np.sqrt(np.mean(result ** 2))
        assert output_rms > input_rms * 5, f"AGC should boost quiet audio: in={input_rms}, out={output_rms}"

    def test_agc_attenuates_loud_audio(self):
        """Loud audio should be reduced toward target."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0)
        # Loud signal at ~-3 dBFS
        loud = np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)).astype(np.float32) * 0.7
        result = agc.process(loud, sample_rate=16000)
        input_rms = np.sqrt(np.mean(loud ** 2))
        output_rms = np.sqrt(np.mean(result ** 2))
        assert output_rms < input_rms * 0.8, f"AGC should attenuate loud audio: in={input_rms}, out={output_rms}"

    def test_agc_respects_max_gain(self):
        """Gain should not exceed max_gain_db."""
        from whisper_aloud.audio.audio_processor import AGC

        agc = AGC(target_db=-18.0, max_gain_db=10.0)
        # Near-silent signal
        silent = np.ones(16000, dtype=np.float32) * 0.0001
        result = agc.process(silent, sample_rate=16000)
        max_gain_linear = 10 ** (10.0 / 20.0)  # ~3.16x
        assert np.max(np.abs(result)) <= np.max(np.abs(silent)) * max_gain_linear * 1.1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_pipeline.py::TestAGC -v`
Expected: FAIL with `ImportError: cannot import name 'AGC'`

**Step 3: Write minimal implementation**

Add to `src/whisper_aloud/audio/audio_processor.py`:

```python
class AGC:
    """Automatic Gain Control using sliding-window RMS."""

    def __init__(
        self,
        target_db: float = -18.0,
        max_gain_db: float = 30.0,
        min_gain_db: float = -10.0,
        attack_ms: float = 10.0,
        release_ms: float = 100.0,
        window_ms: float = 300.0,
    ):
        self.target_linear = 10 ** (target_db / 20.0)
        self.max_gain = 10 ** (max_gain_db / 20.0)
        self.min_gain = 10 ** (min_gain_db / 20.0)
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.window_ms = window_ms
        self._current_gain = 1.0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply AGC to audio chunk."""
        if audio.size == 0:
            return audio

        window_samples = int(self.window_ms * sample_rate / 1000.0)
        attack_coeff = np.exp(-1.0 / (self.attack_ms * sample_rate / 1000.0))
        release_coeff = np.exp(-1.0 / (self.release_ms * sample_rate / 1000.0))

        result = np.copy(audio)
        gain = self._current_gain

        for i in range(len(result)):
            # Compute local RMS over window
            start = max(0, i - window_samples)
            window = audio[start:i + 1]
            rms = np.sqrt(np.mean(window ** 2))

            if rms > 1e-8:
                desired_gain = self.target_linear / rms
                desired_gain = np.clip(desired_gain, self.min_gain, self.max_gain)
            else:
                desired_gain = 1.0

            # Smooth gain changes
            if desired_gain < gain:
                coeff = attack_coeff
            else:
                coeff = release_coeff
            gain = coeff * gain + (1 - coeff) * desired_gain

            result[i] *= gain

        self._current_gain = gain
        return result
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio_pipeline.py::TestAGC -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_audio_pipeline.py src/whisper_aloud/audio/audio_processor.py
git commit -m "feat(audio): add AGC with sliding-window RMS"
```

---

### Task 1.3: Peak Limiter

**Files:**
- Modify: `tests/test_audio_pipeline.py`
- Modify: `src/whisper_aloud/audio/audio_processor.py`

**Step 1: Write the failing test**

Append to `tests/test_audio_pipeline.py`:

```python
class TestPeakLimiter:
    """Tests for peak limiter."""

    def test_limiter_clips_above_ceiling(self):
        """Audio above ceiling should be limited."""
        from whisper_aloud.audio.audio_processor import PeakLimiter

        limiter = PeakLimiter(ceiling_db=-1.0)
        ceiling_linear = 10 ** (-1.0 / 20.0)  # ~0.891
        loud = np.ones(1600, dtype=np.float32) * 1.0  # 0 dBFS
        result = limiter.process(loud)
        assert np.max(np.abs(result)) <= ceiling_linear + 0.001

    def test_limiter_passes_below_ceiling(self):
        """Audio below ceiling should pass unchanged."""
        from whisper_aloud.audio.audio_processor import PeakLimiter

        limiter = PeakLimiter(ceiling_db=-1.0)
        quiet = np.ones(1600, dtype=np.float32) * 0.5
        result = limiter.process(quiet)
        np.testing.assert_array_equal(result, quiet)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_pipeline.py::TestPeakLimiter -v`
Expected: FAIL with `ImportError: cannot import name 'PeakLimiter'`

**Step 3: Write minimal implementation**

Add to `src/whisper_aloud/audio/audio_processor.py`:

```python
class PeakLimiter:
    """Hard peak limiter to prevent clipping."""

    def __init__(self, ceiling_db: float = -1.0):
        self.ceiling = 10 ** (ceiling_db / 20.0)

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Apply hard limiter."""
        if audio.size == 0:
            return audio
        return np.clip(audio, -self.ceiling, self.ceiling)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio_pipeline.py::TestPeakLimiter -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_audio_pipeline.py src/whisper_aloud/audio/audio_processor.py
git commit -m "feat(audio): add peak limiter"
```

---

### Task 1.4: AudioPipeline (orchestrator)

**Files:**
- Modify: `tests/test_audio_pipeline.py`
- Modify: `src/whisper_aloud/audio/audio_processor.py`
- Modify: `src/whisper_aloud/config.py`

**Step 1: Write the failing test**

Append to `tests/test_audio_pipeline.py`:

```python
class TestAudioPipeline:
    """Tests for the full audio processing pipeline."""

    def test_pipeline_processes_all_stages(self):
        """Pipeline should run gate → AGC → limiter in order."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=True,
            agc_enabled=True,
            denoising_enabled=False,  # Skip for now
            limiter_enabled=True,
        )
        pipeline = AudioPipeline(config)
        audio = np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)).astype(np.float32) * 0.1
        result = pipeline.process(audio, sample_rate=16000)
        assert result.shape == audio.shape
        assert result.dtype == np.float32

    def test_pipeline_all_disabled_passthrough(self):
        """With all stages disabled, output equals input."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(
            noise_gate_enabled=False,
            agc_enabled=False,
            denoising_enabled=False,
            limiter_enabled=False,
        )
        pipeline = AudioPipeline(config)
        audio = np.random.randn(1600).astype(np.float32) * 0.5
        result = pipeline.process(audio, sample_rate=16000)
        np.testing.assert_array_equal(result, audio)

    def test_pipeline_is_stateful_across_chunks(self):
        """Pipeline should maintain state between process() calls."""
        from whisper_aloud.audio.audio_processor import AudioPipeline
        from whisper_aloud.config import AudioProcessingConfig

        config = AudioProcessingConfig(agc_enabled=True)
        pipeline = AudioPipeline(config)
        chunk1 = np.sin(np.linspace(0, 440 * 2 * np.pi, 1600)).astype(np.float32) * 0.01
        chunk2 = np.sin(np.linspace(0, 440 * 2 * np.pi, 1600)).astype(np.float32) * 0.01
        result1 = pipeline.process(chunk1, sample_rate=16000)
        result2 = pipeline.process(chunk2, sample_rate=16000)
        # Second chunk should have higher gain (AGC adapting)
        rms1 = np.sqrt(np.mean(result1 ** 2))
        rms2 = np.sqrt(np.mean(result2 ** 2))
        assert rms2 >= rms1 * 0.8, "AGC should maintain/increase gain across chunks"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_pipeline.py::TestAudioPipeline -v`
Expected: FAIL with `ImportError: cannot import name 'AudioPipeline'`

**Step 3: Add AudioProcessingConfig to config.py**

Add to `src/whisper_aloud/config.py` after `PersistenceConfig`:

```python
@dataclass
class AudioProcessingConfig:
    """Configuration for audio processing pipeline."""
    noise_gate_enabled: bool = True
    noise_gate_threshold_db: float = -40.0
    agc_enabled: bool = True
    agc_target_db: float = -18.0
    agc_max_gain_db: float = 30.0
    denoising_enabled: bool = True
    denoising_strength: float = 0.5
    limiter_enabled: bool = True
    limiter_ceiling_db: float = -1.0
```

Add `audio_processing` field to `WhisperAloudConfig`:

```python
audio_processing: AudioProcessingConfig = field(default_factory=AudioProcessingConfig)
```

Update `to_dict()`, `from_dict()`, `_apply_env_overrides()`, and `validate()` accordingly.

**Step 4: Write AudioPipeline**

Add to `src/whisper_aloud/audio/audio_processor.py`:

```python
from ..config import AudioProcessingConfig

class AudioPipeline:
    """Full audio processing pipeline: gate → AGC → denoising → limiter."""

    def __init__(self, config: AudioProcessingConfig):
        self.config = config
        self._gate = NoiseGate(threshold_db=config.noise_gate_threshold_db) if config.noise_gate_enabled else None
        self._agc = AGC(target_db=config.agc_target_db, max_gain_db=config.agc_max_gain_db) if config.agc_enabled else None
        self._limiter = PeakLimiter(ceiling_db=config.limiter_ceiling_db) if config.limiter_enabled else None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Process audio through the pipeline."""
        result = audio
        if self._gate:
            result = self._gate.process(result, sample_rate)
        if self._agc:
            result = self._agc.process(result, sample_rate)
        # Denoising: Task 1.5
        if self._limiter:
            result = self._limiter.process(result)
        return result.astype(np.float32)
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_audio_pipeline.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add tests/test_audio_pipeline.py src/whisper_aloud/audio/audio_processor.py src/whisper_aloud/config.py
git commit -m "feat(audio): add AudioPipeline orchestrator with config"
```

---

### Task 1.5: Denoising (noisereduce integration)

**Files:**
- Modify: `tests/test_audio_pipeline.py`
- Modify: `src/whisper_aloud/audio/audio_processor.py`

**Step 1: Write the failing test**

Append to `tests/test_audio_pipeline.py`:

```python
class TestDenoiser:
    """Tests for spectral denoising."""

    def test_denoiser_reduces_noise(self):
        """Denoiser should reduce noise floor."""
        from whisper_aloud.audio.audio_processor import Denoiser

        denoiser = Denoiser(strength=0.5)
        sr = 16000
        # Signal + noise
        t = np.linspace(0, 1, sr, dtype=np.float32)
        signal = np.sin(2 * np.pi * 440 * t) * 0.3
        noise = np.random.randn(sr).astype(np.float32) * 0.05
        noisy = signal + noise
        result = denoiser.process(noisy, sample_rate=sr)
        # Output should have lower noise (check by comparing noise-only regions)
        assert result.shape == noisy.shape

    def test_denoiser_graceful_without_noisereduce(self):
        """If noisereduce not installed, should pass through."""
        from whisper_aloud.audio.audio_processor import Denoiser
        from unittest.mock import patch

        denoiser = Denoiser(strength=0.5)
        audio = np.random.randn(1600).astype(np.float32)
        with patch.dict('sys.modules', {'noisereduce': None}):
            # Force reimport to simulate missing module
            denoiser._noisereduce = None
            result = denoiser.process(audio, sample_rate=16000)
        np.testing.assert_array_equal(result, audio)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_pipeline.py::TestDenoiser -v`
Expected: FAIL with `ImportError: cannot import name 'Denoiser'`

**Step 3: Write minimal implementation**

Add to `src/whisper_aloud/audio/audio_processor.py`:

```python
class Denoiser:
    """Spectral denoising using noisereduce (optional dependency)."""

    def __init__(self, strength: float = 0.5):
        self.strength = strength
        self._noisereduce = None
        try:
            import noisereduce
            self._noisereduce = noisereduce
        except ImportError:
            logger.info("noisereduce not installed, denoising disabled")

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply spectral denoising."""
        if self._noisereduce is None or audio.size == 0:
            return audio
        try:
            return self._noisereduce.reduce_noise(
                y=audio, sr=sample_rate,
                prop_decrease=self.strength,
                stationary=True,
            ).astype(np.float32)
        except Exception as e:
            logger.warning(f"Denoising failed, passing through: {e}")
            return audio
```

Wire into `AudioPipeline.process()` between AGC and limiter.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio_pipeline.py::TestDenoiser -v`
Expected: PASS (or SKIP if noisereduce not installed — first test may need conditional skip)

**Step 5: Commit**

```bash
git add tests/test_audio_pipeline.py src/whisper_aloud/audio/audio_processor.py
git commit -m "feat(audio): add spectral denoiser with graceful fallback"
```

---

### Task 1.6: Integrate pipeline into AudioRecorder

**Files:**
- Modify: `src/whisper_aloud/audio/recorder.py`
- Modify: `src/whisper_aloud/audio/__init__.py`
- Modify existing tests if needed

**Step 1:** Write a test that `AudioRecorder.stop()` returns audio processed through `AudioPipeline` when config has processing enabled.

**Step 2:** Run test, verify fails.

**Step 3:** Modify `AudioRecorder` to accept `AudioProcessingConfig` and use `AudioPipeline` in its `stop()` method (replacing the old `AudioProcessor.process_recording()` call).

**Step 4:** Run all audio tests: `pytest tests/test_audio_pipeline.py tests/test_audio_recorder.py tests/test_audio_processor.py -v`

**Step 5:** Commit.

```bash
git commit -m "feat(audio): integrate AudioPipeline into AudioRecorder"
```

---

## Phase 2: D-Bus API Expansion & Daemon Hardening

### Task 2.1: Unified identifiers and introspection XML

**Files:**
- Create: `data/org.fede.whisperaloud.xml`
- Modify: `src/whisper_aloud/service/daemon.py` (change bus name and interface name)

**Step 1: Create introspection XML**

Write `data/org.fede.whisperaloud.xml` with the full API from the design doc (all methods and signals).

**Step 2: Update daemon.py**

- Change the docstring XML from `org.fede.whisperAloud.Control` to `org.fede.whisperaloud.Control`
- Change `bus.publish("org.fede.whisperAloud", ...)` to `bus.publish("org.fede.whisperaloud", ...)`
- Add new methods: `CancelRecording`, `GetHistory`, `GetConfig`, `SetConfig`
- Add new signals: `RecordingStarted`, `RecordingStopped`, `TranscriptionReady`, `LevelUpdate`, `ConfigChanged`, `Error`
- Rename `ErrorOccurred` → `Error` (with code + message)
- Rename `TranscriptionCompleted` → `TranscriptionReady` (with text + meta dict)

**Step 3: Update `__main__.py` CLI client to use new bus name**

**Step 4: Commit**

```bash
git commit -m "refactor(dbus): unify identifiers to org.fede.whisperaloud"
```

---

### Task 2.2: New D-Bus methods with tests

**Files:**
- Create: `tests/test_daemon.py`
- Modify: `src/whisper_aloud/service/daemon.py`

**Step 1: Write tests for new methods**

Test `GetStatus()` returns dict with expected keys (state, version, model, device).
Test `GetConfig()` returns serialized config.
Test `SetConfig()` applies changes and emits `ConfigChanged`.
Test `CancelRecording()` aborts active recording.
Test `GetHistory()` returns entries from history manager.

Mock pydbus/D-Bus — test the method logic, not D-Bus transport.

**Step 2-5:** Red-green-refactor for each method. Commit after each group.

---

### Task 2.3: LevelUpdate signal with throttling

**Files:**
- Modify: `tests/test_daemon.py`
- Modify: `src/whisper_aloud/service/daemon.py`

**Step 1:** Write test that LevelUpdate fires at max 10Hz even if audio callback runs at 100Hz.

**Step 2:** Implement: daemon registers a level callback with AudioRecorder, accumulates peaks, emits `LevelUpdate` via `GLib.timeout_add(100, ...)` (every 100ms = 10Hz).

**Step 3:** Commit.

---

### Task 2.4: SIGTERM handler — clean shutdown

**Files:**
- Modify: `tests/test_daemon.py`
- Modify: `src/whisper_aloud/service/daemon.py`

**Step 1:** Write test: when recording is active and SIGTERM received, recording stops without transcription, audio is flushed if save_audio=true, `StatusChanged("shutdown")` is emitted.

**Step 2:** Implement in existing signal handler.

**Step 3:** Commit.

---

### Task 2.5: Clipboard integration in daemon

**Files:**
- Modify: `src/whisper_aloud/service/daemon.py`

**Step 1:** Write test: after `TranscriptionReady`, if `auto_copy=true`, text is copied to clipboard.

**Step 2:** Add `ClipboardManager` to daemon, call it in `_transcribe_and_emit` after transcription.

**Step 3:** Commit.

---

## Phase 3: AppIndicator (Tray Icon)

### Task 3.1: AppIndicator with state management

**Files:**
- Create: `src/whisper_aloud/service/indicator.py`
- Create: `tests/test_indicator.py`

**Step 1: Write the failing test**

```python
# tests/test_indicator.py
from unittest.mock import MagicMock, patch


class TestIndicator:
    def test_indicator_sets_idle_icon(self):
        """Indicator should show mic icon when idle."""
        with patch('gi.repository.AyatanaAppIndicator3') as mock_ai:
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None, on_quit=lambda: None)
            indicator.set_state("idle")
            # Verify icon was set to mic
            mock_ai.Indicator.new.return_value.set_icon_full.assert_called()

    def test_indicator_menu_has_toggle(self):
        """Context menu should have Toggle Recording item."""
        with patch('gi.repository.AyatanaAppIndicator3'):
            with patch('gi.repository.Gtk') as mock_gtk:
                from whisper_aloud.service.indicator import WhisperAloudIndicator
                indicator = WhisperAloudIndicator(on_toggle=lambda: None, on_quit=lambda: None)
                # Verify menu items were created
                assert indicator._menu is not None
```

**Step 2:** Run test, verify fails.

**Step 3:** Implement `WhisperAloudIndicator` class:
- `__init__(on_toggle, on_open_gui, on_quit)` — creates indicator with menu
- `set_state(state: str)` — updates icon
- `set_last_text(text: str)` — updates "Last:" menu item
- Uses `AyatanaAppIndicator3.Indicator.new()` with `Gtk.Menu` (GTK3)

**Step 4:** Run tests. Commit.

```bash
git commit -m "feat(tray): add AppIndicator with state management"
```

---

### Task 3.2: Integrate indicator into daemon

**Files:**
- Modify: `src/whisper_aloud/service/daemon.py`

**Step 1:** Write test: daemon creates indicator on startup, state changes propagate to indicator.

**Step 2:** Instantiate `WhisperAloudIndicator` in daemon `__init__`, wire callbacks.

**Step 3:** Commit.

---

## Phase 4: Global Hotkey

### Task 4.1: Hotkey backend abstraction

**Files:**
- Create: `src/whisper_aloud/service/hotkey.py`
- Create: `tests/test_hotkey.py`

**Step 1: Write the failing test**

```python
# tests/test_hotkey.py
class TestHotkeyManager:
    def test_detects_available_backend(self):
        """Should detect which backend is available."""
        from whisper_aloud.service.hotkey import HotkeyManager
        manager = HotkeyManager()
        backend = manager.detect_backend()
        assert backend in ("portal", "keybinder", "none")

    def test_register_returns_success(self):
        """Register should succeed or return False if no backend."""
        from whisper_aloud.service.hotkey import HotkeyManager
        manager = HotkeyManager()
        result = manager.register("<Super><Alt>r", callback=lambda: None)
        assert isinstance(result, bool)
```

**Step 2-4:** Implement `HotkeyManager`:
- `detect_backend()` — tries XDG Portal, then keybinder, returns string
- `register(accel, callback)` → bool
- `unregister()` → None
- Graceful: if no backend, returns False, logs info

**Step 5:** Commit.

---

### Task 4.2: XDG Desktop Portal backend

**Files:**
- Modify: `src/whisper_aloud/service/hotkey.py`

Implement portal backend using `gi.repository.Xdp` (libportal). This is the GNOME 46+ / KDE 6+ path.

Commit.

---

### Task 4.3: libkeybinder3 fallback backend

**Files:**
- Modify: `src/whisper_aloud/service/hotkey.py`

Implement keybinder backend using `gi.repository.Keybinder`. This is the X11 fallback.

Commit.

---

### Task 4.4: HotkeyConfig and integration

**Files:**
- Modify: `src/whisper_aloud/config.py`
- Modify: `src/whisper_aloud/service/daemon.py`

**Step 1:** Add `HotkeyConfig` dataclass to config.py.

**Step 2:** Integrate `HotkeyManager` into daemon startup.

**Step 3:** If no backend available, log warning and expose D-Bus methods only (graceful degradation).

Commit.

---

## Phase 5: GUI Refactor to D-Bus Client

### Task 5.1: D-Bus client wrapper

**Files:**
- Create: `src/whisper_aloud/service/client.py`
- Create: `tests/test_dbus_client.py`

**Step 1: Write the failing test**

```python
# tests/test_dbus_client.py
from unittest.mock import MagicMock, patch


class TestDBusClient:
    def test_client_connects_to_daemon(self):
        """Client should connect to daemon bus name."""
        with patch('pydbus.SessionBus') as mock_bus:
            from whisper_aloud.service.client import WhisperAloudClient
            client = WhisperAloudClient()
            mock_bus.return_value.get.assert_called_with(
                "org.fede.whisperaloud", "/org/fede/whisperaloud"
            )

    def test_toggle_calls_daemon(self):
        """toggle_recording should call D-Bus ToggleRecording."""
        with patch('pydbus.SessionBus') as mock_bus:
            from whisper_aloud.service.client import WhisperAloudClient
            client = WhisperAloudClient()
            client.toggle_recording()
            mock_bus.return_value.get.return_value.ToggleRecording.assert_called_once()

    def test_client_handles_daemon_unavailable(self):
        """Should raise clear error if daemon not running."""
        with patch('pydbus.SessionBus') as mock_bus:
            from whisper_aloud.service.client import WhisperAloudClient
            mock_bus.return_value.get.side_effect = Exception("org.fede.whisperaloud not found")
            client = WhisperAloudClient()
            assert not client.is_connected
```

**Step 2-4:** Implement `WhisperAloudClient`:
- Connects to `org.fede.whisperaloud` via pydbus
- Methods mirror daemon: `toggle_recording()`, `start_recording()`, `stop_recording()`, etc.
- Signal subscription: `on_transcription_ready(callback)`, `on_level_update(callback)`, `on_status_changed(callback)`
- `is_connected` property
- Auto-activation: if daemon not running, attempts D-Bus activation

**Step 5:** Commit.

---

### Task 5.2: Refactor MainWindow to use D-Bus client

**Files:**
- Modify: `src/whisper_aloud/ui/main_window.py`
- Modify: `src/whisper_aloud/ui/app.py`

This is the biggest refactor. Key changes:

**Step 1:** Replace direct `Transcriber`/`AudioRecorder`/`HistoryManager` instantiation with `WhisperAloudClient`.

**Step 2:** Replace recording logic:
- Old: `self.recorder.start()` / `self.recorder.stop()` / `self.transcriber.transcribe_numpy()`
- New: `self.client.start_recording()` / `self.client.stop_recording()` → wait for `TranscriptionReady` signal

**Step 3:** Replace level meter:
- Old: `AudioRecorder` level callback
- New: Subscribe to `LevelUpdate` D-Bus signal

**Step 4:** Replace history:
- Old: Direct `HistoryManager` queries
- New: `self.client.get_history(limit=100)`

**Step 5:** Replace settings save:
- Old: `config.save()` directly
- New: `self.client.set_config(changes)` → daemon applies and emits `ConfigChanged`

**Step 6:** Handle daemon unavailable:
- Show diagnostic dialog: "WhisperAloud service not running. Run: systemctl --user start whisper-aloud"
- Offer "Start Service" button that calls `systemctl --user start whisper-aloud.service`

**Step 7:** Handle daemon restart:
- Subscribe to `NameOwnerChanged` on the bus
- Auto-reconnect when daemon comes back

**Step 8:** Run full test suite, fix breakage.

**Step 9:** Commit.

```bash
git commit -m "refactor(ui): convert GUI to D-Bus thin client"
```

---

### Task 5.3: Update app.py for single-instance + daemon interaction

**Files:**
- Modify: `src/whisper_aloud/ui/app.py`

- Change `application_id` to `org.fede.whisperaloud.Gui`
- Add `Gio.ApplicationFlags.HANDLES_COMMAND_LINE` or similar for raising existing window
- On `do_activate`: connect to daemon, present window

Commit.

---

## Phase 6: Systemd + D-Bus Activation

### Task 6.1: Data files

**Files:**
- Create: `data/whisper-aloud.service`
- Create: `data/org.fede.whisperaloud.service` (D-Bus activation)
- Move: `com.whisperaloud.App.desktop` → `data/org.fede.whisperaloud.desktop`

**Step 1:** Write systemd unit (from design doc).

**Step 2:** Write D-Bus activation file (from design doc).

**Step 3:** Update .desktop file with Actions (Toggle, Cancel), unified ID.

**Step 4:** Commit.

```bash
git commit -m "feat(system): add systemd unit, D-Bus activation, desktop file"
```

---

### Task 6.2: Delete GNOME Shell extension

**Files:**
- Delete: `gnome-extension/` (entire directory)
- Modify: `scripts/install_gnome_integration.sh` (remove extension install, keep notification setup)

Commit.

```bash
git commit -m "chore: remove deprecated GNOME Shell extension"
```

---

## Phase 7: Packaging (.deb)

### Task 7.1: Debian packaging files

**Files:**
- Create: `debian/control`
- Create: `debian/rules`
- Create: `debian/changelog`
- Create: `debian/copyright`
- Create: `debian/whisper-aloud.install`
- Create: `debian/whisper-aloud.dirs`
- Create: `debian/compat` (or `debian/debhelper-compat`)

**Step 1:** Write `debian/control` with two packages (`whisper-aloud`, `whisper-aloud-engine-ctranslate2`) and dependencies from design doc.

**Step 2:** Write `debian/rules` using `dh` with `--buildsystem=pybuild`.

**Step 3:** Write `debian/whisper-aloud.install` mapping data files to install paths.

**Step 4:** Test build: `dpkg-buildpackage -us -uc -b`

**Step 5:** Lint: `lintian -IE --pedantic ../whisper-aloud_*.changes`

**Step 6:** Commit.

```bash
git commit -m "feat(packaging): add debian packaging"
```

---

### Task 7.2: Shell wrappers

**Files:**
- Create: `data/run` (wrapper script)
- Create: `data/run-gui` (GUI wrapper script)

Shell scripts that prefer `/usr/lib/whisper-aloud/venv/bin/python` if exists, fallback to system `python3`.

Commit.

---

### Task 7.3: AppStream metadata

**Files:**
- Create: `data/org.fede.whisperaloud.metainfo.xml`

AppStream metadata for GNOME Software / app stores.

Validate: `appstreamcli validate-relax data/org.fede.whisperaloud.metainfo.xml`

Commit.

---

## Phase 8: Fix Existing Tests + Final Integration

### Task 8.1: Fix the 4 failing tests

**Files:**
- Modify: `tests/test_audio_device_manager.py`
- Modify: `tests/test_audio_processor.py`
- Modify: `tests/test_audio_recorder.py`

Fix mock setup bugs identified in exploration (subscriptable mock, zero-division resample, mock comparison).

Commit.

---

### Task 8.2: Update CLI for unified bus name

**Files:**
- Modify: `src/whisper_aloud/__main__.py`

Update D-Bus client calls to use `org.fede.whisperaloud`.

Commit.

---

### Task 8.3: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Add new commands (systemctl, D-Bus introspection), update architecture description.

Commit.

---

### Task 8.4: Full test run + final commit

Run: `pytest -v --cov=whisper_aloud`

Ensure all tests pass. Fix any remaining issues.

Final commit on `devel` branch.

---

## Execution Order Summary

| Phase | Tasks | Dependencies |
|-------|-------|-------------|
| 1. Audio Pipeline | 1.1–1.6 | Independent |
| 2. D-Bus Expansion | 2.1–2.5 | Independent |
| 3. AppIndicator | 3.1–3.2 | After 2.1 (unified IDs) |
| 4. Global Hotkey | 4.1–4.4 | After 2.1 |
| 5. GUI Refactor | 5.1–5.3 | After 2.1–2.5 (daemon API complete) |
| 6. Systemd + Files | 6.1–6.2 | After 2.1 |
| 7. Packaging | 7.1–7.3 | After all code phases |
| 8. Cleanup | 8.1–8.4 | After all |

Phases 1, 2, 3, 4, 6 can run in parallel (different files). Phase 5 depends on Phase 2. Phase 7 depends on all code. Phase 8 is final.
