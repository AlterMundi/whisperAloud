"""Tests for the production Parakeet engine (WHISP-36)."""
import importlib.util

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.stt.engine import SpeechEngine
from whisper_aloud.stt.parakeet_speech_engine import ParakeetSpeechEngine

_HAS_SHERPA = importlib.util.find_spec("sherpa_onnx") is not None


def _cfg():
    c = WhisperAloudConfig()
    c.model.engine = "parakeet"
    c.model.name = "parakeet-tdt-0.6b-v3"
    return c


def test_parakeet_satisfies_speech_engine_protocol():
    """ParakeetSpeechEngine structurally conforms to SpeechEngine (no sherpa needed)."""
    engine = ParakeetSpeechEngine(_cfg())
    assert isinstance(engine, SpeechEngine)


def test_cancel_before_transcribe_returns_empty():
    """A pending cancel makes transcribe_numpy return an empty result and clears the flag."""
    engine = ParakeetSpeechEngine(_cfg())
    engine._rec = object()  # pretend loaded so load_model isn't invoked
    engine.cancel_transcription()
    assert engine.is_cancelling
    out = engine.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000)
    assert out.text == ""
    assert out.duration == pytest.approx(1.0, abs=0.05)
    assert engine.is_cancelling is False


def test_unload_clears_loaded_state():
    engine = ParakeetSpeechEngine(_cfg())
    engine._rec = object()
    assert engine.is_loaded
    engine.unload_model()
    assert engine.is_loaded is False


@pytest.mark.skipif(not _HAS_SHERPA, reason="sherpa-onnx not installed")
def test_transcribe_numpy_smoke():
    """Real decode on a synthetic clip — skipped unless sherpa-onnx AND model are present."""
    from whisper_aloud.stt.model_download import model_dir
    engine = ParakeetSpeechEngine(_cfg())
    if not (model_dir("parakeet-tdt-0.6b-v3") / "tokens.txt").exists():
        pytest.skip("parakeet model not downloaded")
    out = engine.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000)
    assert isinstance(out.text, str)
    assert out.language == engine.config.transcription.language
    assert out.segments == []
    assert out.confidence == 0.0
