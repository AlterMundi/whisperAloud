"""Main transcription engine for WhisperAloud."""

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from .config import WhisperAloudConfig
from .exceptions import AudioFormatError, ModelLoadError, TranscriptionError

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
        logger.info("Transcriber initialized with model: %s, device: %s",
                   config.model.name, config.model.device)

    def load_model(self) -> None:
        """
        Explicitly load the Whisper model.

        Raises:
            ModelLoadError: If model fails to load with details
        """
        if self._model is not None:
            logger.debug("Model already loaded")
            return

        try:
            logger.info("Loading Whisper model: %s on device: %s",
                       self.config.model.name, self.config.model.device)

            # Try to load model
            self._model = WhisperModel(
                self.config.model.name,
                device=self.config.model.device,
                compute_type=self.config.model.compute_type,
                download_root=self.config.model.download_root,
            )

            # Test model with dummy inference to ensure it works
            # This will catch issues like incompatible compute_type
            try:
                # Create a short dummy audio (0.1s silence)
                dummy_audio = np.zeros(1600, dtype=np.float32)
                segments, info = self._model.transcribe(dummy_audio, language="en")
                list(segments)  # Consume generator
                logger.info("Model loaded successfully")
            except Exception as e:
                self._model = None
                raise ModelLoadError(
                    f"Model '{self.config.model.name}' loaded but failed test inference: {e}"
                ) from e

        except Exception as e:
            self._model = None
            device_hint = ""
            if "cuda" in str(e).lower():
                device_hint = " Try device='cpu' to use CPU instead."
            elif "cpu" in str(e).lower():
                device_hint = " Ensure sufficient RAM available."
            raise ModelLoadError(
                f"Failed to load model '{self.config.model.name}' on device '{self.config.model.device}': {e}.{device_hint}"
            ) from e

    def _process_segments(self, segments, duration: float) -> tuple[str, List[dict], float]:
        """
        Process transcription segments into result components.

        Args:
            segments: Iterator of segment objects from faster-whisper
            duration: Audio duration in seconds

        Returns:
            Tuple of (text, segment_list, confidence)
        """
        segment_list = []
        text_parts = []
        total_logprob = 0.0
        segment_count = 0

        for segment in segments:
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

        return text, segment_list, confidence

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
        
        # Validate language code to prevent segmentation faults in underlying library
        if language and (len(language) != 2 or not language.isalpha()):
             logger.warning(f"Invalid language code '{language}', falling back to auto-detection")
             language = None

        transcribe_kwargs = {
            "language": language,
            "beam_size": kwargs.get("beam_size", self.config.transcription.beam_size),
            "vad_filter": kwargs.get("vad_filter", self.config.transcription.vad_filter),
            "task": kwargs.get("task", self.config.transcription.task),
        }

        try:
            logger.info("Starting transcription of file: %s", audio_path)
            start_time = time.time()

            segments, info = self._model.transcribe(audio_path, **transcribe_kwargs)

            processing_time = time.time() - start_time
            text, segment_list, confidence = self._process_segments(segments, info.duration)

            result = TranscriptionResult(
                text=text,
                language=info.language,
                segments=segment_list,
                confidence=confidence,
                duration=info.duration,
                processing_time=processing_time,
            )

            logger.info("Transcription completed in %.2fs, confidence: %.2f%%",
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
        
        # Validate language code to prevent segmentation faults in underlying library
        if language and (len(language) != 2 or not language.isalpha()):
             logger.warning(f"Invalid language code '{language}', falling back to auto-detection")
             language = None

        transcribe_kwargs = {
            "language": language,
            "beam_size": kwargs.get("beam_size", self.config.transcription.beam_size),
            "vad_filter": kwargs.get("vad_filter", self.config.transcription.vad_filter),
            "task": kwargs.get("task", self.config.transcription.task),
        }

        try:
            logger.info("Starting transcription of numpy array (%.2fs)", len(audio) / sample_rate)
            start_time = time.time()

            segments, info = self._model.transcribe(audio, **transcribe_kwargs)

            processing_time = time.time() - start_time
            duration = len(audio) / sample_rate
            text, segment_list, confidence = self._process_segments(segments, duration)

            result = TranscriptionResult(
                text=text,
                language=info.language,
                segments=segment_list,
                confidence=confidence,
                duration=duration,
                processing_time=processing_time,
            )

            logger.info("Transcription completed in %.2fs, confidence: %.2f%%",
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