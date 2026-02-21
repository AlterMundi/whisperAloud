"""Main transcription engine for WhisperAloud."""

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from .config import WhisperAloudConfig
from .exceptions import AudioFormatError, ModelLoadError, TranscriptionError
from .utils.validation_helpers import sanitize_language_code

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
except ImportError as e:
    raise ImportError("faster-whisper is required. Install with: pip install faster-whisper") from e


@dataclass
class TranscriptionResult:
    """Result of a transcription operation."""
    text: str
    language: str
    segments: List[dict]
    confidence: float
    duration: float
    processing_time: float


class Transcriber:
    """High-level interface to faster-whisper with error handling."""

    def __init__(self, config: WhisperAloudConfig):
        """Initialize transcriber with configuration (lazy loading)."""
        self.config = config
        self._model: Optional[WhisperModel] = None
        self._cancel_flag = threading.Event()
        logger.info("Transcriber initialized with model: %s, device: %s",
                   config.model.name, config.model.device)

    def cancel_transcription(self) -> None:
        """
        Signal the transcription to stop processing segments.

        This will cause the current transcription to exit early,
        returning a partial result with whatever segments were processed.
        """
        self._cancel_flag.set()
        logger.info("Transcription cancellation requested")

    @property
    def is_cancelling(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancel_flag.is_set()

    def _is_cuda_library_error(self, error: Exception) -> bool:
        """Check if the error is related to missing CUDA libraries."""
        error_str = str(error).lower()
        cuda_indicators = [
            "libcudnn",
            "cudnn",
            "cublas",
            "libcublas",
            "cuda",
            "nvrtc",
            "unable to load",
            "cannot load symbol",
        ]
        return any(indicator in error_str for indicator in cuda_indicators)

    def _try_load_model(self, device: str, compute_type: str) -> bool:
        """
        Attempt to load model with specified device.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Attempting to load model on device: %s", device)
            self._model = WhisperModel(
                self.config.model.name,
                device=device,
                compute_type=compute_type,
                download_root=self.config.model.download_root,
            )

            # Test with dummy inference
            dummy_audio = np.zeros(1600, dtype=np.float32)
            segments, info = self._model.transcribe(dummy_audio, language="en")
            list(segments)  # Consume generator
            return True

        except Exception as e:
            self._model = None
            logger.debug("Failed to load on %s: %s", device, e)
            return False

    def load_model(self) -> None:
        """
        Explicitly load the Whisper model.

        If CUDA is requested but libraries are missing, automatically
        falls back to CPU with a warning.

        Raises:
            ModelLoadError: If model fails to load with details
        """
        if self._model is not None:
            logger.debug("Model already loaded")
            return

        requested_device = self.config.model.device
        compute_type = self.config.model.compute_type

        logger.info("Loading Whisper model: %s on device: %s",
                   self.config.model.name, requested_device)

        # Try loading with requested device
        try:
            self._model = WhisperModel(
                self.config.model.name,
                device=requested_device,
                compute_type=compute_type,
                download_root=self.config.model.download_root,
            )

            # Test model with dummy inference
            dummy_audio = np.zeros(1600, dtype=np.float32)
            segments, info = self._model.transcribe(dummy_audio, language="en")
            list(segments)  # Consume generator
            logger.info("Model loaded successfully on %s", requested_device)
            return

        except Exception as e:
            self._model = None

            # Check if this is a CUDA library error and we can fallback
            if self._is_cuda_library_error(e) and requested_device in ("cuda", "auto"):
                logger.warning(
                    "CUDA libraries not available (%s). "
                    "Install with: sudo apt install libcudnn9-cuda-12 libcublas-12-8",
                    str(e)[:100]
                )
                logger.info("Falling back to CPU...")

                # Try CPU fallback
                # For CPU, prefer int8 compute type for efficiency
                cpu_compute = "int8" if compute_type in ("float16", "int8") else compute_type

                if self._try_load_model("cpu", cpu_compute):
                    logger.warning(
                        "Model loaded on CPU (fallback). GPU acceleration unavailable. "
                        "To enable GPU: run ./scripts/install_cuda.sh"
                    )
                    return
                else:
                    raise ModelLoadError(
                        f"Failed to load model on both CUDA and CPU. "
                        f"Original error: {e}"
                    ) from e

            # Not a CUDA error or fallback failed - raise with hints
            device_hint = ""
            if "cuda" in str(e).lower():
                device_hint = " Install CUDA libs or set device='cpu'."
            elif "cpu" in str(e).lower() or "memory" in str(e).lower():
                device_hint = " Ensure sufficient RAM available."

            raise ModelLoadError(
                f"Failed to load model '{self.config.model.name}' on device '{requested_device}': {e}.{device_hint}"
            ) from e

    def _process_segments(self, segments, duration: float) -> tuple[str, List[dict], float, bool]:
        """
        Process transcription segments into result components.

        Args:
            segments: Iterator of segment objects from faster-whisper
            duration: Audio duration in seconds

        Returns:
            Tuple of (text, segment_list, confidence, was_cancelled)
        """
        segment_list = []
        text_parts = []
        total_logprob = 0.0
        segment_count = 0
        was_cancelled = False

        for segment in segments:
            # Check for cancellation before processing each segment
            if self._cancel_flag.is_set():
                logger.info("Transcription cancelled by user after %d segments", segment_count)
                was_cancelled = True
                break

            segment_list.append({
                "text": segment.text,
                "start": segment.start,
                "end": segment.end,
                "confidence": math.exp(segment.avg_logprob) if segment.avg_logprob else 0.0,
            })
            text_parts.append(segment.text)
            if segment.avg_logprob is not None:
                total_logprob += segment.avg_logprob
                segment_count += 1

        text = "".join(text_parts).strip()
        confidence = math.exp(total_logprob / segment_count) if segment_count > 0 else 0.0

        return text, segment_list, confidence, was_cancelled

    def transcribe_file(self, audio_path: str, **kwargs) -> TranscriptionResult:
        """
        Transcribe audio file.

        Args:
            audio_path: Path to audio file (WAV, MP3, etc.)
            **kwargs: Override transcription config

        Returns:
            TranscriptionResult with text and metadata

        Raises:
            AudioFormatError: If file format invalid/unsupported
            TranscriptionError: If transcription fails
        """
        # Validate file exists and is readable
        path = Path(audio_path)
        if not path.exists():
            raise AudioFormatError(f"Audio file not found: {audio_path}")
        if not path.is_file():
            raise AudioFormatError(f"Path is not a file: {audio_path}")

        # Load model if not already loaded
        self.load_model()

        # Merge config with kwargs
        language = kwargs.get("language", self.config.transcription.language)

        # Sanitize language code to prevent segmentation faults in underlying library
        if language:
            sanitized = sanitize_language_code(language)
            if sanitized is None:
                logger.warning(f"Invalid language code '{language}', falling back to auto-detection")
                language = None
            elif sanitized == "auto":
                language = None
            else:
                language = sanitized

        transcribe_kwargs = {
            "language": language,
            "beam_size": kwargs.get("beam_size", self.config.transcription.beam_size),
            "vad_filter": kwargs.get("vad_filter", self.config.transcription.vad_filter),
            "task": kwargs.get("task", self.config.transcription.task),
        }

        try:
            # Reset cancellation flag before starting
            self._cancel_flag.clear()

            logger.info("Starting transcription of file: %s", audio_path)
            start_time = time.time()

            segments, info = self._model.transcribe(audio_path, **transcribe_kwargs)

            processing_time = time.time() - start_time
            text, segment_list, confidence, was_cancelled = self._process_segments(segments, info.duration)

            if was_cancelled:
                logger.info("Transcription was cancelled, returning partial result")

            result = TranscriptionResult(
                text=text,
                language=info.language,
                segments=segment_list,
                confidence=confidence,
                duration=info.duration,
                processing_time=processing_time,
            )

            logger.info("Transcription %s in %.2fs, confidence: %.2f%%",
                       "cancelled" if was_cancelled else "completed",
                       processing_time, confidence * 100)
            return result

        except Exception as e:
            raise TranscriptionError(f"Transcription failed for file '{audio_path}': {e}") from e

    def transcribe_numpy(self, audio: np.ndarray, sample_rate: int = 16000, **kwargs) -> TranscriptionResult:
        """
        Transcribe numpy audio array.

        Args:
            audio: Float32 array, mono, [-1.0, 1.0] range
            sample_rate: Sample rate in Hz
            **kwargs: Override transcription config

        Returns:
            TranscriptionResult with text and metadata

        Raises:
            AudioFormatError: If array format invalid
            TranscriptionError: If transcription fails
        """
        # Validate audio array
        if not isinstance(audio, np.ndarray):
            raise AudioFormatError("Audio must be a numpy array")
        if audio.dtype != np.float32:
            raise AudioFormatError(f"Audio must be float32, got {audio.dtype}")
        if audio.ndim != 1:
            raise AudioFormatError(f"Audio must be 1D array, got {audio.ndim}D")
        if len(audio) == 0:
            raise AudioFormatError("Audio array is empty")
        if not (-1.0 <= audio.min() <= 1.0 and -1.0 <= audio.max() <= 1.0):
            logger.warning("Audio values outside [-1, 1] range, will be clipped")

        # Load model if not already loaded
        self.load_model()

        # Merge config with kwargs
        language = kwargs.get("language", self.config.transcription.language)

        # Sanitize language code to prevent segmentation faults in underlying library
        if language:
            sanitized = sanitize_language_code(language)
            if sanitized is None:
                logger.warning(f"Invalid language code '{language}', falling back to auto-detection")
                language = None
            elif sanitized == "auto":
                language = None
            else:
                language = sanitized

        transcribe_kwargs = {
            "language": language,
            "beam_size": kwargs.get("beam_size", self.config.transcription.beam_size),
            "vad_filter": kwargs.get("vad_filter", self.config.transcription.vad_filter),
            "task": kwargs.get("task", self.config.transcription.task),
        }

        try:
            # Reset cancellation flag before starting
            self._cancel_flag.clear()

            logger.info("Starting transcription of numpy array (%.2fs)", len(audio) / sample_rate)
            start_time = time.time()

            segments, info = self._model.transcribe(audio, **transcribe_kwargs)

            processing_time = time.time() - start_time
            duration = len(audio) / sample_rate
            text, segment_list, confidence, was_cancelled = self._process_segments(segments, duration)

            if was_cancelled:
                logger.info("Transcription was cancelled, returning partial result")

            result = TranscriptionResult(
                text=text,
                language=info.language,
                segments=segment_list,
                confidence=confidence,
                duration=duration,
                processing_time=processing_time,
            )

            logger.info("Transcription %s in %.2fs, confidence: %.2f%%",
                       "cancelled" if was_cancelled else "completed",
                       processing_time, confidence * 100)
            return result

        except Exception as e:
            raise TranscriptionError(f"Transcription failed for numpy array: {e}") from e

    @property
    def is_loaded(self) -> bool:
        """Check if model is currently loaded."""
        return self._model is not None

    def unload_model(self) -> None:
        """Unload model to free memory."""
        if self._model is not None:
            logger.info("Unloading Whisper model")
            del self._model
            self._model = None
