"""Audio recording with state management."""

import logging
import threading
import time
from enum import Enum
from typing import Callable, List, Optional

import numpy as np
import sounddevice as sd

from ..config import AudioConfig, AudioProcessingConfig
from ..exceptions import AudioDeviceError, AudioRecordingError
from .audio_processor import AudioPipeline, AudioProcessor
from .device_manager import AudioDevice, DeviceManager
from .level_meter import AudioLevel, LevelMeter

logger = logging.getLogger(__name__)


class RecordingState(Enum):
    """Recording state machine."""
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class AudioRecorder:
    """
    Professional audio recorder with state management.

    Features:
    - Non-blocking background recording
    - Real-time level monitoring
    - Voice activity detection
    - Automatic format conversion
    - Thread-safe state management
    """

    def __init__(
        self,
        config: AudioConfig,
        level_callback: Optional[Callable[[AudioLevel], None]] = None,
        processing_config: Optional[AudioProcessingConfig] = None
    ):
        """
        Initialize recorder.

        Args:
            config: Audio configuration
            level_callback: Optional callback for real-time level updates
            processing_config: Optional audio processing pipeline config
        """
        self.config = config
        self.level_callback = level_callback

        # State
        self._state = RecordingState.IDLE
        self._state_lock = threading.Lock()

        # Recording data
        self._frames: List[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._device: Optional[AudioDevice] = None
        self._start_time: Optional[float] = None

        # Components
        self._level_meter = LevelMeter(smoothing=0.3)
        self._processor = AudioProcessor()  # Keep for format conversion utilities
        self._pipeline = AudioPipeline(processing_config or AudioProcessingConfig())

        logger.info("AudioRecorder initialized")

    @property
    def state(self) -> RecordingState:
        """Get current recording state."""
        with self._state_lock:
            return self._state

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self.state == RecordingState.RECORDING

    @property
    def recording_duration(self) -> float:
        """Get current recording duration in seconds."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def _set_state(self, new_state: RecordingState) -> None:
        """Set recording state (thread-safe)."""
        with self._state_lock:
            logger.debug(f"State: {self._state.value} -> {new_state.value}")
            self._state = new_state

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """
        Callback for audio stream (runs in audio thread).

        Args:
            indata: Input audio data
            frames: Number of frames
            time_info: Timing info
            status: PortAudio status
        """
        if status:
            logger.warning(f"Audio callback status: {status}")

        if self.state != RecordingState.RECORDING:
            return

        # Copy audio data (indata is read-only)
        audio_chunk = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()

        # Store frame
        self._frames.append(audio_chunk)

        # Calculate and report level
        if self.level_callback:
            try:
                level = self._level_meter.calculate_level(audio_chunk)
                self.level_callback(level)
            except Exception as e:
                logger.error(f"Level callback error: {e}")

        # Check max duration
        if self.recording_duration >= self.config.max_recording_duration:
            logger.warning(f"Max recording duration reached: {self.config.max_recording_duration}s")
            self._set_state(RecordingState.STOPPED)

    def start(self, device_id: Optional[int] = None) -> None:
        """
        Start recording.

        Args:
            device_id: Optional device ID (uses config default if None)

        Raises:
            AudioRecordingError: If recording fails to start
            AudioDeviceError: If device validation fails
        """
        if self.state == RecordingState.RECORDING:
            logger.warning("Already recording, ignoring start request")
            return

        try:
            # Validate device
            device_id = device_id or self.config.device_id
            self._device = DeviceManager.validate_device(
                device_id,
                self.config.sample_rate,
                self.config.channels
            )

            logger.info(f"Starting recording on device: {self._device.name}")

            # Reset state
            self._frames = []
            self._level_meter.reset()
            self._start_time = time.time()

            # Open stream
            self._stream = sd.InputStream(
                device=self._device.id,
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype='float32',
                callback=self._audio_callback,
                blocksize=int(self.config.chunk_duration * self.config.sample_rate),
            )

            self._stream.start()
            self._set_state(RecordingState.RECORDING)
            logger.info("Recording started")

        except AudioDeviceError:
            self._set_state(RecordingState.ERROR)
            raise
        except Exception as e:
            self._set_state(RecordingState.ERROR)
            raise AudioRecordingError(f"Failed to start recording: {e}") from e

    def stop(self) -> np.ndarray:
        """
        Stop recording and return audio data.

        Returns:
            Recorded audio as float32 numpy array (mono, normalized)

        Raises:
            AudioRecordingError: If stop fails or no data recorded
        """
        if self.state not in (RecordingState.RECORDING, RecordingState.PAUSED):
            raise AudioRecordingError(f"Cannot stop recording in state: {self.state.value}")

        try:
            logger.info("Stopping recording...")
            self._set_state(RecordingState.STOPPED)

            # Stop and close stream
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            # Concatenate frames
            if not self._frames:
                logger.warning("No audio frames recorded")
                return np.array([], dtype=np.float32)

            raw_audio = np.concatenate(self._frames)
            logger.info(f"Recorded {len(raw_audio) / self.config.sample_rate:.2f}s of audio")

            # Format conversion (mono, resample)
            audio = raw_audio
            if audio.ndim > 1:
                audio = AudioProcessor.stereo_to_mono(audio)
            if self.config.sample_rate != 16000:
                audio = AudioProcessor.resample(audio, self.config.sample_rate, 16000)

            # Trim silence if VAD enabled
            if self.config.vad_enabled:
                audio, _, _ = AudioProcessor.trim_silence(audio, 16000, self.config.vad_threshold)

            # Apply processing pipeline (gate, AGC, denoise, limit)
            processed_audio = self._pipeline.process(audio, sample_rate=16000)

            # Reset state
            self._set_state(RecordingState.IDLE)
            logger.info(f"Recording complete: {len(processed_audio) / 16000:.2f}s (processed)")

            return processed_audio

        except Exception as e:
            self._set_state(RecordingState.ERROR)
            raise AudioRecordingError(f"Failed to stop recording: {e}") from e

    def cancel(self) -> None:
        """Cancel recording and discard audio data."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._frames = []
        self._set_state(RecordingState.IDLE)
        logger.info("Recording cancelled")

    def pause(self) -> None:
        """Pause recording (keeps stream open)."""
        if self.state != RecordingState.RECORDING:
            return

        self._set_state(RecordingState.PAUSED)
        logger.info("Recording paused")

    def resume(self) -> None:
        """Resume paused recording."""
        if self.state != RecordingState.PAUSED:
            return

        self._set_state(RecordingState.RECORDING)
        logger.info("Recording resumed")

    def __del__(self):
        """Cleanup on deletion."""
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
