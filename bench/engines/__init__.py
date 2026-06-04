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
