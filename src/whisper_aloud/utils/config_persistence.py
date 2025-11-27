"""Configuration persistence utilities."""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import WhisperAloudConfig

logger = logging.getLogger(__name__)


def save_config_to_file(config: "WhisperAloudConfig") -> Path:
    """Save configuration to JSON file."""
    config_dir = Path.home() / ".config" / "whisper_aloud"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    config_dict = {
        "model": {
            "name": config.model.name,
            "device": config.model.device,
            "compute_type": config.model.compute_type,
            "download_root": config.model.download_root
        },
        "transcription": {
            "language": config.transcription.language,
            "beam_size": config.transcription.beam_size,
            "vad_filter": config.transcription.vad_filter,
            "task": config.transcription.task
        },
        "audio": {
            "sample_rate": config.audio.sample_rate,
            "channels": config.audio.channels,
            "device_id": config.audio.device_id,
            "chunk_duration": config.audio.chunk_duration,
            "vad_enabled": config.audio.vad_enabled,
            "vad_threshold": config.audio.vad_threshold,
            "silence_duration": config.audio.silence_duration,
            "normalize_audio": config.audio.normalize_audio,
            "max_recording_duration": config.audio.max_recording_duration
        },
        "clipboard": {
            "auto_copy": config.clipboard.auto_copy,
            "auto_paste": config.clipboard.auto_paste,
            "paste_delay_ms": config.clipboard.paste_delay_ms,
            "timeout_seconds": config.clipboard.timeout_seconds,
            "fallback_to_file": config.clipboard.fallback_to_file,
            "fallback_path": config.clipboard.fallback_path
        },
        "persistence": {
            "save_audio": config.persistence.save_audio,
            "audio_archive_path": str(config.persistence.audio_archive_path) if config.persistence.audio_archive_path else None,
            "audio_format": config.persistence.audio_format,
            "deduplicate_audio": config.persistence.deduplicate_audio,
            "auto_cleanup_enabled": config.persistence.auto_cleanup_enabled,
            "auto_cleanup_days": config.persistence.auto_cleanup_days,
            "max_entries": config.persistence.max_entries,
            "db_path": str(config.persistence.db_path) if config.persistence.db_path else None
        } if config.persistence else None
    }

    with open(config_path, "w") as f:
        json.dump(config_dict, f, indent=2)

    logger.info(f"Configuration saved to {config_path}")
    return config_path