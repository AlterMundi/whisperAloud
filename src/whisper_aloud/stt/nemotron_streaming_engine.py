"""Production streaming engine: sherpa-onnx OnlineRecognizer for NVIDIA Nemotron 3.5
ASR Streaming (WHISP-56).

Findings/API facts this adapter is built from:
docs/superpowers/specs/2026-10-01-whisp55-nemotron-sherpa-findings.md and the working
spike at scripts/spikes/nemotron_stream_demo.py.

``sherpa_onnx`` is imported lazily inside ``load_model`` (mirrors
``parakeet_speech_engine.py``'s import-guard pattern) so importing this module for the
factory or isinstance checks never requires sherpa-onnx to be installed.

This one engine satisfies both the daemon's streaming surface
(``StreamingSpeechEngine``: start_session/accept_chunk/get_partial/finalize/
cancel_session) and the existing batch surface (``SpeechEngine.transcribe_numpy``), by
having the batch path feed the whole buffer through the streaming path in fixed-size
chunks and then finalizing (WHISP-56 design choice: one engine serves both call
styles instead of splitting them).
"""
from __future__ import annotations

import glob
import logging
import os
import time
from typing import TYPE_CHECKING

import numpy as np

from ..transcriber import TranscriptionResult
from .streaming import CommitTracker, PartialResult

if TYPE_CHECKING:
    from ..config import WhisperAloudConfig

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_FEATURE_DIM = 128

# Tail padding appended before finalize so the decoder flushes its last pending words.
# Per the WHISP-55 spike/sherpa-onnx streaming examples: ~0.66s of silence.
_TAIL_PADDING_SECONDS = 0.66

# Chunk size (ms) per catalog model name -- the one published chunk variant wired up
# so far (WHISP-56 default). Keyed by name so additional chunk-size catalog entries
# (80/160/320/1120ms) can be added later without touching this class's logic.
_CHUNK_MS_BY_MODEL_NAME: dict[str, int] = {
    "nemotron-3.5-streaming-560ms": 560,
}
_DEFAULT_CHUNK_MS = 560


class NemotronStreamingEngine:
    """Nemotron 3.5 ASR Streaming via ``sherpa_onnx.OnlineRecognizer`` (model_type="nemotron").

    Only ``decoding_method="greedy_search"`` is supported for this model type (no
    hotwords/beam search yet, per the WHISP-55 findings doc).
    """

    is_streaming = True

    def __init__(self, config: "WhisperAloudConfig") -> None:
        self.config = config
        self._recognizer = None
        self._stream = None
        self._cancelling = False
        self._tracker = CommitTracker(stable_count=2)
        self._last_partial = PartialResult(committed="", tentative="", seq=0)
        self._chunk_ms = _CHUNK_MS_BY_MODEL_NAME.get(config.model.name, _DEFAULT_CHUNK_MS)

    @property
    def is_loaded(self) -> bool:
        return self._recognizer is not None

    @property
    def is_cancelling(self) -> bool:
        return self._cancelling

    def load_model(self) -> None:
        if self._recognizer is not None:
            return
        try:
            import sherpa_onnx
        except ImportError as e:
            raise ImportError(
                "The Nemotron streaming engine requires sherpa-onnx. "
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

        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=tokens,
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            sample_rate=_SAMPLE_RATE,
            feature_dim=_FEATURE_DIM,
            model_type="nemotron",
            decoding_method="greedy_search",
            num_threads=os.cpu_count() or 4,
        )
        logger.info("Nemotron streaming engine loaded (chunk=%dms)", self._chunk_ms)

    def unload_model(self) -> None:
        self._recognizer = None
        self._stream = None

    def cancel_transcription(self) -> None:
        """Alias for `cancel_session`, satisfying the batch `SpeechEngine` protocol."""
        self.cancel_session()

    # -- streaming surface (StreamingSpeechEngine) --------------------------------

    def start_session(self, language: str) -> None:
        if self._recognizer is None:
            self.load_model()
        self._stream = self._recognizer.create_stream()
        self._stream.set_option("language", language)
        self._tracker.reset()
        self._cancelling = False
        self._last_partial = PartialResult(committed="", tentative="", seq=0)

    def accept_chunk(self, samples: np.ndarray) -> None:
        if self._stream is None:
            raise RuntimeError("accept_chunk() called before start_session()")
        if self._cancelling:
            return
        self._stream.accept_waveform(_SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = self._recognizer.get_result(self._stream)
        self._last_partial = self._tracker.update(text)

    def get_partial(self) -> PartialResult:
        return self._last_partial

    def finalize(self) -> TranscriptionResult:
        t0 = time.time()
        language = self.config.transcription.language
        if self._stream is None:
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=0.0, processing_time=round(time.time() - t0, 3),
            )
        if self._cancelling:
            self._cancelling = False
            self._stream = None
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=0.0, processing_time=round(time.time() - t0, 3),
            )

        tail = np.zeros(int(_TAIL_PADDING_SECONDS * _SAMPLE_RATE), dtype=np.float32)
        self._stream.accept_waveform(_SAMPLE_RATE, tail)
        self._stream.input_finished()
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        final_text = self._recognizer.get_result(self._stream).strip()
        self._stream = None
        return TranscriptionResult(
            text=final_text,
            language=language,
            segments=[],
            confidence=0.0,
            duration=0.0,
            processing_time=round(time.time() - t0, 3),
        )

    def cancel_session(self) -> None:
        self._cancelling = True

    # -- batch surface (SpeechEngine), feeding the streaming path chunk by chunk ---

    def transcribe_numpy(
        self, audio: np.ndarray, sample_rate: int = 16000, **kwargs
    ) -> TranscriptionResult:
        t0 = time.time()
        samples = np.asarray(audio, dtype=np.float32)
        if sample_rate != _SAMPLE_RATE:
            import scipy.signal as ss

            n = int(len(samples) * _SAMPLE_RATE / sample_rate)
            samples = ss.resample(samples, n).astype(np.float32)
        duration = len(samples) / _SAMPLE_RATE
        language = self.config.transcription.language

        # Best-effort cancellation: honored only before decode begins, same contract
        # as ParakeetSpeechEngine.transcribe_numpy.
        if self._cancelling:
            self._cancelling = False
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=duration, processing_time=0.0,
            )

        self.start_session(language)
        chunk_size = max(1, int(_SAMPLE_RATE * self._chunk_ms / 1000))
        for i in range(0, len(samples), chunk_size):
            self.accept_chunk(samples[i : i + chunk_size])
        result = self.finalize()
        return TranscriptionResult(
            text=result.text,
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
