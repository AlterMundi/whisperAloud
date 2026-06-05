"""NVIDIA Parakeet TDT transducer adapter via sherpa-onnx.

`model` is a directory containing encoder*.onnx, decoder*.onnx, joiner*.onnx, tokens.txt
(e.g. the sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 release).
"""
from __future__ import annotations

import glob
import os

import numpy as np

from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript


class ParakeetEngine(STTEngine):
    name = "parakeet"

    def __init__(self) -> None:
        self._rec = None
        self._model_dir = None

    def load(self, model: str, device: str, compute_type: str) -> None:
        import sherpa_onnx

        def _one(pattern):
            matches = glob.glob(os.path.join(model, pattern))
            if not matches:
                raise FileNotFoundError(f"{pattern} not found in model dir: {model}")
            return matches[0]

        enc = _one("encoder*.onnx")
        dec = _one("decoder*.onnx")
        joi = _one("joiner*.onnx")
        tokens = os.path.join(model, "tokens.txt")
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=enc,
            decoder=dec,
            joiner=joi,
            tokens=tokens,
            num_threads=os.cpu_count() or 4,
            model_type="nemo_transducer",
            provider=("cuda" if device == "cuda" else "cpu"),
        )
        self._model_dir = model

    def transcribe_numpy(self, samples: np.ndarray, sample_rate: int,
                         language: str | None = None) -> Transcript:
        if self._rec is None:
            raise RuntimeError("ParakeetEngine.load() must be called before transcribe")
        if sample_rate != 16000:
            import scipy.signal as ss

            n = int(len(samples) * 16000 / sample_rate)
            samples = ss.resample(samples, n).astype(np.float32)
            sample_rate = 16000
        stream = self._rec.create_stream()
        stream.accept_waveform(sample_rate, np.asarray(samples, dtype=np.float32))
        self._rec.decode_stream(stream)
        return Transcript(text=stream.result.text.strip())

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            streaming=False,  # offline mode here; streaming variants are WHISP-37
            languages=["es", "en", "fr", "de", "it", "pt"],  # v3 = 25 EU langs (subset listed)
            native_punctuation=True,
            devices=["cpu", "cuda"],
        )

    def unload(self) -> None:
        self._rec = None
