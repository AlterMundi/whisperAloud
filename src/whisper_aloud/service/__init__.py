"""D-Bus service for WhisperAloud daemon mode."""

from importlib import import_module

__all__ = [
    "WhisperAloudService",
    "WhisperAloudClient",
    "DaemonHistoryManager",
    "HotkeyManager",
]

_LAZY_EXPORTS = {
    "WhisperAloudService": ("daemon", "WhisperAloudService"),
    "WhisperAloudClient": ("client", "WhisperAloudClient"),
    "DaemonHistoryManager": ("history_client", "DaemonHistoryManager"),
    "HotkeyManager": ("hotkey", "HotkeyManager"),
}


def __getattr__(name):
    """Lazily import service modules to avoid daemon-heavy imports by default."""
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = import_module(f".{module_name}", __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
