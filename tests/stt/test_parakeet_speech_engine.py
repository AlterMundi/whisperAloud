"""Tests for the production Parakeet engine (WHISP-36)."""
import importlib.util
import sys
from unittest.mock import MagicMock, patch

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


class TestLoadModelErrorPaths:
    """WHISP-45: load_model() error handling, mocked at the sherpa-onnx/filesystem boundary."""

    def test_load_model_raises_import_error_without_sherpa(self):
        """ImportError is re-raised with an actionable install hint when sherpa-onnx is absent."""
        engine = ParakeetSpeechEngine(_cfg())
        with patch.dict(sys.modules, {"sherpa_onnx": None}):
            with pytest.raises(ImportError, match="pip install"):
                engine.load_model()
        assert engine.is_loaded is False

    def test_load_model_raises_when_model_files_missing(self, tmp_path):
        """A model dir without the expected onnx files fails fast with FileNotFoundError."""
        engine = ParakeetSpeechEngine(_cfg())
        with patch("whisper_aloud.stt.model_download.ensure_model", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="encoder"):
                engine.load_model()
        assert engine.is_loaded is False

    def _write_fake_model_files(self, tmp_path):
        (tmp_path / "encoder.onnx").write_bytes(b"")
        (tmp_path / "decoder.onnx").write_bytes(b"")
        (tmp_path / "joiner.onnx").write_bytes(b"")
        (tmp_path / "tokens.txt").write_text("")
        return tmp_path

    def test_load_model_falls_back_to_cpu_when_cuda_init_fails(self, tmp_path):
        """A CUDA build failure on a cuda-configured device falls back to CPU instead of raising."""
        config = _cfg()
        config.model.device = "cuda"
        engine = ParakeetSpeechEngine(config)
        self._write_fake_model_files(tmp_path)

        fake_sherpa = MagicMock()
        cpu_recognizer = MagicMock()
        fake_sherpa.OfflineRecognizer.from_transducer.side_effect = [
            RuntimeError("no CUDA device"),
            cpu_recognizer,
        ]

        with patch("whisper_aloud.stt.model_download.ensure_model", return_value=tmp_path), \
             patch.dict(sys.modules, {"sherpa_onnx": fake_sherpa}):
            engine.load_model()

        assert engine.is_loaded is True
        assert engine._device_in_use == "cpu"
        assert fake_sherpa.OfflineRecognizer.from_transducer.call_count == 2

    def test_load_model_propagates_cpu_build_failure(self, tmp_path):
        """A build failure on a cpu-configured device has no fallback and must propagate."""
        config = _cfg()
        config.model.device = "cpu"
        engine = ParakeetSpeechEngine(config)
        self._write_fake_model_files(tmp_path)

        fake_sherpa = MagicMock()
        fake_sherpa.OfflineRecognizer.from_transducer.side_effect = RuntimeError("onnxruntime missing")

        with patch("whisper_aloud.stt.model_download.ensure_model", return_value=tmp_path), \
             patch.dict(sys.modules, {"sherpa_onnx": fake_sherpa}):
            with pytest.raises(RuntimeError, match="onnxruntime missing"):
                engine.load_model()
        assert engine.is_loaded is False

    def test_load_model_is_idempotent(self, tmp_path):
        """A second load_model() call is a no-op once a recognizer is already loaded."""
        engine = ParakeetSpeechEngine(_cfg())
        engine._rec = object()
        with patch("whisper_aloud.stt.model_download.ensure_model") as mock_ensure:
            engine.load_model()
        mock_ensure.assert_not_called()


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
