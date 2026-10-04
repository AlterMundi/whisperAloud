"""Configuration management for WhisperAloud."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .exceptions import ConfigurationError
from .sandbox import is_flatpak_sandbox
from .stt import catalog
from .utils.validation_helpers import sanitize_language_code

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration Path Constants
# =============================================================================

# Host-install defaults, computed at import time. Prefer user_config_dir() /
# user_data_dir() below, which also handle the Flatpak sandbox (WHISP-61).
CONFIG_DIR = Path.home() / ".config" / "whisper_aloud"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / "whisper_aloud"


def _xdg_base(env_var: str, home_relative: str) -> Path:
    """Resolve an XDG base dir, honoring the env var only inside Flatpak.

    Inside a Flatpak sandbox only ~/.var/app/<app-id>/ is persistent;
    ~/.config and ~/.local/share are not exposed, so writes there vanish
    when the sandbox exits. Flatpak points XDG_CONFIG_HOME/XDG_DATA_HOME
    at ~/.var/app/<app-id>/{config,data}, so the sandboxed app must use
    them (WHISP-61). Outside the sandbox the historical HOME-relative paths
    are kept unchanged. A relative or empty value is ignored, per the XDG
    Base Directory spec.
    """
    if is_flatpak_sandbox():
        value = os.environ.get(env_var, "")
        if value and os.path.isabs(value):
            return Path(value)
    return Path.home() / home_relative


def user_config_dir() -> Path:
    """Directory holding config.json (computed at call time)."""
    return _xdg_base("XDG_CONFIG_HOME", ".config") / "whisper_aloud"


def user_data_dir() -> Path:
    """Directory holding history.db and the audio archive (call time)."""
    return _xdg_base("XDG_DATA_HOME", ".local/share") / "whisper_aloud"


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
    engine: str = "faster-whisper"
    name: str = "base"
    device: str = "auto"
    compute_type: str = "int8"
    download_root: Optional[str] = None
    # WHISP-94: when False, Transcriber.load_model() never falls back to a
    # network download for a model that isn't in the local cache yet -- it
    # raises ModelLoadError instead. Default True preserves the existing
    # first-use-downloads behavior.
    allow_network_download: bool = True


# Fields that trigger model reload when changed (class-level constant)
MODEL_RELOAD_FIELDS: Set[str] = {"engine", "name", "device", "compute_type"}

# Valid model names per engine (used by validate() and engine/download code).
# Derived from stt.catalog, the single source of truth for model metadata (WHISP-50).
# Kept as a module-level dict (not a function) for backward compat with existing callers.
ENGINE_MODELS: Dict[str, List[str]] = catalog.engine_models()


@dataclass
class TranscriptionConfig:
    """Configuration for transcription settings."""
    language: str = "auto"
    beam_size: int = 5
    vad_filter: bool = True
    task: str = "transcribe"
    # WHISP-77: Silero VAD (faster-whisper's vad_parameters) is more aggressive
    # than its own author's defaults (threshold=0.5, min_speech_duration_ms=250)
    # suggest -- a quiet remote meeting participant captured over speakers can
    # fall entirely below that threshold and get dropped. These defaults are
    # deliberately more permissive. Distinct from audio.vad_threshold, which is
    # an RMS amplitude gate for the live-recording noise gate/trim, not a
    # Silero speech-probability threshold.
    #
    # 0.35 (tried first) was not permissive enough on a real 36-minute meeting
    # recording -- measured Silero retention there was 58%/66%/85%/92% at
    # threshold 0.5/0.35/0.2/0.1 respectively (see tests/test_vad_quiet_speech.py
    # and stt/normalize.py's docstring for the full investigation). 0.2 is the
    # smallest threshold that gets close to full retention on that file without
    # going as low as 0.1, which is more likely to pick up noise as speech.
    vad_threshold: float = 0.2
    vad_min_speech_ms: int = 100
    # Apply a one-shot loudness normalization pass (see stt.normalize) before
    # VAD/transcription in the *file* transcription path. Live dictation
    # already runs through the daemon's AudioPipeline (NoiseGate -> AGC ->
    # Denoiser -> PeakLimiter) before reaching the engine, so this must not be
    # (and is not) applied a second time there -- see Transcriber.transcribe_numpy.
    #
    # Default False: measured on a real 36-minute meeting recording, dynamic
    # normalization (both this project's dynaudnorm-style implementation and
    # literal ffmpeg dynaudnorm) *reduced* Silero VAD retention versus doing
    # nothing at every threshold tested. The function and the CLI's
    # `--normalize` flag are kept for anyone who wants to opt in, but it is
    # not the lever that actually helped on real audio -- see highpass_hz.
    normalize_loudness: bool = False
    # High-pass filter (Hz) applied before VAD/transcription in the file path,
    # removing low-frequency rumble (HVAC, handling noise, room hum) that
    # otherwise raises the noise floor Silero VAD scores against. 0 disables.
    # This -- not loudness normalization -- was the change that actually
    # mattered on a real 36-minute meeting recording: a plain 80Hz high-pass
    # alone took Silero retention from 87%/93% to 96%/99% at threshold
    # 0.2/0.1, while dynaudnorm (with or without the high-pass) made it worse.
    highpass_hz: float = 80.0


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
    paste_shortcut: str = "ctrl+shift+v"   # "ctrl+v" or "ctrl+shift+v"
    timeout_seconds: float = 5.0
    fallback_to_file: bool = True
    fallback_path: str = "/tmp/whisper_aloud_clipboard.txt"
    paste_to_captured_window: bool = True


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
    edit_history_enabled: bool = True

    def __post_init__(self):
        """Set default paths if not provided."""
        if self.db_path is None:
            self.db_path = user_data_dir() / "history.db"
        if self.audio_archive_path is None:
            self.audio_archive_path = user_data_dir() / "audio"


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
    # WHISP-83: causal high-pass (audio.audio_processor.HighPass), run first
    # in AudioPipeline, ahead of NoiseGate, for *live* dictation. 0 disables.
    # Deliberately a separate knob from TranscriptionConfig.highpass_hz (the
    # file-transcription path's equivalent, applied non-causally via
    # stt.normalize.apply_highpass_filter): live and file audio run through
    # entirely different code paths, and two fields, each owned by the path
    # that actually runs it, keeps that boundary explicit.
    #
    # Default is 0 (OFF) -- opt-in, unlike TranscriptionConfig.highpass_hz's
    # 80Hz default. Transcriber.transcribe_numpy (the live-dictation path:
    # service/daemon.py -> Transcriber.transcribe_numpy, distinct from the
    # lower-level STTEngine.transcribe_numpy) *does* run faster-whisper's
    # Silero vad_filter/vad_parameters by default (vad_threshold=0.2, see
    # TranscriptionConfig), so a regression here would reach every live
    # dictation, not just an opt-in file-transcription flag. Measured with
    # scripts/measure_live_highpass_vad_retention.py against the WHISP-77
    # quiet_speech_component.wav fixture (real speech + synthetic 30-60Hz
    # rumble): unlike the file path's zero-phase (sosfiltfilt) filter, this
    # *causal* (sosfilt) filter's own phase shift measured as a small net
    # NEGATIVE for Silero VAD retention specifically around the project's
    # vad_threshold=0.2 default (checked against both zero and
    # steady-state-scaled initial filter state -- not a cold-start
    # artifact). NoiseGate itself (the stage this runs ahead of) barely
    # reacted to the rumble either way, being an RMS/energy gate rather than
    # a spectral one. With no demonstrated gain and a plausible live-path
    # regression, the stage stays available (same implementation, same
    # tests) but is off until explicitly enabled.
    # Env override: WHISPER_ALOUD_AUDIO_HIGHPASS_HZ.
    highpass_hz: float = 0.0


@dataclass
class RecordingFlowConfig:
    """Configuration for smart recording flow (MPRIS + hardware gain)."""
    pause_media: bool = True            # Pause active MPRIS players on record start
    raise_mic_gain: bool = True         # Raise mic gain to target before recording
    target_gain_linear: float = 0.85   # Target mic gain (0.0–1.5, applied only if above current)
    pre_pause_delay_ms: int = 0        # Optional delay after pausing media before recording
    post_resume_delay_ms: int = 0      # Optional delay after recording before resuming media
    gain_restore_on_crash: bool = True  # Persist gain snapshot for restore after crash


@dataclass
class InputGainConfig:
    """Input normalization, the first stage ahead of the processing chain.

    See docs/design/input-gain-loop.md. A digital, speech-level-driven gain
    (WebRTC AGC2-style) that only amplifies, never clips, and never lifts
    the noise floor above ``max_output_noise_dbfs``. It never touches the
    device/source volume: ``hardware_control`` is reserved for an opt-in
    backend that is not implemented yet (setting it only logs a warning).
    """
    enabled: bool = True
    target_dbfs: float = -20.0            # voiced-speech RMS level to aim for
    max_gain_db: float = 30.0             # bounded boost (AGC2 default)
    max_output_noise_dbfs: float = -40.0  # cap on the amplified noise floor
    peak_ceiling_dbfs: float = -3.0       # no normalized sample above this
    max_gain_change_db_per_s: float = 10.0  # upward slew, streaming path only
    hardware_control: bool = False        # reserved (deferred backend)


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
    recording_flow: RecordingFlowConfig = field(default_factory=RecordingFlowConfig)
    input_gain: InputGainConfig = field(default_factory=InputGainConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for serialization."""
        return {
            "model": {
                "engine": self.model.engine,
                "name": self.model.name,
                "device": self.model.device,
                "compute_type": self.model.compute_type,
                "download_root": self.model.download_root,
                "allow_network_download": self.model.allow_network_download,
            },
            "transcription": {
                "language": self.transcription.language,
                "beam_size": self.transcription.beam_size,
                "vad_filter": self.transcription.vad_filter,
                "task": self.transcription.task,
                "vad_threshold": self.transcription.vad_threshold,
                "vad_min_speech_ms": self.transcription.vad_min_speech_ms,
                "normalize_loudness": self.transcription.normalize_loudness,
                "highpass_hz": self.transcription.highpass_hz,
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
                "paste_shortcut": self.clipboard.paste_shortcut,
                "timeout_seconds": self.clipboard.timeout_seconds,
                "fallback_to_file": self.clipboard.fallback_to_file,
                "fallback_path": self.clipboard.fallback_path,
                "paste_to_captured_window": self.clipboard.paste_to_captured_window,
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
                "edit_history_enabled": self.persistence.edit_history_enabled,
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
                "highpass_hz": self.audio_processing.highpass_hz,
            },
            "hotkey": {
                "toggle_recording": self.hotkey.toggle_recording,
                "cancel_recording": self.hotkey.cancel_recording,
            },
            "recording_flow": {
                "pause_media": self.recording_flow.pause_media,
                "raise_mic_gain": self.recording_flow.raise_mic_gain,
                "target_gain_linear": self.recording_flow.target_gain_linear,
                "pre_pause_delay_ms": self.recording_flow.pre_pause_delay_ms,
                "post_resume_delay_ms": self.recording_flow.post_resume_delay_ms,
                "gain_restore_on_crash": self.recording_flow.gain_restore_on_crash,
            },
            "input_gain": {
                "enabled": self.input_gain.enabled,
                "target_dbfs": self.input_gain.target_dbfs,
                "max_gain_db": self.input_gain.max_gain_db,
                "max_output_noise_dbfs": self.input_gain.max_output_noise_dbfs,
                "peak_ceiling_dbfs": self.input_gain.peak_ceiling_dbfs,
                "max_gain_change_db_per_s": self.input_gain.max_gain_change_db_per_s,
                "hardware_control": self.input_gain.hardware_control,
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

        if "recording_flow" in data:
            for key, value in data["recording_flow"].items():
                if hasattr(config.recording_flow, key):
                    setattr(config.recording_flow, key, value)

        if "input_gain" in data:
            for key, value in data["input_gain"].items():
                if hasattr(config.input_gain, key):
                    setattr(config.input_gain, key, value)

        return config

    def copy(self) -> 'WhisperAloudConfig':
        """Create a deep copy of the configuration."""
        return WhisperAloudConfig.from_dict(self.to_dict())

    @classmethod
    def load(cls, strict: bool = False) -> 'WhisperAloudConfig':
        """
        Load configuration with priority: defaults < file < env vars.

        Args:
            strict: If True, a config file that exists but can't be read or
                parsed raises ConfigurationError instead of silently falling
                back to defaults. Used by ReloadConfig() (WHISP-71): an
                explicit reload of a malformed file must fail loudly --
                docs/dbus-api.md documents ReloadConfig() returning False
                and emitting Error("reload_failed", ...) in that case, not
                quietly replacing the in-memory config (and, via SetConfig's
                caller-triggered save(), the file on disk) with defaults.
                Startup (WhisperAloudService.__init__ -> load() with no
                arguments) intentionally keeps the lenient default: log the
                error, keep defaults in memory, and never touch the file on
                disk (nothing here calls save()) -- see docs/dbus-api.md's
                ReloadConfig section for the full rationale.

        Returns:
            WhisperAloudConfig instance

        Raises:
            ConfigurationError: if strict=True and the config file exists
                but cannot be read or parsed.
        """
        # Compute path dynamically to support HOME changes in tests
        config_file = user_config_dir() / "config.json"

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
                if strict:
                    raise ConfigurationError(f"Config file corrupted: {e}") from e
                logger.error(f"Config file corrupted: {e}. Using defaults.")
            except Exception as e:
                if strict:
                    raise ConfigurationError(f"Failed to load config from file: {e}") from e
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
        self.model.engine = os.getenv('WHISPER_ALOUD_MODEL_ENGINE', self.model.engine)
        self.model.name = os.getenv('WHISPER_ALOUD_MODEL_NAME', self.model.name)
        self.model.device = os.getenv('WHISPER_ALOUD_MODEL_DEVICE', self.model.device)
        self.model.compute_type = os.getenv('WHISPER_ALOUD_MODEL_COMPUTE_TYPE', self.model.compute_type)
        self.model.download_root = os.getenv('WHISPER_ALOUD_MODEL_DOWNLOAD_ROOT', self.model.download_root)
        self.model.allow_network_download = parse_bool_env(
            'WHISPER_ALOUD_MODEL_ALLOW_NETWORK_DOWNLOAD', self.model.allow_network_download
        )

        # Transcription
        self.transcription.language = os.getenv('WHISPER_ALOUD_LANGUAGE', self.transcription.language)
        self.transcription.beam_size = parse_int_env('WHISPER_ALOUD_BEAM_SIZE', self.transcription.beam_size)
        self.transcription.vad_filter = parse_bool_env('WHISPER_ALOUD_VAD_FILTER', self.transcription.vad_filter)
        self.transcription.task = os.getenv('WHISPER_ALOUD_TASK', self.transcription.task)
        # Distinct env var names from audio.vad_threshold/WHISPER_ALOUD_VAD_THRESHOLD
        # (RMS gate) -- this is Silero's speech-probability threshold.
        self.transcription.vad_threshold = parse_float_env(
            'WHISPER_ALOUD_VAD_SPEECH_THRESHOLD', self.transcription.vad_threshold
        )
        self.transcription.vad_min_speech_ms = parse_int_env(
            'WHISPER_ALOUD_VAD_MIN_SPEECH_MS', self.transcription.vad_min_speech_ms
        )
        self.transcription.normalize_loudness = parse_bool_env(
            'WHISPER_ALOUD_TRANSCRIPTION_NORMALIZE', self.transcription.normalize_loudness
        )
        self.transcription.highpass_hz = parse_float_env(
            'WHISPER_ALOUD_HIGHPASS_HZ', self.transcription.highpass_hz
        )

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
        self.clipboard.paste_shortcut = os.getenv(
            'WHISPER_ALOUD_CLIPBOARD_PASTE_SHORTCUT',
            self.clipboard.paste_shortcut
        )
        self.clipboard.timeout_seconds = parse_float_env('WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS', self.clipboard.timeout_seconds)
        self.clipboard.fallback_to_file = parse_bool_env('WHISPER_ALOUD_CLIPBOARD_FALLBACK_TO_FILE', self.clipboard.fallback_to_file)
        self.clipboard.fallback_path = os.getenv('WHISPER_ALOUD_CLIPBOARD_FALLBACK_PATH', self.clipboard.fallback_path)
        self.clipboard.paste_to_captured_window = parse_bool_env(
            'WHISPER_ALOUD_CLIPBOARD_PASTE_TO_CAPTURED_WINDOW',
            self.clipboard.paste_to_captured_window
        )

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
        self.persistence.edit_history_enabled = parse_bool_env(
            'WHISPER_ALOUD_EDIT_HISTORY', self.persistence.edit_history_enabled
        )

        # Recording flow
        self.recording_flow.pause_media = parse_bool_env(
            'WHISPER_ALOUD_PAUSE_MEDIA', self.recording_flow.pause_media
        )
        self.recording_flow.raise_mic_gain = parse_bool_env(
            'WHISPER_ALOUD_RAISE_MIC_GAIN', self.recording_flow.raise_mic_gain
        )
        self.recording_flow.target_gain_linear = parse_float_env(
            'WHISPER_ALOUD_TARGET_GAIN_LINEAR', self.recording_flow.target_gain_linear
        )
        self.recording_flow.pre_pause_delay_ms = parse_int_env(
            'WHISPER_ALOUD_PRE_PAUSE_DELAY_MS', self.recording_flow.pre_pause_delay_ms
        )
        self.recording_flow.post_resume_delay_ms = parse_int_env(
            'WHISPER_ALOUD_POST_RESUME_DELAY_MS', self.recording_flow.post_resume_delay_ms
        )
        self.recording_flow.gain_restore_on_crash = parse_bool_env(
            'WHISPER_ALOUD_GAIN_RESTORE_ON_CRASH', self.recording_flow.gain_restore_on_crash
        )

        # Audio processing pipeline (WHISP-83)
        self.audio_processing.highpass_hz = parse_float_env(
            'WHISPER_ALOUD_AUDIO_HIGHPASS_HZ', self.audio_processing.highpass_hz
        )

        # Input normalization (docs/design/input-gain-loop.md)
        ig = self.input_gain
        ig.enabled = parse_bool_env('WHISPER_ALOUD_INPUT_GAIN_ENABLED', ig.enabled)
        ig.target_dbfs = parse_float_env('WHISPER_ALOUD_INPUT_GAIN_TARGET_DBFS', ig.target_dbfs)
        ig.max_gain_db = parse_float_env('WHISPER_ALOUD_INPUT_GAIN_MAX_GAIN_DB', ig.max_gain_db)
        ig.max_output_noise_dbfs = parse_float_env(
            'WHISPER_ALOUD_INPUT_GAIN_MAX_OUTPUT_NOISE_DBFS', ig.max_output_noise_dbfs
        )
        ig.hardware_control = parse_bool_env(
            'WHISPER_ALOUD_INPUT_GAIN_HARDWARE_CONTROL', ig.hardware_control
        )

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
        # Validate engine
        valid_engines = list(ENGINE_MODELS.keys())
        if self.model.engine not in valid_engines:
            raise ConfigurationError(
                f"Invalid engine '{self.model.engine}'. Valid: {', '.join(valid_engines)}"
            )

        # Validate model name for the selected engine
        valid_models = ENGINE_MODELS[self.model.engine]
        if self.model.name not in valid_models:
            raise ConfigurationError(
                f"Invalid model name '{self.model.name}' for engine '{self.model.engine}'. "
                f"Valid: {', '.join(valid_models)}"
            )

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

        # Validate Silero VAD settings (WHISP-77)
        if not (0.0 < self.transcription.vad_threshold < 1.0):
            raise ConfigurationError(
                f"Invalid transcription vad_threshold {self.transcription.vad_threshold}. Must be 0.0-1.0"
            )
        if not (0 <= self.transcription.vad_min_speech_ms <= 5000):
            raise ConfigurationError(
                f"Invalid vad_min_speech_ms {self.transcription.vad_min_speech_ms}. Must be 0-5000"
            )
        # 0 disables the high-pass; otherwise must be a valid cutoff strictly
        # below Nyquist for 16kHz audio (8000 Hz) with headroom for the filter.
        if self.transcription.highpass_hz != 0 and not (0 < self.transcription.highpass_hz < 4000):
            raise ConfigurationError(
                f"Invalid highpass_hz {self.transcription.highpass_hz}. Must be 0 (disabled) or 0-4000"
            )

        # Validate audio configuration
        if not (8000 <= self.audio.sample_rate <= 48000):
            raise ConfigurationError(f"Invalid sample rate {self.audio.sample_rate}. Must be 8000-48000 Hz")

        if self.audio.channels not in (1, 2):
            raise ConfigurationError(f"Invalid channels {self.audio.channels}. Must be 1 or 2")

        if not (0.0 < self.audio.vad_threshold < 1.0):
            raise ConfigurationError(f"Invalid VAD threshold {self.audio.vad_threshold}. Must be 0.0-1.0")

        if not (0.01 <= self.audio.chunk_duration <= 1.0):
            raise ConfigurationError(f"Invalid chunk duration {self.audio.chunk_duration}. Must be 0.01-1.0s")

        # Validate audio processing pipeline configuration (WHISP-83).
        # Same bound as transcription.highpass_hz above (0 disables, or a
        # cutoff comfortably below Nyquist for 16kHz audio).
        hp = self.audio_processing.highpass_hz
        if hp != 0 and not (0 < hp < 4000):
            raise ConfigurationError(
                f"Invalid audio_processing highpass_hz {self.audio_processing.highpass_hz}. "
                "Must be 0 (disabled) or 0-4000"
            )

        self._validate_input_gain()

        # Validate clipboard configuration
        if self.clipboard.timeout_seconds <= 0:
            raise ConfigurationError(f"Invalid clipboard timeout {self.clipboard.timeout_seconds}. Must be > 0")

        if self.clipboard.paste_delay_ms < 0:
            raise ConfigurationError(f"Invalid paste delay {self.clipboard.paste_delay_ms}. Must be >= 0")

    def _validate_input_gain(self) -> None:
        """Bounds for the input normalization stage (input_gain section)."""
        ig = self.input_gain
        checks = (
            (-50.0 <= ig.target_dbfs <= -6.0, "target_dbfs", "-50..-6 dBFS"),
            (0.0 <= ig.max_gain_db <= 60.0, "max_gain_db", "0..60 dB"),
            (-90.0 <= ig.max_output_noise_dbfs <= -20.0, "max_output_noise_dbfs", "-90..-20 dBFS"),
            (-20.0 <= ig.peak_ceiling_dbfs <= 0.0, "peak_ceiling_dbfs", "-20..0 dBFS"),
            (0.0 < ig.max_gain_change_db_per_s <= 1000.0, "max_gain_change_db_per_s", ">0..1000"),
        )
        for ok, name, valid in checks:
            if not ok:
                raise ConfigurationError(
                    f"Invalid input_gain.{name} {getattr(ig, name)}. Must be {valid}"
                )
        if ig.target_dbfs >= ig.peak_ceiling_dbfs:
            raise ConfigurationError(
                f"Invalid input_gain.target_dbfs {ig.target_dbfs}: must be below "
                f"input_gain.peak_ceiling_dbfs {ig.peak_ceiling_dbfs}"
            )

    def save(self) -> Path:
        """Save configuration to file."""
        # Compute path dynamically to support HOME changes in tests
        config_dir = user_config_dir()
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
    if old.model.engine != new.model.engine:
        model_changes.add("engine")
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
    if old.transcription.vad_threshold != new.transcription.vad_threshold:
        trans_changes.add("vad_threshold")
    if old.transcription.vad_min_speech_ms != new.transcription.vad_min_speech_ms:
        trans_changes.add("vad_min_speech_ms")
    if old.transcription.normalize_loudness != new.transcription.normalize_loudness:
        trans_changes.add("normalize_loudness")
    if old.transcription.highpass_hz != new.transcription.highpass_hz:
        trans_changes.add("highpass_hz")

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

    # Input normalization: every field is a plain value, compare them all.
    input_gain_changes = {
        name
        for name in old.input_gain.__dataclass_fields__
        if getattr(old.input_gain, name) != getattr(new.input_gain, name)
    }
    if input_gain_changes:
        changes.changed_sections.add("input_gain")
        changes.changed_fields["input_gain"] = input_gain_changes

    return changes
