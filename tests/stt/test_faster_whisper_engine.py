import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from whisper_aloud.stt.faster_whisper_engine import FasterWhisperEngine
from whisper_aloud.stt import Transcript, EngineCapabilities


def test_capabilities():
    eng = FasterWhisperEngine()
    caps = eng.capabilities
    assert isinstance(caps, EngineCapabilities)
    assert caps.streaming is False
    assert caps.languages == "multilingual"
    assert "cpu" in caps.devices and "cuda" in caps.devices


def test_transcribe_numpy_joins_segments():
    eng = FasterWhisperEngine()
    seg1 = MagicMock(); seg1.text = "hola "
    seg2 = MagicMock(); seg2.text = "mundo"
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([seg1, seg2], MagicMock())
    eng._model = fake_model  # inject loaded model
    out = eng.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000, language="es")
    assert isinstance(out, Transcript)
    assert out.text == "hola mundo"
    fake_model.transcribe.assert_called_once()


def test_transcribe_without_load_raises():
    with pytest.raises(RuntimeError):
        FasterWhisperEngine().transcribe_numpy(np.zeros(10, dtype=np.float32), 16000)
