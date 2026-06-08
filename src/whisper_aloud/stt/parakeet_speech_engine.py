"""Production Parakeet engine (sherpa-onnx) implementing the SpeechEngine protocol (WHISP-36).

NOTE: distinct from the benchmark-side ``ParakeetEngine(STTEngine)`` in ``parakeet_engine.py``
(different surface — that one is load()/capabilities -> Transcript for the bench harness).
This class returns the rich ``TranscriptionResult`` and carries daemon lifecycle state.
``sherpa_onnx`` is imported lazily inside ``load_model`` so importing this module (for the
factory or isinstance checks) does not require sherpa-onnx to be installed.
"""
from __future__ import annotations

import glob
import logging
import os
import time
from typing import TYPE_CHECKING

import numpy as np

from ..transcriber import TranscriptionResult

if TYPE_CHECKING:
    from ..config import WhisperAloudConfig

logger = logging.getLogger(__name__)


class ParakeetSpeechEngine:
    """Parakeet TDT transducer engine satisfying the SpeechEngine protocol."""

    def __init__(self, config: "WhisperAloudConfig") -> None:
        self.config = config
        self._rec = None
        self._cancelling = False  # plain bool: GIL makes the single-writer/single-reader assignment atomic; cancel arrives from the daemon's D-Bus thread
        self._device_in_use: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._rec is not None

    @property
    def is_cancelling(self) -> bool:
        return self._cancelling

    def load_model(self) -> None:
        if self._rec is not None:
            return
        try:
            import sherpa_onnx
        except ImportError as e:
            raise ImportError(
                "The Parakeet engine requires sherpa-onnx. "
                "Install it with: pip install 'whisper-aloud[parakeet]'"
            ) from e

        from .model_download import ensure_model

        model_path = str(ensure_model(self.config.model.name))

        def _one(pattern: str) -> str:
            matches = glob.glob(os.path.join(model_path, pattern))
            if not matches:
                raise FileNotFoundError(f"{pattern} not found in model dir: {model_path}")
            return matches[0]

        encoder = _one("encoder*.onnx")
        decoder = _one("decoder*.onnx")
        joiner = _one("joiner*.onnx")
        tokens = os.path.join(model_path, "tokens.txt")
        requested = "cuda" if self.config.model.device == "cuda" else "cpu"

        def _build(provider: str):
            return sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                tokens=tokens,
                num_threads=os.cpu_count() or 4,
                model_type="nemo_transducer",
                provider=provider,
            )

        try:
            self._rec = _build(requested)
            self._device_in_use = requested
        except Exception as e:  # CUDA build/driver missing -> CPU fallback
            if requested == "cuda":
                logger.warning("Parakeet CUDA init failed (%s); falling back to CPU", e)
                self._rec = _build("cpu")
                self._device_in_use = "cpu"
            else:
                raise
        logger.info("Parakeet loaded on %s", self._device_in_use)

    def unload_model(self) -> None:
        self._rec = None
        self._device_in_use = None

    def cancel_transcription(self) -> None:
        self._cancelling = True

    def transcribe_numpy(
        self, audio: np.ndarray, sample_rate: int = 16000, **kwargs
    ) -> TranscriptionResult:
        t0 = time.time()
        samples = np.asarray(audio, dtype=np.float32)
        if sample_rate != 16000:
            import scipy.signal as ss

            n = int(len(samples) * 16000 / sample_rate)
            samples = ss.resample(samples, n).astype(np.float32)
        language = self.config.transcription.language
        duration = len(samples) / 16000
        # Honor a pending cancel before doing any work (incl. a cold-boot load).
        # Best-effort cancellation: honored only before decode begins (sherpa offline
        # decode is a single blocking call). Clear the flag once handled.
        if self._cancelling:
            self._cancelling = False
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=duration, processing_time=0.0,
            )
        if self._rec is None:
            self.load_model()
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, samples)
        self._rec.decode_stream(stream)
        # segments/confidence are honest placeholders: sherpa-onnx offline transducer
        # does not expose per-segment dicts or a calibrated confidence in our shape.
        return TranscriptionResult(
            text=stream.result.text.strip(),
            language=language,
            segments=[],
            confidence=0.0,
            duration=duration,
            processing_time=round(time.time() - t0, 3),
        )

    def transcribe_file(self, audio_path: str, **kwargs) -> TranscriptionResult:
        import soundfile as sf

        audio, sample_rate = sf.read(audio_path, dtype="float32")
        return self.transcribe_numpy(audio, sample_rate, **kwargs)
