"""Configuration management for WhisperAloud."""

import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from .exceptions import ConfigurationError


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
        """Load configuration from environment variables."""
        model_config = ModelConfig(
            name=os.getenv('WHISPER_ALOUD_MODEL_NAME', 'base'),
            device=os.getenv('WHISPER_ALOUD_MODEL_DEVICE', 'auto'),
            compute_type=os.getenv('WHISPER_ALOUD_MODEL_COMPUTE_TYPE', 'int8'),
            download_root=os.getenv('WHISPER_ALOUD_MODEL_DOWNLOAD_ROOT'),
        )

        transcription_config = TranscriptionConfig(
            language=os.getenv('WHISPER_ALOUD_LANGUAGE', 'es'),
            beam_size=int(os.getenv('WHISPER_ALOUD_BEAM_SIZE', '5')),
            vad_filter=os.getenv('WHISPER_ALOUD_VAD_FILTER', 'true').lower() == 'true',
            task=os.getenv('WHISPER_ALOUD_TASK', 'transcribe'),
        )

        audio_config = AudioConfig(
            sample_rate=int(os.getenv('WHISPER_ALOUD_SAMPLE_RATE', '16000')),
            channels=int(os.getenv('WHISPER_ALOUD_CHANNELS', '1')),
            device_id=int(os.getenv('WHISPER_ALOUD_DEVICE_ID')) if os.getenv('WHISPER_ALOUD_DEVICE_ID') else None,
            chunk_duration=float(os.getenv('WHISPER_ALOUD_CHUNK_DURATION', '0.1')),
            vad_enabled=os.getenv('WHISPER_ALOUD_VAD_ENABLED', 'true').lower() == 'true',
            vad_threshold=float(os.getenv('WHISPER_ALOUD_VAD_THRESHOLD', '0.02')),
            silence_duration=float(os.getenv('WHISPER_ALOUD_SILENCE_DURATION', '1.0')),
            normalize_audio=os.getenv('WHISPER_ALOUD_NORMALIZE_AUDIO', 'true').lower() == 'true',
            max_recording_duration=float(os.getenv('WHISPER_ALOUD_MAX_RECORDING_DURATION', '300.0')),
        )

        clipboard_config = ClipboardConfig(
            auto_copy=os.getenv('WHISPER_ALOUD_CLIPBOARD_AUTO_COPY', 'true').lower() == 'true',
            auto_paste=os.getenv('WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE', 'true').lower() == 'true',
            paste_delay_ms=int(os.getenv('WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS', '100')),
            timeout_seconds=float(os.getenv('WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS', '5.0')),
            fallback_to_file=os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_TO_FILE', 'true').lower() == 'true',
            fallback_path=os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_PATH', '/tmp/whisper_aloud_clipboard.txt'),
        )

        persistence_config = PersistenceConfig(
            db_path=Path(os.getenv('WHISPER_ALOUD_DB_PATH')) if os.getenv('WHISPER_ALOUD_DB_PATH') else None,
            save_audio=os.getenv('WHISPER_ALOUD_SAVE_AUDIO', 'false').lower() == 'true',
            audio_archive_path=Path(os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE')) if os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE') else None,
            audio_format=os.getenv('WHISPER_ALOUD_AUDIO_FORMAT', 'flac'),
            deduplicate_audio=os.getenv('WHISPER_ALOUD_DEDUPLICATE_AUDIO', 'true').lower() == 'true',
            auto_cleanup_enabled=os.getenv('WHISPER_ALOUD_AUTO_CLEANUP', 'true').lower() == 'true',
            auto_cleanup_days=int(os.getenv('WHISPER_ALOUD_CLEANUP_DAYS', '90')),
            max_entries=int(os.getenv('WHISPER_ALOUD_MAX_ENTRIES', '10000'))
        )

        config = cls(
            model=model_config,
            transcription=transcription_config,
            audio=audio_config,
            clipboard=clipboard_config,
            persistence=persistence_config
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

        # Validate language (basic check)
        if not isinstance(self.transcription.language, str) or len(self.transcription.language) < 2:
            raise ConfigurationError(
                f"Invalid language '{self.transcription.language}'. "
                "Use ISO language code (e.g., 'en', 'es') or 'auto'"
            )

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