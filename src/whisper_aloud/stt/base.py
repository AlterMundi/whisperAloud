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
