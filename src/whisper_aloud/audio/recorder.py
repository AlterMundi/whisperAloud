"""Audio recording with state management."""

import logging
import queue
import threading
import time
from enum import Enum
from typing import Callable, List, Optional

import numpy as np
import sounddevice as sd

from ..config import AudioConfig, AudioProcessingConfig
from ..exceptions import AudioDeviceError, AudioRecordingError
from .audio_processor import AudioPipeline, AudioProcessor, StreamingResampler
from .device_manager import AudioDevice, DeviceManager
from .level_meter import AudioLevel, LevelMeter

logger = logging.getLogger(__name__)


class RecordingState(Enum):
    """Recording state machine."""
    IDLE = "idle"
    RECORDING = "recording"
    STOPPING = "stopping"  # transitional: stream closing, no new frames
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
        processing_config: Optional[AudioProcessingConfig] = None,
        auto_stop_callback: Optional[Callable[["np.ndarray"], None]] = None,
        on_processed_chunk: Optional[Callable[[np.ndarray], None]] = None,
    ):
        """
        Initialize recorder.

        Args:
            config: Audio configuration
            level_callback: Optional callback for real-time level updates
            processing_config: Optional audio processing pipeline config
            on_processed_chunk: Optional hook invoked with each ~chunk_duration
                slice of audio as it is captured, resampled to 16kHz (if the
                device isn't already at 16kHz) and run through a per-session
                streaming AudioPipeline (gate -> AGC -> limiter, no denoiser).
                When set, a background worker thread is started in start()
                and stopped in stop()/cancel() — the audio callback itself
                only enqueues raw chunks, so a slow or raising hook can never
                block or crash audio capture. Nothing in the daemon wires
                this up yet (that's WHISP-57); the one-shot stop() path
                (full buffer -> one-shot pipeline -> return) is unaffected
                by this parameter.
        """
        self.config = config
        self.level_callback = level_callback
        self.auto_stop_callback = auto_stop_callback
        self.on_processed_chunk = on_processed_chunk

        # State
        self._state = RecordingState.IDLE
        self._state_lock = threading.Lock()

        # Recording data
        self._frames: List[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None
        self._device: Optional[AudioDevice] = None
        self._start_time: Optional[float] = None

        # Components
        self._level_meter = LevelMeter(attack_ms=10.0, release_ms=300.0, sample_rate=config.sample_rate)
        self._processor = AudioProcessor()  # Keep for format conversion utilities
        self._pipeline = AudioPipeline(processing_config or AudioProcessingConfig())

        # Per-session streaming state (only used when on_processed_chunk is
        # set). Built fresh in start() and torn down in stop()/cancel() so
        # each recording session gets its own pipeline/resampler state.
        self._chunk_queue: Optional["queue.Queue"] = None
        self._chunk_worker: Optional[threading.Thread] = None
        self._streaming_pipeline: Optional[AudioPipeline] = None
        self._streaming_resampler: Optional[StreamingResampler] = None

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

        # Hand the raw chunk to the streaming worker thread (if a hook is
        # registered). Only a cheap, non-blocking enqueue happens here --
        # resampling, pipeline processing, and the hook itself all run off
        # this thread so they can never stall or crash audio capture.
        if self._chunk_queue is not None:
            try:
                self._chunk_queue.put_nowait(audio_chunk)
            except queue.Full:
                logger.warning("Chunk queue full, dropping a processed-chunk hook frame")

        # Calculate and report level
        if self.level_callback:
            try:
                level = self._level_meter.calculate_level(audio_chunk)
                self.level_callback(level)
            except Exception as e:
                logger.error(f"Level callback error: {e}")

        # Check max duration — audio callbacks must not block, so delegate to a thread
        if self.recording_duration >= self.config.max_recording_duration:
            logger.warning(f"Max recording duration reached: {self.config.max_recording_duration}s")
            self._set_state(RecordingState.STOPPING)
            threading.Thread(target=self._auto_stop, daemon=True).start()

    def _auto_stop(self) -> None:
        """Stop recording when max duration is reached (called from a thread, not the callback)."""
        try:
            audio_data = self.stop()
            if self.auto_stop_callback is not None:
                try:
                    self.auto_stop_callback(audio_data)
                except Exception as e:
                    logger.error(f"Auto-stop callback failed: {e}")
        except Exception as e:
            logger.error(f"Auto-stop failed: {e}")
            self._set_state(RecordingState.ERROR)

    def _chunk_worker_loop(self) -> None:
        """Consume raw chunks from the queue off the audio-callback thread.

        Resamples each chunk to 16kHz (if needed, causally, via
        StreamingResampler) then runs it through this session's streaming
        AudioPipeline before calling on_processed_chunk. A None sentinel on
        the queue stops the loop. Any exception from the hook (or from
        resampling/processing) is logged and swallowed so it can never kill
        recording or this worker thread.
        """
        while True:
            chunk = self._chunk_queue.get()
            if chunk is None:
                break
            try:
                processed = chunk
                if self._streaming_resampler is not None:
                    processed = self._streaming_resampler.process(processed)
                if self._streaming_pipeline is not None and processed.size:
                    processed = self._streaming_pipeline.process_chunk(processed, 16000)
                if self.on_processed_chunk is not None and processed.size:
                    self.on_processed_chunk(processed)
            except Exception as e:
                logger.error(f"on_processed_chunk hook failed: {e}", exc_info=True)

    def _start_chunk_worker(self) -> None:
        """Set up a fresh per-session streaming pipeline/resampler/queue/thread."""
        if self.on_processed_chunk is None:
            return
        self._streaming_pipeline = AudioPipeline(self._pipeline.config, streaming=True)
        self._streaming_resampler = (
            StreamingResampler(self.config.sample_rate, 16000)
            if self.config.sample_rate != 16000
            else None
        )
        self._chunk_queue = queue.Queue()
        self._chunk_worker = threading.Thread(
            target=self._chunk_worker_loop, name="AudioRecorderChunkWorker", daemon=True
        )
        self._chunk_worker.start()

    def _stop_chunk_worker(self) -> None:
        """Signal the worker thread to drain and stop, then discard session state."""
        if self._chunk_queue is not None:
            self._chunk_queue.put(None)
        if self._chunk_worker is not None:
            self._chunk_worker.join(timeout=2.0)
        self._chunk_queue = None
        self._chunk_worker = None
        self._streaming_pipeline = None
        self._streaming_resampler = None

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
            device_id = device_id if device_id is not None else self.config.device_id
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
            # AudioRecorder keeps a single one-shot AudioPipeline instance
            # alive across the daemon's whole lifetime (many recordings), so
            # it must be reset per session -- otherwise AGC's rolling RMS
            # history and NoiseGate's envelope/hold state from the previous
            # recording leak into this one's stop() processing.
            self._pipeline.reset()
            self._start_chunk_worker()

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
        if self.state not in (RecordingState.RECORDING, RecordingState.STOPPING, RecordingState.PAUSED):
            raise AudioRecordingError(f"Cannot stop recording in state: {self.state.value}")

        try:
            logger.info("Stopping recording...")
            self._set_state(RecordingState.STOPPING)

            # Close stream FIRST so the callback stops appending frames
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            # No more chunks can arrive now; drain and stop the worker thread
            # (if a hook was registered) before processing the full buffer.
            self._stop_chunk_worker()

            # Now safe to read _frames — callback cannot append after stream is closed
            with self._state_lock:
                frames_snapshot = list(self._frames)

            # Concatenate frames
            if not frames_snapshot:
                logger.warning("No audio frames recorded")
                self._set_state(RecordingState.IDLE)
                return np.array([], dtype=np.float32)

            raw_audio = np.concatenate(frames_snapshot)
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

        self._stop_chunk_worker()
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
