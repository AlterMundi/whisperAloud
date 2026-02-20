"""Audio recording subsystem for WhisperAloud."""

from importlib import import_module

__all__ = [
    "DeviceManager",
    "AudioDevice",
    "AudioRecorder",
    "RecordingState",
    "AudioProcessor",
    "AudioPipeline",
    "LevelMeter",
    "AudioLevel",
]

_LAZY_EXPORTS = {
    "DeviceManager": ("device_manager", "DeviceManager"),
    "AudioDevice": ("device_manager", "AudioDevice"),
    "AudioRecorder": ("recorder", "AudioRecorder"),
    "RecordingState": ("recorder", "RecordingState"),
    "AudioProcessor": ("audio_processor", "AudioProcessor"),
    "AudioPipeline": ("audio_processor", "AudioPipeline"),
    "LevelMeter": ("level_meter", "LevelMeter"),
    "AudioLevel": ("level_meter", "AudioLevel"),
}


def __getattr__(name):
    """Lazily import audio modules so optional deps stay optional."""
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        module = import_module(f".{module_name}", __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
