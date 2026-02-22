"""Clipboard integration for WhisperAloud."""

from .clipboard_manager import ClipboardManager
from .paste_simulator import PasteSimulator
from .window_manager import WindowInfo, WindowManager

__all__ = [
    "ClipboardManager",
    "PasteSimulator",
    "WindowInfo",
    "WindowManager",
]
