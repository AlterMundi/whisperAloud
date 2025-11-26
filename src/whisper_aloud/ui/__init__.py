"""GTK4 user interface for WhisperAloud."""

from .app import WhisperAloudApp
from .main_window import MainWindow
from .utils import AppState, format_duration, format_confidence, format_file_size
from .level_meter import LevelMeterWidget, LevelMeterPanel
from .settings_dialog import SettingsDialog
from .error_handler import ErrorDialog, ErrorSeverity, InputValidator, ValidationError

__all__ = [
    "WhisperAloudApp",
    "MainWindow",
    "AppState",
    "format_duration",
    "format_confidence",
    "format_file_size",
    "LevelMeterWidget",
    "LevelMeterPanel",
    "SettingsDialog",
    "ErrorDialog",
    "ErrorSeverity",
    "InputValidator",
    "ValidationError",
]


def main() -> None:
    """Entry point for the GUI application."""
    import sys
    app = WhisperAloudApp()
    sys.exit(app.run(sys.argv))
