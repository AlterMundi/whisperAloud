"""Production speech-engine contract for the daemon (WHISP-35).

The daemon depends on this Protocol via `create_engine` instead of constructing
`Transcriber` directly, so future engines (Parakeet, WHISP-36) become selectable
through `config.model.engine` without touching daemon code. `Transcriber` already
satisfies this Protocol structurally — no inheritance change required.

Note: this is the *production* daemon contract. It is intentionally separate from
the benchmark-side `STTEngine` ABC in `stt/base.py` (different surface); unifying them
is out of scope (see WHISP-35 spec non-goals).
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
    if engine == "parakeet":
        from .parakeet_speech_engine import ParakeetSpeechEngine

        return ParakeetSpeechEngine(config)
    if engine == "nemotron-streaming":
        from .nemotron_streaming_engine import NemotronStreamingEngine

        return NemotronStreamingEngine(config)
    if engine == "fake":
        from . import catalog

        entry = catalog.get("fake", config.model.name)
        if entry is not None and entry.streaming:
            from .fake_engine import FakeStreamingSpeechEngine

            return FakeStreamingSpeechEngine(config)

        from .fake_engine import FakeSpeechEngine

        return FakeSpeechEngine(config)

    from ..exceptions import ConfigurationError

    # Defensive backstop: config.validate() already rejects unknown engines upstream,
    # but guard here in case a caller constructs a config without validating.
    raise ConfigurationError(f"Unknown STT engine '{engine}'")
