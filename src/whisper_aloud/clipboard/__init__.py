"""Clipboard integration for WhisperAloud."""

from .clipboard_manager import ClipboardManager, TestClipboardStub, build_clipboard_manager
from .paste_simulator import PasteSimulator
from .window_manager import WindowInfo, WindowManager

__all__ = [
    "ClipboardManager",
    "TestClipboardStub",
    "build_clipboard_manager",
    "PasteSimulator",
    "WindowInfo",
    "WindowManager",
]
