"""Pluggable STT engines with a shared interface."""
from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript

__all__ = ["STTEngine", "Transcript", "EngineCapabilities"]
