"""Persistence layer for transcription history."""

from importlib import import_module

from .models import HistoryEntry
from .database import TranscriptionDatabase
from .history_manager import HistoryManager

__all__ = [
    "HistoryEntry",
    "TranscriptionDatabase",
    "HistoryManager",
    "AudioArchive",
]

_LAZY_EXPORTS = {
    "AudioArchive": ("audio_archive", "AudioArchive"),
}


def __getattr__(name):
    """Lazily import optional modules to avoid hard dependency at package import time."""
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = import_module(f".{module_name}", __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
