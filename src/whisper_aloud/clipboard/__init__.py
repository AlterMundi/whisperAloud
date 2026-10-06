"""Clipboard integration for WhisperAloud."""

from .clipboard_manager import ClipboardManager, TestClipboardStub, build_clipboard_manager
from .paste_selection import build_paste_simulator
from .paste_simulator import PasteSimulator
from .portal_paste_simulator import PortalPasteSimulator
from .window_manager import WindowInfo, WindowManager, is_own_window

__all__ = [
    "ClipboardManager",
    "TestClipboardStub",
    "build_clipboard_manager",
    "build_paste_simulator",
    "PasteSimulator",
    "PortalPasteSimulator",
    "WindowInfo",
    "WindowManager",
    "is_own_window",
]
