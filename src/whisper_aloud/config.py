"""Configuration management for WhisperAloud."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .exceptions import ConfigurationError
from .utils.validation_helpers import sanitize_language_code

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration Path Constants
# =============================================================================

CONFIG_DIR = Path.home() / ".config" / "whisper_aloud"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / "whisper_aloud"


# =============================================================================
# Environment Variable Parsing Helpers
# =============================================================================

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
        logger.warning(f"Invalid integer value '{value}' for {env_var}. Using default: {default}")
        return default


def parse_float_env(env_var: str, default: float) -> float:
    """Parse float environment variable with validation."""
    value = os.getenv(env_var)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        logger.warning(f"Invalid float value '{value}' for {env_var}. Using default: {default}")
        return default


# =============================================================================
# Configuration Dataclasses - SINGLE SOURCE OF TRUTH FOR DEFAULTS
# =============================================================================

@dataclass
class ModelConfig:
    """Configuration for the Whisper model."""
    name: str = "base"
    device: str = "auto"
    compute_type: str = "int8"
    download_root: Optional[str] = None


# Fields that trigger model reload when changed (class-level constant)
MODEL_RELOAD_FIELDS: Set[str] = {"name", "device", "compute_type"}


@dataclass
class TranscriptionConfig:
    """Configuration for transcription settings."""
    language: str = "es"
    beam_size: int = 5
    vad_filter: bool = True
    task: str = "transcribe"


# Language change may trigger model reload (for optimization)
TRANSCRIPTION_RELOAD_FIELDS: Set[str] = {"language"}


@dataclass
class AudioConfig:
    """Configuration for audio recording."""
    sample_rate: int = 16000
    channels: int = 1
    device_id: Optional[int] = None
    chunk_duration: float = 0.1
    vad_enabled: bool = True
    vad_threshold: float = 0.02
    silence_duration: float = 1.0
    normalize_audio: bool = True
    max_recording_duration: float = 300.0


# Fields that require recorder re-initialization
AUDIO_REINIT_FIELDS: Set[str] = {"device_id", "sample_rate", "channels"}


@dataclass
class ClipboardConfig:
    """Configuration for clipboard integration."""
    auto_copy: bool = True
    auto_paste: bool = True
    paste_delay_ms: int = 100
    timeout_seconds: float = 5.0
    fallback_to_file: bool = True
    fallback_path: str = "/tmp/whisper_aloud_clipboard.txt"


@dataclass
class NotificationConfig:
    """Configuration for desktop OSD notifications."""
    enabled: bool = True
    recording_started: bool = True
    recording_stopped: bool = True
    transcription_completed: bool = True
    error: bool = True


@dataclass
class PersistenceConfig:
    """Configuration for persistence/history."""
    db_path: Optional[Path] = None
    save_audio: bool = False
    audio_archive_path: Optional[Path] = None
    audio_format: str = "flac"
    deduplicate_audio: bool = True
    auto_cleanup_enabled: bool = True
    auto_cleanup_days: int = 90
    max_entries: int = 10000

    def __post_init__(self):
        """Set default paths if not provided."""
        if self.db_path is None:
            self.db_path = DATA_DIR / "history.db"
        if self.audio_archive_path is None:
            self.audio_archive_path = DATA_DIR / "audio"


@dataclass
class HotkeyConfig:
    """Configuration for global hotkey."""
    toggle_recording: str = "<Super><Alt>r"
    cancel_recording: str = "<Super><Alt>Escape"


@dataclass
class AudioProcessingConfig:
    """Configuration for audio processing pipeline."""
    noise_gate_enabled: bool = True
    noise_gate_threshold_db: float = -40.0
    agc_enabled: bool = True
    agc_target_db: float = -18.0
    agc_max_gain_db: float = 20.0
    denoising_enabled: bool = True
    denoising_strength: float = 0.5
    limiter_enabled: bool = True
    limiter_ceiling_db: float = -1.0


# =============================================================================
# Main Configuration Class
# =============================================================================

@dataclass
class WhisperAloudConfig:
    """Main configuration for WhisperAloud."""
    model: ModelConfig = field(default_factory=ModelConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    audio_processing: AudioProcessingConfig = field(default_factory=AudioProcessingConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for serialization."""
        return {
            "model": {
                "name": self.model.name,
                "device": self.model.device,
                "compute_type": self.model.compute_type,
                "download_root": self.model.download_root,
            },
            "transcription": {
                "language": self.transcription.language,
                "beam_size": self.transcription.beam_size,
                "vad_filter": self.transcription.vad_filter,
                "task": self.transcription.task,
            },
            "audio": {
                "sample_rate": self.audio.sample_rate,
                "channels": self.audio.channels,
                "device_id": self.audio.device_id,
                "chunk_duration": self.audio.chunk_duration,
                "vad_enabled": self.audio.vad_enabled,
                "vad_threshold": self.audio.vad_threshold,
                "silence_duration": self.audio.silence_duration,
                "normalize_audio": self.audio.normalize_audio,
                "max_recording_duration": self.audio.max_recording_duration,
            },
            "clipboard": {
                "auto_copy": self.clipboard.auto_copy,
                "auto_paste": self.clipboard.auto_paste,
                "paste_delay_ms": self.clipboard.paste_delay_ms,
                "timeout_seconds": self.clipboard.timeout_seconds,
                "fallback_to_file": self.clipboard.fallback_to_file,
                "fallback_path": self.clipboard.fallback_path,
            },
            "notifications": {
                "enabled": self.notifications.enabled,
                "recording_started": self.notifications.recording_started,
                "recording_stopped": self.notifications.recording_stopped,
                "transcription_completed": self.notifications.transcription_completed,
                "error": self.notifications.error,
            },
            "persistence": {
                "db_path": str(self.persistence.db_path) if self.persistence.db_path else None,
                "save_audio": self.persistence.save_audio,
                "audio_archive_path": str(self.persistence.audio_archive_path) if self.persistence.audio_archive_path else None,
                "audio_format": self.persistence.audio_format,
                "deduplicate_audio": self.persistence.deduplicate_audio,
                "auto_cleanup_enabled": self.persistence.auto_cleanup_enabled,
                "auto_cleanup_days": self.persistence.auto_cleanup_days,
                "max_entries": self.persistence.max_entries,
            },
            "audio_processing": {
                "noise_gate_enabled": self.audio_processing.noise_gate_enabled,
                "noise_gate_threshold_db": self.audio_processing.noise_gate_threshold_db,
                "agc_enabled": self.audio_processing.agc_enabled,
                "agc_target_db": self.audio_processing.agc_target_db,
                "agc_max_gain_db": self.audio_processing.agc_max_gain_db,
                "denoising_enabled": self.audio_processing.denoising_enabled,
                "denoising_strength": self.audio_processing.denoising_strength,
                "limiter_enabled": self.audio_processing.limiter_enabled,
                "limiter_ceiling_db": self.audio_processing.limiter_ceiling_db,
            },
            "hotkey": {
                "toggle_recording": self.hotkey.toggle_recording,
                "cancel_recording": self.hotkey.cancel_recording,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WhisperAloudConfig':
        """Create config from dictionary."""
        config = cls()

        if "model" in data:
            for key, value in data["model"].items():
                if hasattr(config.model, key):
                    setattr(config.model, key, value)

        if "transcription" in data:
            for key, value in data["transcription"].items():
                if hasattr(config.transcription, key):
                    setattr(config.transcription, key, value)

        if "audio" in data:
            for key, value in data["audio"].items():
                if hasattr(config.audio, key):
                    setattr(config.audio, key, value)

        if "clipboard" in data:
            for key, value in data["clipboard"].items():
                if hasattr(config.clipboard, key):
                    setattr(config.clipboard, key, value)

        if "notifications" in data:
            for key, value in data["notifications"].items():
                if hasattr(config.notifications, key):
                    setattr(config.notifications, key, value)

        if "persistence" in data:
            for key, value in data["persistence"].items():
                if hasattr(config.persistence, key):
                    if key in ("db_path", "audio_archive_path") and value:
                        value = Path(value)
                    setattr(config.persistence, key, value)

        if "audio_processing" in data:
            for key, value in data["audio_processing"].items():
                if hasattr(config.audio_processing, key):
                    setattr(config.audio_processing, key, value)

        if "hotkey" in data:
            for key, value in data["hotkey"].items():
                if hasattr(config.hotkey, key):
                    setattr(config.hotkey, key, value)

        return config

    def copy(self) -> 'WhisperAloudConfig':
        """Create a deep copy of the configuration."""
        return WhisperAloudConfig.from_dict(self.to_dict())

    @classmethod
    def load(cls) -> 'WhisperAloudConfig':
        """
        Load configuration with priority: defaults < file < env vars.

        Returns:
            WhisperAloudConfig instance
        """
        # Compute path dynamically to support HOME changes in tests
        config_file = Path.home() / ".config" / "whisper_aloud" / "config.json"

        # Start with defaults from dataclass
        config = cls()

        # Load from config file if exists
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    file_config = json.load(f)
                logger.info(f"Loading configuration from {config_file}")
                config = cls.from_dict(file_config)
            except json.JSONDecodeError as e:
                logger.error(f"Config file corrupted: {e}. Using defaults.")
            except Exception as e:
                logger.warning(f"Failed to load config from file: {e}")

        # Apply environment variable overrides
        config._apply_env_overrides()

        # Sanitize and validate
        config._sanitize()
        config.validate()

        return config

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides to config."""
        # Model
        self.model.name = os.getenv('WHISPER_ALOUD_MODEL_NAME', self.model.name)
        self.model.device = os.getenv('WHISPER_ALOUD_MODEL_DEVICE', self.model.device)
        self.model.compute_type = os.getenv('WHISPER_ALOUD_MODEL_COMPUTE_TYPE', self.model.compute_type)
        self.model.download_root = os.getenv('WHISPER_ALOUD_MODEL_DOWNLOAD_ROOT', self.model.download_root)

        # Transcription
        self.transcription.language = os.getenv('WHISPER_ALOUD_LANGUAGE', self.transcription.language)
        self.transcription.beam_size = parse_int_env('WHISPER_ALOUD_BEAM_SIZE', self.transcription.beam_size)
        self.transcription.vad_filter = parse_bool_env('WHISPER_ALOUD_VAD_FILTER', self.transcription.vad_filter)
        self.transcription.task = os.getenv('WHISPER_ALOUD_TASK', self.transcription.task)

        # Audio
        self.audio.sample_rate = parse_int_env('WHISPER_ALOUD_SAMPLE_RATE', self.audio.sample_rate)
        self.audio.channels = parse_int_env('WHISPER_ALOUD_CHANNELS', self.audio.channels)
        if os.getenv('WHISPER_ALOUD_DEVICE_ID'):
            self.audio.device_id = parse_int_env('WHISPER_ALOUD_DEVICE_ID', self.audio.device_id)
        self.audio.chunk_duration = parse_float_env('WHISPER_ALOUD_CHUNK_DURATION', self.audio.chunk_duration)
        self.audio.vad_enabled = parse_bool_env('WHISPER_ALOUD_VAD_ENABLED', self.audio.vad_enabled)
        self.audio.vad_threshold = parse_float_env('WHISPER_ALOUD_VAD_THRESHOLD', self.audio.vad_threshold)
        self.audio.silence_duration = parse_float_env('WHISPER_ALOUD_SILENCE_DURATION', self.audio.silence_duration)
        self.audio.normalize_audio = parse_bool_env('WHISPER_ALOUD_NORMALIZE_AUDIO', self.audio.normalize_audio)
        self.audio.max_recording_duration = parse_float_env('WHISPER_ALOUD_MAX_RECORDING_DURATION', self.audio.max_recording_duration)

        # Clipboard
        self.clipboard.auto_copy = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_AUTO_COPY', self.clipboard.auto_copy)
        self.clipboard.auto_paste = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE', self.clipboard.auto_paste)
        self.clipboard.paste_delay_ms = parse_int_env('WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS', self.clipboard.paste_delay_ms)
        self.clipboard.timeout_seconds = parse_float_env('WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS', self.clipboard.timeout_seconds)
        self.clipboard.fallback_to_file = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_FALLBACK_TO_FILE', self.clipboard.fallback_to_file)
        self.clipboard.fallback_path = os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_PATH', self.clipboard.fallback_path)

        # Notifications
        self.notifications.enabled = parse_bool_env('WHISPER_ALOUD_NOTIFICATIONS_ENABLED', self.notifications.enabled)
        self.notifications.recording_started = parse_bool_env(
            'WHISPER_ALOUD_NOTIFICATIONS_RECORDING_STARTED', self.notifications.recording_started
        )
        self.notifications.recording_stopped = parse_bool_env(
            'WHISPER_ALOUD_NOTIFICATIONS_RECORDING_STOPPED', self.notifications.recording_stopped
        )
        self.notifications.transcription_completed = parse_bool_env(
            'WHISPER_ALOUD_NOTIFICATIONS_TRANSCRIPTION_COMPLETED', self.notifications.transcription_completed
        )
        self.notifications.error = parse_bool_env('WHISPER_ALOUD_NOTIFICATIONS_ERROR', self.notifications.error)

        # Persistence
        if os.getenv('WHISPER_ALOUD_DB_PATH'):
            self.persistence.db_path = Path(os.getenv('WHISPER_ALOUD_DB_PATH'))
        self.persistence.save_audio = parse_bool_env('WHISPER_ALOUD_SAVE_AUDIO', self.persistence.save_audio)
        if os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE'):
            self.persistence.audio_archive_path = Path(os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE'))
        self.persistence.audio_format = os.getenv('WHISPER_ALOUD_AUDIO_FORMAT', self.persistence.audio_format)
        self.persistence.deduplicate_audio = parse_bool_env('WHISPER_ALOUD_DEDUPLICATE_AUDIO', self.persistence.deduplicate_audio)
        self.persistence.auto_cleanup_enabled = parse_bool_env('WHISPER_ALOUD_AUTO_CLEANUP', self.persistence.auto_cleanup_enabled)
        self.persistence.auto_cleanup_days = parse_int_env('WHISPER_ALOUD_CLEANUP_DAYS', self.persistence.auto_cleanup_days)
        self.persistence.max_entries = parse_int_env('WHISPER_ALOUD_MAX_ENTRIES', self.persistence.max_entries)

    def _sanitize(self) -> None:
        """Sanitize configuration values."""
        # Keep a stable representation for auto-detected language.
        if self.transcription.language is None:
            self.transcription.language = "auto"
            return

        # Sanitize language code
        sanitized = sanitize_language_code(self.transcription.language)
        if sanitized is None:
            logger.warning(f"Invalid language '{self.transcription.language}', resetting to 'es'")
            self.transcription.language = 'es'
        elif sanitized != self.transcription.language:
            self.transcription.language = sanitized

    def validate(self) -> None:
        """Validate configuration values."""
        # Validate model name (faster-whisper supported models)
        valid_models = [
            "tiny", "base", "small", "medium",
            "large-v1", "large-v2", "large-v3", "large",  # large is alias for large-v3
            "large-v3-turbo", "turbo",  # turbo variants
        ]
        if self.model.name not in valid_models:
            raise ConfigurationError(f"Invalid model name '{self.model.name}'. Valid: {', '.join(valid_models)}")

        # Validate device
        valid_devices = ["auto", "cpu", "cuda"]
        if self.model.device not in valid_devices:
            raise ConfigurationError(f"Invalid device '{self.model.device}'. Valid: {', '.join(valid_devices)}")

        # Validate compute type
        valid_compute_types = ["int8", "float16", "float32"]
        if self.model.compute_type not in valid_compute_types:
            raise ConfigurationError(f"Invalid compute type '{self.model.compute_type}'. Valid: {', '.join(valid_compute_types)}")

        # Validate language ("auto" or 2-letter code)
        sanitized_language = sanitize_language_code(self.transcription.language)
        if sanitized_language is None:
            raise ConfigurationError(
                f"Invalid language '{self.transcription.language}'. "
                "Valid: 'auto' or 2-letter ISO code (e.g., 'en', 'es')"
            )

        # Validate beam size
        if not (1 <= self.transcription.beam_size <= 10):
            raise ConfigurationError(f"Invalid beam size {self.transcription.beam_size}. Must be 1-10")

        # Validate task
        valid_tasks = ["transcribe", "translate"]
        if self.transcription.task not in valid_tasks:
            raise ConfigurationError(f"Invalid task '{self.transcription.task}'. Valid: {', '.join(valid_tasks)}")

        # Validate audio configuration
        if not (8000 <= self.audio.sample_rate <= 48000):
            raise ConfigurationError(f"Invalid sample rate {self.audio.sample_rate}. Must be 8000-48000 Hz")

        if self.audio.channels not in (1, 2):
            raise ConfigurationError(f"Invalid channels {self.audio.channels}. Must be 1 or 2")

        if not (0.0 < self.audio.vad_threshold < 1.0):
            raise ConfigurationError(f"Invalid VAD threshold {self.audio.vad_threshold}. Must be 0.0-1.0")

        if not (0.01 <= self.audio.chunk_duration <= 1.0):
            raise ConfigurationError(f"Invalid chunk duration {self.audio.chunk_duration}. Must be 0.01-1.0s")

        # Validate clipboard configuration
        if self.clipboard.timeout_seconds <= 0:
            raise ConfigurationError(f"Invalid clipboard timeout {self.clipboard.timeout_seconds}. Must be > 0")

        if self.clipboard.paste_delay_ms < 0:
            raise ConfigurationError(f"Invalid paste delay {self.clipboard.paste_delay_ms}. Must be >= 0")

    def save(self) -> Path:
        """Save configuration to file."""
        # Compute path dynamically to support HOME changes in tests
        config_dir = Path.home() / ".config" / "whisper_aloud"
        config_file = config_dir / "config.json"
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Configuration saved to {config_file}")
        return config_file


# =============================================================================
# Change Detection
# =============================================================================

@dataclass
class ConfigChanges:
    """Describes what changed between two configurations."""
    requires_model_reload: bool = False
    requires_audio_reinit: bool = False
    changed_sections: Set[str] = field(default_factory=set)
    changed_fields: Dict[str, Set[str]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """True if any changes detected."""
        return bool(self.changed_sections)

    def __str__(self) -> str:
        parts = []
        if self.requires_model_reload:
            parts.append("model reload required")
        if self.requires_audio_reinit:
            parts.append("audio reinit required")
        if self.changed_sections:
            parts.append(f"changed: {', '.join(self.changed_sections)}")
        return "; ".join(parts) if parts else "no changes"


def detect_config_changes(old: WhisperAloudConfig, new: WhisperAloudConfig) -> ConfigChanges:
    """
    Detect changes between two configurations.

    Args:
        old: Previous configuration
        new: New configuration

    Returns:
        ConfigChanges describing what changed
    """
    changes = ConfigChanges()

    # Check model changes
    model_changes = set()
    if old.model.name != new.model.name:
        model_changes.add("name")
    if old.model.device != new.model.device:
        model_changes.add("device")
    if old.model.compute_type != new.model.compute_type:
        model_changes.add("compute_type")

    if model_changes:
        changes.changed_sections.add("model")
        changes.changed_fields["model"] = model_changes
        # Check if any reload-triggering fields changed
        if model_changes & MODEL_RELOAD_FIELDS:
            changes.requires_model_reload = True

    # Check transcription changes
    trans_changes = set()
    if old.transcription.language != new.transcription.language:
        trans_changes.add("language")
    if old.transcription.beam_size != new.transcription.beam_size:
        trans_changes.add("beam_size")
    if old.transcription.task != new.transcription.task:
        trans_changes.add("task")

    if trans_changes:
        changes.changed_sections.add("transcription")
        changes.changed_fields["transcription"] = trans_changes
        # Language change triggers model reload for optimization
        if trans_changes & TRANSCRIPTION_RELOAD_FIELDS:
            changes.requires_model_reload = True

    # Check audio changes
    audio_changes = set()
    if old.audio.device_id != new.audio.device_id:
        audio_changes.add("device_id")
    if old.audio.sample_rate != new.audio.sample_rate:
        audio_changes.add("sample_rate")
    if old.audio.channels != new.audio.channels:
        audio_changes.add("channels")
    if old.audio.vad_enabled != new.audio.vad_enabled:
        audio_changes.add("vad_enabled")
    if old.audio.vad_threshold != new.audio.vad_threshold:
        audio_changes.add("vad_threshold")
    if old.audio.normalize_audio != new.audio.normalize_audio:
        audio_changes.add("normalize_audio")

    if audio_changes:
        changes.changed_sections.add("audio")
        changes.changed_fields["audio"] = audio_changes
        # Check if audio reinit is needed
        if audio_changes & AUDIO_REINIT_FIELDS:
            changes.requires_audio_reinit = True

    # Check clipboard changes
    clipboard_changes = set()
    if old.clipboard.auto_copy != new.clipboard.auto_copy:
        clipboard_changes.add("auto_copy")
    if old.clipboard.auto_paste != new.clipboard.auto_paste:
        clipboard_changes.add("auto_paste")

    if clipboard_changes:
        changes.changed_sections.add("clipboard")
        changes.changed_fields["clipboard"] = clipboard_changes

    # Check notification changes
    notification_changes = set()
    if old.notifications.enabled != new.notifications.enabled:
        notification_changes.add("enabled")
    if old.notifications.recording_started != new.notifications.recording_started:
        notification_changes.add("recording_started")
    if old.notifications.recording_stopped != new.notifications.recording_stopped:
        notification_changes.add("recording_stopped")
    if old.notifications.transcription_completed != new.notifications.transcription_completed:
        notification_changes.add("transcription_completed")
    if old.notifications.error != new.notifications.error:
        notification_changes.add("error")

    if notification_changes:
        changes.changed_sections.add("notifications")
        changes.changed_fields["notifications"] = notification_changes

    # Check persistence changes
    persistence_changes = set()
    if old.persistence.save_audio != new.persistence.save_audio:
        persistence_changes.add("save_audio")
    if old.persistence.auto_cleanup_enabled != new.persistence.auto_cleanup_enabled:
        persistence_changes.add("auto_cleanup_enabled")

    if persistence_changes:
        changes.changed_sections.add("persistence")
        changes.changed_fields["persistence"] = persistence_changes

    return changes
