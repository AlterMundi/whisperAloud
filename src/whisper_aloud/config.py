"""Configuration management for WhisperAloud."""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from .exceptions import ConfigurationError
from .utils.validation_helpers import sanitize_language_code

logger = logging.getLogger(__name__)


def parse_bool_env(env_var: str, default: bool) -> bool:
    """Parse boolean environment variable with validation."""
    value = os.getenv(env_var)
    if value is None:
        return default

    value_lower = value.lower().strip()
    if value_lower in ('true', '1', 'yes', 'on'):
        return True
    elif value_lower in ('false', '0', 'no', 'off', ''):
        return False
    else:
        logger.warning(
            f"Invalid boolean value '{value}' for {env_var}. "
            f"Valid: true/false, 1/0, yes/no, on/off. Using default: {default}"
        )
        return default


def parse_int_env(env_var: str, default: int) -> int:
    """Parse integer environment variable with validation."""
    value = os.getenv(env_var)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        logger.warning(
            f"Invalid integer value '{value}' for {env_var}. "
            f"Using default: {default}"
        )
        return default


def parse_float_env(env_var: str, default: float) -> float:
    """Parse float environment variable with validation."""
    value = os.getenv(env_var)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        logger.warning(
            f"Invalid float value '{value}' for {env_var}. "
            f"Using default: {default}"
        )
        return default


@dataclass
class ModelConfig:
    """Configuration for the Whisper model."""
    name: str = "base"
    device: str = "auto"
    compute_type: str = "int8"
    download_root: Optional[str] = None


@dataclass
class TranscriptionConfig:
    """Configuration for transcription settings."""
    language: str = "es"
    beam_size: int = 5
    vad_filter: bool = True
    task: str = "transcribe"


@dataclass
class AudioConfig:
    """Configuration for audio recording."""
    sample_rate: int = 16000           # Hz (Whisper native rate)
    channels: int = 1                  # Mono
    device_id: Optional[int] = None    # None = default device
    chunk_duration: float = 0.1        # Seconds per audio chunk
    vad_enabled: bool = True           # Voice activity detection
    vad_threshold: float = 0.02        # RMS threshold for VAD
    silence_duration: float = 1.0      # Seconds of silence to trim
    normalize_audio: bool = True       # Normalize to [-1, 1]
    max_recording_duration: float = 300.0  # 5 minutes max


@dataclass
class ClipboardConfig:
    """Configuration for clipboard integration."""
    auto_copy: bool = True                # Auto-copy after transcription
    auto_paste: bool = True               # Auto-paste if available (auto-detect permissions)
    paste_delay_ms: int = 100             # Delay before paste simulation
    timeout_seconds: float = 5.0          # Command timeout
    fallback_to_file: bool = True         # ALWAYS write to temp file if clipboard fails
    fallback_path: str = "/tmp/whisper_aloud_clipboard.txt"  # Fallback file location


@dataclass
class PersistenceConfig:
    """Configuration for persistence/history."""

    # Database
    db_path: Optional[Path] = None

    # Audio archiving
    save_audio: bool = False
    audio_archive_path: Optional[Path] = None
    audio_format: str = "flac"
    deduplicate_audio: bool = True

    # Auto-cleanup
    auto_cleanup_enabled: bool = True
    auto_cleanup_days: int = 90

    # Limits
    max_entries: int = 10000

    def __post_init__(self):
        """Set default paths if not provided."""
        if self.db_path is None:
            self.db_path = Path.home() / ".local/share/whisper_aloud/history.db"
        if self.audio_archive_path is None:
            self.audio_archive_path = Path.home() / ".local/share/whisper_aloud/audio"


