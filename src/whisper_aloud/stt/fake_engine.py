"""Deterministic test-only speech engine (WHISP-46/47 e2e harness).

Never reachable outside WHISPER_ALOUD_TEST_MODE=1 -- see stt/catalog.py's
conditional registration and config.validate()'s engine allowlist, which is
derived from the catalog. This module itself has no test-mode guard of its
own; the guard lives at the registration boundary (catalog + create_engine),
consistent with how every other engine is just "reachable or not" via config.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..exceptions import ConfigurationError, TranscriptionError
from .streaming import PartialResult

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
            raise ConfigurationError(f"FakeSpeechEngine: no sidecar transcript at {sidecar}")
        text = sidecar.read_text().strip()

        duration = len(audio) / sample_rate if sample_rate else 0.0
        self._cancelling = False
        return TranscriptionResult(
            text=text,
            language=self._config.transcription.language or "en",
            segments=[],
            duration=duration,
            confidence=1.0,
            processing_time=float(latency) if latency else 0.0,
        )

    def transcribe_file(self, audio_path: str, **kwargs) -> "TranscriptionResult":
        import numpy as np

        return self.transcribe_numpy(np.zeros(1, dtype="float32"), **kwargs)


class FakeStreamingSpeechEngine:
    """Deterministic streaming `StreamingSpeechEngine` test double (WHISP-57).

    Selected via the "fake"/"fake-streaming" catalog entry (`streaming=True`),
    itself only registered under WHISPER_ALOUD_TEST_MODE=1 -- see
    stt/catalog.py and stt/engine.py's `create_engine`. Never reachable in a
    production install, same guard as FakeSpeechEngine above.

    Reads the same sidecar `<wav>.txt` convention as FakeSpeechEngine, but
    reveals it one word at a time as `accept_chunk()` is called (one
    recording's worth of streaming chunks, as the real AudioRecorder's
    per-chunk hook would deliver them), so a client watching
    `PartialTranscript` sees `committed`/`tentative` grow deterministically.
    `seq` increments on every `accept_chunk()` call (not only when a new word
    is revealed), matching `CommitTracker`'s "one update, one seq" contract.
    """

    is_streaming = True

    def __init__(
        self,
        config: "WhisperAloudConfig",
        finalize_latency_s: float = 0.0,
        finalize_gate: Optional["threading.Event"] = None,
    ) -> None:
        """
        Args:
            config: Loaded config (only `transcription.language` is read).
            finalize_latency_s: Used only if WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S
                is unset -- a fixed delay inside `finalize()`'s unlocked section,
                for tests that want to provoke a real (non-`_SyncExecutor`)
                Stop->Start race deterministically-ish.
            finalize_gate: If given, `finalize()` blocks on `.wait()` on this
                instead of sleeping, so a test can hold it open and release it
                at an exact point (WHISP-57 audit: a real-concurrency
                regression test needs this instead of a sleep-based race).
        """
        self._config = config
        self._loaded = False
        self._cancelling = False
        self._lock = threading.Lock()
        self._words: list[str] = []
        self._revealed = 0
        self._seq = 0
        self._session_started = False
        # WHISP-57 audit (HIGH), defense in depth: incremented by every
        # start_session() call. finalize() snapshots it at entry and, after its
        # unlocked section (where a concurrent start_session() for a NEW
        # session could run -- the daemon's _transcribing guard should prevent
        # this in practice, but this engine does not rely on that), only
        # mutates shared session state if the generation is still the one it
        # started with. Without this, a slow finalize() racing a fresh
        # start_session() could reset the new session's _session_started flag
        # out from under it.
        self._generation = 0
        self._finalize_latency_s = finalize_latency_s
        self._finalize_gate = finalize_gate

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
        """Alias for `cancel_session`, satisfying the batch `SpeechEngine` protocol."""
        self.cancel_session()

    def start_session(self, language: str) -> None:
        wav_path = os.environ.get("WHISPER_ALOUD_FAKE_AUDIO_WAV")
        if not wav_path:
            raise ConfigurationError(
                "FakeStreamingSpeechEngine requires WHISPER_ALOUD_FAKE_AUDIO_WAV to be set"
            )
        sidecar = Path(wav_path).with_suffix(".txt")
        if not sidecar.exists():
            raise ConfigurationError(f"FakeStreamingSpeechEngine: no sidecar transcript at {sidecar}")
        text = sidecar.read_text().strip()
        with self._lock:
            self._words = text.split()
            self._revealed = 0
            self._seq = 0
            self._cancelling = False
            self._session_started = True
            self._generation += 1

    def accept_chunk(self, samples: "np.ndarray") -> None:
        with self._lock:
            if not self._session_started:
                raise RuntimeError("accept_chunk() called before start_session()")
            if self._cancelling:
                return
            if self._revealed < len(self._words):
                self._revealed += 1
            self._seq += 1

    def get_partial(self) -> PartialResult:
        with self._lock:
            words = self._words[: self._revealed]
            seq = self._seq
        if not words:
            return PartialResult(committed="", tentative="", seq=seq)
        return PartialResult(committed=" ".join(words[:-1]), tentative=words[-1], seq=seq)

    def finalize(self) -> "TranscriptionResult":
        from ..transcriber import TranscriptionResult  # local import, avoid cycle

        # Snapshot everything this finalize() needs BEFORE the unlocked
        # section below -- a concurrent start_session() for a new session can
        # run while we're "decoding" (sleeping/blocked on finalize_gate) and
        # must never change the text *this* finalize() reports.
        with self._lock:
            generation = self._generation
            cancelling = self._cancelling
            words_snapshot = list(self._words)

        # Unlocked "decode" section, mirroring NemotronStreamingEngine's
        # finalize() (which runs real sherpa-onnx calls outside the lock) --
        # a real ThreadPoolExecutor can interleave a new start_session() here.
        if self._finalize_gate is not None:
            self._finalize_gate.wait()
        else:
            env_latency = os.environ.get("WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S")
            latency = float(env_latency) if env_latency else self._finalize_latency_s
            if latency:
                time.sleep(latency)

        text = "" if cancelling else " ".join(words_snapshot)

        with self._lock:
            if self._generation == generation:
                self._cancelling = False
                self._session_started = False
            # else: a newer session has already started -- leave its
            # _session_started/_cancelling alone, this finalize() is stale.

        return TranscriptionResult(
            text=text,
            language=self._config.transcription.language or "en",
            segments=[],
            confidence=1.0,
            duration=0.0,
            processing_time=0.0,
        )

    def cancel_session(self) -> None:
        with self._lock:
            self._cancelling = True
