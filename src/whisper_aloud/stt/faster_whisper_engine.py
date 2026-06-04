"""faster-whisper (CTranslate2) adapter."""
from __future__ import annotations

import numpy as np

from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript


class FasterWhisperEngine(STTEngine):
    name = "faster-whisper"

    def __init__(self) -> None:
        self._model = None
        self._beam_size = 5

    def load(self, model: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe_numpy(self, samples, sample_rate, language=None) -> Transcript:
        if self._model is None:
            raise RuntimeError("FasterWhisperEngine.load() must be called before transcribe")
        if sample_rate != 16000:
            import scipy.signal as ss

            samples = ss.resample(samples, int(len(samples) * 16000 / sample_rate)).astype(
                np.float32
            )
        segments, _ = self._model.transcribe(
            samples, language=language, beam_size=self._beam_size, vad_filter=False
        )
        seglist = list(segments)
        text = "".join(s.text for s in seglist).strip()
        return Transcript(text=text, segments=seglist)

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            streaming=False,
            languages="multilingual",
            native_punctuation=True,
            devices=["cpu", "cuda"],
        )

    def unload(self) -> None:
        self._model = None