@dataclass
class WhisperAloudConfig:
    """Main configuration for WhisperAloud."""
    model: ModelConfig
    transcription: TranscriptionConfig
    audio: AudioConfig
    clipboard: ClipboardConfig
    persistence: Optional[PersistenceConfig] = None

    @classmethod
    def load(cls) -> 'WhisperAloudConfig':
        """Load configuration from file and environment variables."""
        # Start with defaults as dict
        config_dict = {
            "model": {
                "name": "base",
                "device": "auto",
                "compute_type": "int8",
                "download_root": None,
            },
            "transcription": {
                "language": "es",
                "beam_size": 5,
                "vad_filter": True,
                "task": "transcribe",
            },
            "audio": {
                "sample_rate": 16000,
                "channels": 1,
                "device_id": None,
                "chunk_duration": 0.1,
                "vad_enabled": True,
                "vad_threshold": 0.02,
                "silence_duration": 1.0,
                "normalize_audio": True,
                "max_recording_duration": 300.0,
            },
            "clipboard": {
                "auto_copy": True,
                "auto_paste": True,
                "paste_delay_ms": 100,
                "timeout_seconds": 5.0,
                "fallback_to_file": True,
                "fallback_path": "/tmp/whisper_aloud_clipboard.txt",
            },
            "persistence": {
                "save_audio": False,
                "audio_archive_path": None,
                "audio_format": "flac",
                "deduplicate_audio": True,
                "auto_cleanup_enabled": True,
                "auto_cleanup_days": 90,
                "max_entries": 10000,
                "db_path": None,
            },
        }

        # Load from config file if exists
        config_dir = Path.home() / ".config" / "whisper_aloud"
        config_path = config_dir / "config.json"
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    file_config = json.load(f)
                logger.info(f"Loading configuration from {config_path}")

                # Deep merge file_config into config_dict
                for section, values in file_config.items():
                    if section in config_dict:
                        config_dict[section].update(values)

            except json.JSONDecodeError as e:
                error_msg = (
                    f"Configuration file is corrupted or contains invalid JSON:\n"
                    f"  File: {config_path}\n"
                    f"  Error: {e}\n"
                    f"  Using default configuration instead.\n"
                    f"  Please fix the config file or delete it to regenerate defaults."
                )
                logger.error(error_msg)
                # Also print to stderr so user sees it
                import sys
                print(f"WARNING: {error_msg}", file=sys.stderr)

            except Exception as e:
                # Other errors (permissions, etc.)
                logger.warning(f"Failed to load config from file: {e}")

        # Override with environment variables
        config_dict["model"]["name"] = os.getenv('WHISPER_ALOUD_MODEL_NAME', config_dict["model"]["name"])
        config_dict["model"]["device"] = os.getenv('WHISPER_ALOUD_MODEL_DEVICE', config_dict["model"]["device"])
        config_dict["model"]["compute_type"] = os.getenv('WHISPER_ALOUD_MODEL_COMPUTE_TYPE', config_dict["model"]["compute_type"])
        config_dict["model"]["download_root"] = os.getenv('WHISPER_ALOUD_MODEL_DOWNLOAD_ROOT', config_dict["model"]["download_root"])

        config_dict["transcription"]["language"] = os.getenv('WHISPER_ALOUD_LANGUAGE', config_dict["transcription"]["language"])
        config_dict["transcription"]["beam_size"] = parse_int_env('WHISPER_ALOUD_BEAM_SIZE', config_dict["transcription"]["beam_size"])
        config_dict["transcription"]["vad_filter"] = parse_bool_env('WHISPER_ALOUD_VAD_FILTER', config_dict["transcription"]["vad_filter"])
        config_dict["transcription"]["task"] = os.getenv('WHISPER_ALOUD_TASK', config_dict["transcription"]["task"])

        config_dict["audio"]["sample_rate"] = parse_int_env('WHISPER_ALOUD_SAMPLE_RATE', config_dict["audio"]["sample_rate"])
        config_dict["audio"]["channels"] = parse_int_env('WHISPER_ALOUD_CHANNELS', config_dict["audio"]["channels"])
        config_dict["audio"]["device_id"] = parse_int_env('WHISPER_ALOUD_DEVICE_ID', config_dict["audio"]["device_id"]) if os.getenv('WHISPER_ALOUD_DEVICE_ID') else config_dict["audio"]["device_id"]
        config_dict["audio"]["chunk_duration"] = parse_float_env('WHISPER_ALOUD_CHUNK_DURATION', config_dict["audio"]["chunk_duration"])
        config_dict["audio"]["vad_enabled"] = parse_bool_env('WHISPER_ALOUD_VAD_ENABLED', config_dict["audio"]["vad_enabled"])
        config_dict["audio"]["vad_threshold"] = parse_float_env('WHISPER_ALOUD_VAD_THRESHOLD', config_dict["audio"]["vad_threshold"])
        config_dict["audio"]["silence_duration"] = parse_float_env('WHISPER_ALOUD_SILENCE_DURATION', config_dict["audio"]["silence_duration"])
        config_dict["audio"]["normalize_audio"] = parse_bool_env('WHISPER_ALOUD_NORMALIZE_AUDIO', config_dict["audio"]["normalize_audio"])
        config_dict["audio"]["max_recording_duration"] = parse_float_env('WHISPER_ALOUD_MAX_RECORDING_DURATION', config_dict["audio"]["max_recording_duration"])

        config_dict["clipboard"]["auto_copy"] = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_AUTO_COPY', config_dict["clipboard"]["auto_copy"])
        config_dict["clipboard"]["auto_paste"] = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE', config_dict["clipboard"]["auto_paste"])
        config_dict["clipboard"]["paste_delay_ms"] = parse_int_env('WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS', config_dict["clipboard"]["paste_delay_ms"])
        config_dict["clipboard"]["timeout_seconds"] = parse_float_env('WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS', config_dict["clipboard"]["timeout_seconds"])
        config_dict["clipboard"]["fallback_to_file"] = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_FALLBACK_TO_FILE', config_dict["clipboard"]["fallback_to_file"])
        config_dict["clipboard"]["fallback_path"] = os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_PATH', config_dict["clipboard"]["fallback_path"])

        config_dict["persistence"]["db_path"] = Path(os.getenv('WHISPER_ALOUD_DB_PATH')) if os.getenv('WHISPER_ALOUD_DB_PATH') else config_dict["persistence"]["db_path"]
        config_dict["persistence"]["save_audio"] = parse_bool_env('WHISPER_ALOUD_SAVE_AUDIO', config_dict["persistence"]["save_audio"])
        config_dict["persistence"]["audio_archive_path"] = Path(os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE')) if os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE') else config_dict["persistence"]["audio_archive_path"]
        config_dict["persistence"]["audio_format"] = os.getenv('WHISPER_ALOUD_AUDIO_FORMAT', config_dict["persistence"]["audio_format"])
        config_dict["persistence"]["deduplicate_audio"] = parse_bool_env('WHISPER_ALOUD_DEDUPLICATE_AUDIO', config_dict["persistence"]["deduplicate_audio"])
        config_dict["persistence"]["auto_cleanup_enabled"] = parse_bool_env('WHISPER_ALOUD_AUTO_CLEANUP', config_dict["persistence"]["auto_cleanup_enabled"])
        config_dict["persistence"]["auto_cleanup_days"] = parse_int_env('WHISPER_ALOUD_CLEANUP_DAYS', config_dict["persistence"]["auto_cleanup_days"])
        config_dict["persistence"]["max_entries"] = parse_int_env('WHISPER_ALOUD_MAX_ENTRIES', config_dict["persistence"]["max_entries"])

        # Sanitize language code
        sanitized_language = sanitize_language_code(config_dict["transcription"]["language"])
        if sanitized_language is None:
            logger.warning(f"Invalid language '{config_dict['transcription']['language']}' in config, resetting to 'es'")
            config_dict["transcription"]["language"] = 'es'
        else:
            config_dict["transcription"]["language"] = sanitized_language

        # Instantiate dataclasses once
        config = cls(
            model=ModelConfig(**config_dict["model"]),
            transcription=TranscriptionConfig(**config_dict["transcription"]),
            audio=AudioConfig(**config_dict["audio"]),
            clipboard=ClipboardConfig(**config_dict["clipboard"]),
            persistence=PersistenceConfig(**config_dict["persistence"])
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Validate configuration values."""
        # Validate model name
        valid_models = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
        if self.model.name not in valid_models:
            raise ConfigurationError(
                f"Invalid model name '{self.model.name}'. "
                f"Valid options: {', '.join(valid_models)}"
            )

        # Validate device
        valid_devices = ["auto", "cpu", "cuda"]
        if self.model.device not in valid_devices:
            raise ConfigurationError(
                f"Invalid device '{self.model.device}'. "
                f"Valid options: {', '.join(valid_devices)}"
            )

        # Validate compute type
        valid_compute_types = ["int8", "float16", "float32"]
        if self.model.compute_type not in valid_compute_types:
            raise ConfigurationError(
                f"Invalid compute type '{self.model.compute_type}'. "
                f"Valid options: {', '.join(valid_compute_types)}"
            )

        # Validate language (sanitize invalid values)
        sanitized = sanitize_language_code(self.transcription.language)
        if sanitized is None:
            logger.warning(f"Invalid language '{self.transcription.language}' in config, resetting to 'es'")
            self.transcription.language = 'es'
        elif sanitized != self.transcription.language:
            logger.warning(f"Invalid language '{self.transcription.language}' in config, sanitized to '{sanitized}'")
            self.transcription.language = sanitized

        # Validate beam size
        if not (1 <= self.transcription.beam_size <= 10):
            raise ConfigurationError(
                f"Invalid beam size {self.transcription.beam_size}. "
                "Must be between 1 and 10"
            )

        # Validate task
        valid_tasks = ["transcribe", "translate"]
        if self.transcription.task not in valid_tasks:
            raise ConfigurationError(
                f"Invalid task '{self.transcription.task}'. "
                f"Valid options: {', '.join(valid_tasks)}"
            )

        # Validate audio configuration
        if not (8000 <= self.audio.sample_rate <= 48000):
            raise ConfigurationError(
                f"Invalid sample rate {self.audio.sample_rate}. "
                "Must be between 8000 and 48000 Hz"
            )

        if self.audio.channels not in (1, 2):
            raise ConfigurationError(
                f"Invalid channels {self.audio.channels}. "
                "Must be 1 (mono) or 2 (stereo)"
            )

        if not (0.0 < self.audio.vad_threshold < 1.0):
            raise ConfigurationError(
                f"Invalid VAD threshold {self.audio.vad_threshold}. "
                "Must be between 0.0 and 1.0"
            )

        if not (0.01 <= self.audio.chunk_duration <= 1.0):
            raise ConfigurationError(
                f"Invalid chunk duration {self.audio.chunk_duration}. "
                "Must be between 0.01 and 1.0 seconds"
            )

        # Validate clipboard configuration
        if self.clipboard.timeout_seconds <= 0:
            raise ConfigurationError(
                f"Invalid clipboard timeout {self.clipboard.timeout_seconds}. "
                "Must be greater than 0"
            )

        if self.clipboard.paste_delay_ms < 0:
            raise ConfigurationError(
                f"Invalid paste delay {self.clipboard.paste_delay_ms}. "
                "Must be >= 0"
            )