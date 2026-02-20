"""GTK4 user interface for WhisperAloud."""

from importlib import import_module

from .utils import AppState, format_duration, format_confidence, format_file_size

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

_LAZY_EXPORTS = {
    "WhisperAloudApp": ("app", "WhisperAloudApp"),
    "MainWindow": ("main_window", "MainWindow"),
    "LevelMeterWidget": ("level_meter", "LevelMeterWidget"),
    "LevelMeterPanel": ("level_meter", "LevelMeterPanel"),
    "SettingsDialog": ("settings_dialog", "SettingsDialog"),
    "ErrorDialog": ("error_handler", "ErrorDialog"),
    "ErrorSeverity": ("error_handler", "ErrorSeverity"),
    "InputValidator": ("error_handler", "InputValidator"),
    "ValidationError": ("error_handler", "ValidationError"),
}


def __getattr__(name):
    """Lazily import GTK-heavy UI modules."""
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = import_module(f".{module_name}", __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    """Entry point for the GUI application."""
    import sys
    from .app import WhisperAloudApp

    app = WhisperAloudApp()
    sys.exit(app.run(sys.argv))
