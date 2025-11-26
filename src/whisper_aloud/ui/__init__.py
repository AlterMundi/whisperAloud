"""GTK4 user interface for WhisperAloud."""

from .app import WhisperAloudApp
from .main_window import MainWindow
from .utils import AppState, format_duration, format_confidence, format_file_size

__all__ = [
    "WhisperAloudApp",
    "MainWindow",
    "AppState",
    "format_duration",
    "format_confidence",
    "format_file_size",
]


def main() -> None:
    """Entry point for the GUI application."""
    import sys
    app = WhisperAloudApp()
    sys.exit(app.run(sys.argv))
