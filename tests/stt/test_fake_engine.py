"""Unit tests for the deterministic FakeSpeechEngine test double (WHISP-46)."""
import time

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import ConfigurationError, TranscriptionError
from whisper_aloud.stt.fake_engine import FakeSpeechEngine


def _config():
    c = WhisperAloudConfig()
    c.model.engine = "fake"
    c.model.name = "fake"
    return c


def test_reads_sidecar_txt(monkeypatch, tmp_path):
    wav = tmp_path / "hello.wav"
    wav.write_bytes(b"RIFF....")  # engine never parses the WAV itself
    (tmp_path / "hello.txt").write_text("hello world")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    result = engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))
    assert result.text == "hello world"


def test_missing_sidecar_raises(monkeypatch, tmp_path):
    wav = tmp_path / "missing.wav"
    wav.write_bytes(b"")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    with pytest.raises(ConfigurationError, match="sidecar"):
        engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", raising=False)

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    with pytest.raises(ConfigurationError, match="WHISPER_ALOUD_FAKE_AUDIO_WAV"):
        engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))


def test_latency_injection(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"")
    (tmp_path / "a.txt").write_text("a")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S", "0.05")

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    start = time.monotonic()
    engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))
    assert time.monotonic() - start >= 0.05


def test_error_injection(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"")
    (tmp_path / "a.txt").write_text("a")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_ENGINE_ERROR", "boom")

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    with pytest.raises(TranscriptionError, match="boom"):
        engine.transcribe_numpy(np.zeros(1600, dtype=np.float32))


def test_cancel_and_lifecycle_flags():
    engine = FakeSpeechEngine(_config())
    assert engine.is_loaded is False
    engine.load_model()
    assert engine.is_loaded is True
    assert engine.is_cancelling is False
    engine.cancel_transcription()
    assert engine.is_cancelling is True
    engine.unload_model()
    assert engine.is_loaded is False


def test_transcribe_file_delegates_to_sidecar_lookup(monkeypatch, tmp_path):
    wav = tmp_path / "f.wav"
    wav.write_bytes(b"")
    (tmp_path / "f.txt").write_text("file path transcript")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    engine = FakeSpeechEngine(_config())
    engine.load_model()
    result = engine.transcribe_file(str(wav))
    assert result.text == "file path transcript"
