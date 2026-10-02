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


def test_transcribe_numpy_does_not_apply_highpass():
    """WHISP-83: live dictation audio has already passed through the
    daemon's AudioPipeline (including its own high-pass stage, see
    AudioProcessingConfig.highpass_hz) before reaching here --
    transcribe_numpy must hand samples to the model byte-for-byte unchanged
    (no second, file-transcription-style highpass/normalize pass), unlike
    Transcriber.transcribe_file(), which does its own pre-processing. This
    guards against ever filtering live audio twice.
    """
    eng = FasterWhisperEngine()
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([], MagicMock())
    eng._model = fake_model

    rng = np.random.default_rng(0)
    # Samples with deliberate low-frequency rumble content -- if transcribe_numpy
    # ever started high-pass filtering, this is what would change.
    samples = (0.1 * rng.normal(size=16000)).astype(np.float32)
    samples_copy = samples.copy()

    eng.transcribe_numpy(samples, 16000)

    passed_samples = fake_model.transcribe.call_args[0][0]
    np.testing.assert_array_equal(passed_samples, samples_copy)
