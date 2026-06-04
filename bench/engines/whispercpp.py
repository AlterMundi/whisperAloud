"""Bench-local whisper.cpp adapter (wraps the whisper-cli binary; CPU + Vulkan).

Lives in bench/ (not src/) until promoted to a src/ STTEngine in WHISP-38.
`model` is a path to a GGUF model file. Requires `whisper-cli` on PATH or
WHISPERCPP_BIN env var pointing at it.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from whisper_aloud.stt.base import EngineCapabilities, STTEngine, Transcript


class WhisperCppEngine(STTEngine):
    name = "whispercpp"

    def __init__(self) -> None:
        self._model = None
        self._bin = os.environ.get("WHISPERCPP_BIN", "whisper-cli")

    def load(self, model: str, device: str, compute_type: str) -> None:
        if not os.path.exists(model):
            raise FileNotFoundError(f"whisper.cpp GGUF model not found: {model}")
        self._model = model

    def transcribe_numpy(self, samples: np.ndarray, sample_rate: int,
                         language: str | None = None) -> Transcript:
        if self._model is None:
            raise RuntimeError("WhisperCppEngine.load() must be called before transcribe")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name
        try:
            sf.write(wav_path, np.asarray(samples, dtype=np.float32), sample_rate)
            cmd = [self._bin, "-m", self._model, "-f", wav_path, "-nt", "-otxt", "-of", wav_path]
            if language:
                cmd += ["-l", language]
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
            txt_path = wav_path + ".txt"
            text = ""
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    text = f.read().strip()
                os.remove(txt_path)
            return Transcript(text=text)
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            streaming=False, languages="multilingual",
            native_punctuation=True, devices=["cpu", "vulkan"],
        )

    def unload(self) -> None:
        self._model = None
