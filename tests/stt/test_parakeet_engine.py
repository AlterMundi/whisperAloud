import numpy as np
import pytest
from unittest.mock import MagicMock
from whisper_aloud.stt.parakeet_engine import ParakeetEngine
from whisper_aloud.stt import Transcript, EngineCapabilities


def test_capabilities():
    caps = ParakeetEngine().capabilities
    assert isinstance(caps, EngineCapabilities)
    assert caps.native_punctuation is True
    assert "cpu" in caps.devices and "cuda" in caps.devices


def test_transcribe_numpy_uses_recognizer():
    eng = ParakeetEngine()
    fake_stream = MagicMock()
    fake_stream.result.text = "hola mundo"
    fake_rec = MagicMock()
    fake_rec.create_stream.return_value = fake_stream
    eng._rec = fake_rec  # inject loaded recognizer
    out = eng.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000, language="es")
    assert isinstance(out, Transcript)
    assert out.text == "hola mundo"
    fake_stream.accept_waveform.assert_called_once()
    fake_rec.decode_stream.assert_called_once_with(fake_stream)


def test_transcribe_without_load_raises():
    with pytest.raises(RuntimeError):
        ParakeetEngine().transcribe_numpy(np.zeros(10, dtype=np.float32), 16000)
