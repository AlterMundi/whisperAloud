import numpy as np
import pytest
from whisper_aloud.stt import STTEngine, Transcript, EngineCapabilities


class DummyEngine(STTEngine):
    name = "dummy"

    def load(self, model, device, compute_type):
        self._loaded = (model, device, compute_type)

    def transcribe_numpy(self, samples, sample_rate, language=None):
        return Transcript(text=f"{len(samples)}@{sample_rate}")

    @property
    def capabilities(self):
        return EngineCapabilities(
            streaming=False, languages=["es", "en"],
            native_punctuation=True, devices=["cpu"],
        )


def test_transcript_defaults():
    t = Transcript(text="hola")
    assert t.text == "hola"
    assert t.segments is None


def test_capabilities_fields():
    caps = EngineCapabilities(streaming=True, languages="multilingual",
                              native_punctuation=False, devices=["cpu", "cuda"])
    assert caps.streaming is True
    assert caps.languages == "multilingual"
    assert "cuda" in caps.devices


def test_transcribe_file_delegates_to_numpy(tmp_path):
    import soundfile as sf
    p = tmp_path / "a.wav"
    sf.write(p, np.zeros(16000, dtype=np.float32), 16000)
    eng = DummyEngine()
    eng.load("m", "cpu", "int8")
    result = eng.transcribe_file(str(p))
    assert result.text == "16000@16000"


def test_unload_default_is_noop():
    DummyEngine().unload()  # must not raise
