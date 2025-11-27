"""Configuration management for WhisperAloud."""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from .exceptions import ConfigurationError

logger = logging.getLogger(__name__)


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
        # Start with defaults
        model_config = ModelConfig(
            name='base',
            device='auto',
            compute_type='int8',
            download_root=None,
        )

        transcription_config = TranscriptionConfig(
            language='es',
            beam_size=5,
            vad_filter=True,
            task='transcribe',
        )

        audio_config = AudioConfig(
            sample_rate=16000,
            channels=1,
            device_id=None,
            chunk_duration=0.1,
            vad_enabled=True,
            vad_threshold=0.02,
            silence_duration=1.0,
            normalize_audio=True,
            max_recording_duration=300.0,
        )

        clipboard_config = ClipboardConfig(
            auto_copy=True,
            auto_paste=True,
            paste_delay_ms=100,
            timeout_seconds=5.0,
            fallback_to_file=True,
            fallback_path='/tmp/whisper_aloud_clipboard.txt',
        )

        persistence_config = PersistenceConfig()

        # Load from config file if exists
        config_dir = Path.home() / ".config" / "whisper_aloud"
        config_path = config_dir / "config.json"
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    file_config = json.load(f)
                logger.info(f"Loading configuration from {config_path}")

                # Override with file values
                if "model" in file_config:
                    model_dict = file_config["model"]
                    if "name" in model_dict:
                        model_config.name = model_dict["name"]
                    if "device" in model_dict:
                        model_config.device = model_dict["device"]
                    if "compute_type" in model_dict:
                        model_config.compute_type = model_dict["compute_type"]
                    if "download_root" in model_dict:
                        model_config.download_root = model_dict["download_root"]

                if "transcription" in file_config:
                    trans_dict = file_config["transcription"]
                    if "language" in trans_dict:
                        transcription_config.language = trans_dict["language"]
                    if "beam_size" in trans_dict:
                        transcription_config.beam_size = int(trans_dict["beam_size"])
                    if "vad_filter" in trans_dict:
                        transcription_config.vad_filter = trans_dict["vad_filter"]
                    if "task" in trans_dict:
                        transcription_config.task = trans_dict["task"]

                if "audio" in file_config:
                    audio_dict = file_config["audio"]
                    if "sample_rate" in audio_dict:
                        audio_config.sample_rate = int(audio_dict["sample_rate"])
                    if "channels" in audio_dict:
                        audio_config.channels = int(audio_dict["channels"])
                    if "device_id" in audio_dict:
                        audio_config.device_id = audio_dict["device_id"]
                    if "chunk_duration" in audio_dict:
                        audio_config.chunk_duration = float(audio_dict["chunk_duration"])
                    if "vad_enabled" in audio_dict:
                        audio_config.vad_enabled = audio_dict["vad_enabled"]
                    if "vad_threshold" in audio_dict:
                        audio_config.vad_threshold = float(audio_dict["vad_threshold"])
                    if "silence_duration" in audio_dict:
                        audio_config.silence_duration = float(audio_dict["silence_duration"])
                    if "normalize_audio" in audio_dict:
                        audio_config.normalize_audio = audio_dict["normalize_audio"]
                    if "max_recording_duration" in audio_dict:
                        audio_config.max_recording_duration = float(audio_dict["max_recording_duration"])

                if "clipboard" in file_config:
                    clip_dict = file_config["clipboard"]
                    if "auto_copy" in clip_dict:
                        clipboard_config.auto_copy = clip_dict["auto_copy"]
                    if "auto_paste" in clip_dict:
                        clipboard_config.auto_paste = clip_dict["auto_paste"]
                    if "paste_delay_ms" in clip_dict:
                        clipboard_config.paste_delay_ms = int(clip_dict["paste_delay_ms"])
                    if "timeout_seconds" in clip_dict:
                        clipboard_config.timeout_seconds = float(clip_dict["timeout_seconds"])
                    if "fallback_to_file" in clip_dict:
                        clipboard_config.fallback_to_file = clip_dict["fallback_to_file"]
                    if "fallback_path" in clip_dict:
                        clipboard_config.fallback_path = clip_dict["fallback_path"]

                if "persistence" in file_config:
                    pers_dict = file_config["persistence"]
                    if "save_audio" in pers_dict:
                        persistence_config.save_audio = pers_dict["save_audio"]
                    if "deduplicate_audio" in pers_dict:
                        persistence_config.deduplicate_audio = pers_dict["deduplicate_audio"]
                    if "auto_cleanup_enabled" in pers_dict:
                        persistence_config.auto_cleanup_enabled = pers_dict["auto_cleanup_enabled"]
                    if "auto_cleanup_days" in pers_dict:
                        persistence_config.auto_cleanup_days = int(pers_dict["auto_cleanup_days"])
                    if "max_entries" in pers_dict:
                        persistence_config.max_entries = int(pers_dict["max_entries"])
                    if "db_path" in pers_dict and pers_dict["db_path"]:
                        persistence_config.db_path = Path(pers_dict["db_path"])
                    if "audio_archive_path" in pers_dict and pers_dict["audio_archive_path"]:
                        persistence_config.audio_archive_path = Path(pers_dict["audio_archive_path"])

            except Exception as e:
                logger.warning(f"Failed to load config from file: {e}")

        # Override with environment variables
        model_config = ModelConfig(
            name=os.getenv('WHISPER_ALOUD_MODEL_NAME', model_config.name),
            device=os.getenv('WHISPER_ALOUD_MODEL_DEVICE', model_config.device),
            compute_type=os.getenv('WHISPER_ALOUD_MODEL_COMPUTE_TYPE', model_config.compute_type),
            download_root=os.getenv('WHISPER_ALOUD_MODEL_DOWNLOAD_ROOT', model_config.download_root),
        )

        transcription_config = TranscriptionConfig(
            language=os.getenv('WHISPER_ALOUD_LANGUAGE', transcription_config.language),
            beam_size=int(os.getenv('WHISPER_ALOUD_BEAM_SIZE', transcription_config.beam_size)),
            vad_filter=os.getenv('WHISPER_ALOUD_VAD_FILTER', str(transcription_config.vad_filter)).lower() == 'true',
            task=os.getenv('WHISPER_ALOUD_TASK', transcription_config.task),
        )

        audio_config = AudioConfig(
            sample_rate=int(os.getenv('WHISPER_ALOUD_SAMPLE_RATE', audio_config.sample_rate)),
            channels=int(os.getenv('WHISPER_ALOUD_CHANNELS', audio_config.channels)),
            device_id=int(os.getenv('WHISPER_ALOUD_DEVICE_ID')) if os.getenv('WHISPER_ALOUD_DEVICE_ID') else audio_config.device_id,
            chunk_duration=float(os.getenv('WHISPER_ALOUD_CHUNK_DURATION', audio_config.chunk_duration)),
            vad_enabled=os.getenv('WHISPER_ALOUD_VAD_ENABLED', str(audio_config.vad_enabled)).lower() == 'true',
            vad_threshold=float(os.getenv('WHISPER_ALOUD_VAD_THRESHOLD', audio_config.vad_threshold)),
            silence_duration=float(os.getenv('WHISPER_ALOUD_SILENCE_DURATION', audio_config.silence_duration)),
            normalize_audio=os.getenv('WHISPER_ALOUD_NORMALIZE_AUDIO', str(audio_config.normalize_audio)).lower() == 'true',
            max_recording_duration=float(os.getenv('WHISPER_ALOUD_MAX_RECORDING_DURATION', audio_config.max_recording_duration)),
        )

        clipboard_config = ClipboardConfig(
            auto_copy=os.getenv('WHISPER_ALOUD_CLIPBOARD_AUTO_COPY', str(clipboard_config.auto_copy)).lower() == 'true',
            auto_paste=os.getenv('WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE', str(clipboard_config.auto_paste)).lower() == 'true',
            paste_delay_ms=int(os.getenv('WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS', clipboard_config.paste_delay_ms)),
            timeout_seconds=float(os.getenv('WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS', clipboard_config.timeout_seconds)),
            fallback_to_file=os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_TO_FILE', str(clipboard_config.fallback_to_file)).lower() == 'true',
            fallback_path=os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_PATH', clipboard_config.fallback_path),
        )

        persistence_config = PersistenceConfig(
            db_path=Path(os.getenv('WHISPER_ALOUD_DB_PATH')) if os.getenv('WHISPER_ALOUD_DB_PATH') else persistence_config.db_path,
            save_audio=os.getenv('WHISPER_ALOUD_SAVE_AUDIO', str(persistence_config.save_audio)).lower() == 'true',
            audio_archive_path=Path(os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE')) if os.getenv('WHISPER_ALOUD_AUDIO_ARCHIVE') else persistence_config.audio_archive_path,
            audio_format=os.getenv('WHISPER_ALOUD_AUDIO_FORMAT', persistence_config.audio_format),
            deduplicate_audio=os.getenv('WHISPER_ALOUD_DEDUPLICATE_AUDIO', str(persistence_config.deduplicate_audio)).lower() == 'true',
            auto_cleanup_enabled=os.getenv('WHISPER_ALOUD_AUTO_CLEANUP', str(persistence_config.auto_cleanup_enabled)).lower() == 'true',
            auto_cleanup_days=int(os.getenv('WHISPER_ALOUD_CLEANUP_DAYS', persistence_config.auto_cleanup_days)),
            max_entries=int(os.getenv('WHISPER_ALOUD_MAX_ENTRIES', persistence_config.max_entries))
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
            # Fallback to default if invalid
            logger.warning(f"Invalid language '{self.transcription.language}' in config, resetting to 'es'")
            self.transcription.language = 'es'

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