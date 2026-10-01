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
import threading
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

# Sherpa-onnx thread count, capped regardless of core count: the WHISP-55 spike saw
# no meaningful RTF win past a handful of threads, and capping bounds worst-case CPU
# contention on constrained laptops (and in CI). No existing config field fits this
# (WHISP-56 audit item 5) -- revisit if one is added. Shared naming/value with
# ParakeetSpeechEngine's _NUM_THREADS.
_NUM_THREADS = min(4, os.cpu_count() or 4)


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
        # Guards all session state below (_stream, _tracker, _last_partial,
        # _cancelling, _recognizer, _session_started) -- WHISP-56 audit item 6,
        # ahead of WHISP-57 wiring this engine to concurrent D-Bus calls. Decode
        # work itself (stream.accept_waveform/decode_stream/get_result) runs
        # outside the lock using a local snapshot, so a slow decode never blocks
        # get_partial()/cancel_session() from another thread.
        self._lock = threading.Lock()
        self._session_started = False
        # WHISP-57 audit (HIGH), defense in depth: incremented by every
        # start_session() call. accept_chunk() already guards its own trailing
        # mutation with a `self._stream is not stream` identity check; finalize()
        # additionally snapshots this generation at entry and, after its
        # unlocked decode section, only applies `_tracker.flush()`'s result to
        # `_last_partial` if the generation is unchanged -- otherwise a slow
        # finalize() racing a fresh start_session() (e.g. a daemon bug that lets
        # Stop->Start overlap, which the daemon's own `_transcribing` guard
        # should prevent in practice) would clobber the NEW session's partial
        # state with the OLD session's stale flush().
        self._generation = 0

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
            num_threads=_NUM_THREADS,
        )
        logger.info("Nemotron streaming engine loaded (chunk=%dms)", self._chunk_ms)

    def unload_model(self) -> None:
        with self._lock:
            self._recognizer = None
            self._stream = None

    def cancel_transcription(self) -> None:
        """Alias for `cancel_session`, satisfying the batch `SpeechEngine` protocol."""
        self.cancel_session()

    # -- streaming surface (StreamingSpeechEngine) --------------------------------

    def start_session(self, language: str) -> None:
        if self._recognizer is None:
            self.load_model()
        stream = self._recognizer.create_stream()
        stream.set_option("language", language)
        with self._lock:
            self._stream = stream
            self._tracker.reset()
            self._cancelling = False
            self._last_partial = PartialResult(committed="", tentative="", seq=0)
            self._session_started = True
            self._generation += 1

    def accept_chunk(self, samples: np.ndarray) -> None:
        with self._lock:
            stream = self._stream
            recognizer = self._recognizer
            cancelling = self._cancelling
            ever_started = self._session_started

        if stream is None:
            if not ever_started:
                raise RuntimeError("accept_chunk() called before start_session()")
            return  # session already ended (e.g. a concurrent finalize/cancel); no-op
        if cancelling:
            return

        stream.accept_waveform(_SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = recognizer.get_result(stream)

        with self._lock:
            if self._stream is not stream:
                return  # session ended/replaced while this chunk was decoding
            self._last_partial = self._tracker.update(text)

    def get_partial(self) -> PartialResult:
        with self._lock:
            return self._last_partial

    def finalize(self) -> TranscriptionResult:
        t0 = time.time()
        language = self.config.transcription.language

        with self._lock:
            stream = self._stream
            recognizer = self._recognizer
            cancelling = self._cancelling
            generation = self._generation
            self._stream = None  # the session ends now regardless of what follows

        if stream is None:
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=0.0, processing_time=round(time.time() - t0, 3),
            )
        if cancelling:
            with self._lock:
                self._cancelling = False
            return TranscriptionResult(
                text="", language=language, segments=[], confidence=0.0,
                duration=0.0, processing_time=round(time.time() - t0, 3),
            )

        tail = np.zeros(int(_TAIL_PADDING_SECONDS * _SAMPLE_RATE), dtype=np.float32)
        stream.accept_waveform(_SAMPLE_RATE, tail)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        final_text = recognizer.get_result(stream).strip()

        with self._lock:
            # The live partial view stays append-only (flush never retracts),
            # but the transcript that reaches history/clipboard is the decoder's
            # final text: merging a diverged commit into it would duplicate
            # words (e.g. "que tal." + "qué tal amigo"). Clients replace the
            # partial view with this text on TranscriptionReady.
            #
            # Only apply flush()'s result if no newer session has started
            # since this finalize() began (WHISP-57 audit, HIGH): the decode
            # work above runs unlocked, so a concurrent start_session() could
            # have already reset _tracker for a new session by now, and
            # applying this stale flush() would corrupt it.
            if self._generation == generation:
                self._last_partial = self._tracker.flush(final_text)

        return TranscriptionResult(
            text=final_text,
            language=language,
            segments=[],
            confidence=0.0,
            duration=0.0,
            processing_time=round(time.time() - t0, 3),
        )

    def cancel_session(self) -> None:
        with self._lock:
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
