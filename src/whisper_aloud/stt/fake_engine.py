"""Deterministic test-only speech engine (WHISP-46/47 e2e harness).

Never reachable outside WHISPER_ALOUD_TEST_MODE=1 -- see stt/catalog.py's
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
