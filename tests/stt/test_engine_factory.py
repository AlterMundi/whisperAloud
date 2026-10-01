"""Tests for the production SpeechEngine protocol + create_engine factory (WHISP-35)."""
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import ConfigurationError
from whisper_aloud.stt.engine import SpeechEngine, create_engine
from whisper_aloud.transcriber import Transcriber


def test_transcriber_satisfies_speech_engine_protocol():
    """Transcriber structurally conforms to the SpeechEngine protocol."""
    config = WhisperAloudConfig()
    engine = Transcriber(config)
    assert isinstance(engine, SpeechEngine)


def test_create_engine_returns_transcriber_for_faster_whisper():
    """The factory builds a Transcriber for the faster-whisper engine."""
    config = WhisperAloudConfig()
    config.model.engine = "faster-whisper"
    engine = create_engine(config)
    assert isinstance(engine, Transcriber)
    assert isinstance(engine, SpeechEngine)


def test_create_engine_rejects_unknown_engine():
    """The factory raises ConfigurationError for an unknown engine."""
    config = WhisperAloudConfig()
    config.model.engine = "totally-unknown"
    with pytest.raises(ConfigurationError, match="Unknown STT engine"):
        create_engine(config)


def test_create_engine_returns_parakeet_for_parakeet():
    """The factory builds a ParakeetSpeechEngine for engine=parakeet (no sherpa needed)."""
    from whisper_aloud.stt.parakeet_speech_engine import ParakeetSpeechEngine
    config = WhisperAloudConfig()
    config.model.engine = "parakeet"
    config.model.name = "parakeet-tdt-0.6b-v3"
    engine = create_engine(config)
    assert isinstance(engine, ParakeetSpeechEngine)
    assert isinstance(engine, SpeechEngine)


def test_create_engine_dispatches_fake(monkeypatch):
    """engine="fake" dispatches to FakeSpeechEngine (WHISP-46 e2e harness)."""
    config = WhisperAloudConfig()
    config.model.engine = "fake"
    config.model.name = "fake"
    from whisper_aloud.stt.fake_engine import FakeSpeechEngine

    assert isinstance(create_engine(config), FakeSpeechEngine)


def test_create_engine_dispatches_fake_streaming(monkeypatch):
    """engine="fake", name="fake-streaming" dispatches to FakeStreamingSpeechEngine
    (WHISP-57). The catalog only registers that model name under TEST_MODE=1, so
    the catalog module must be reloaded with the env var set, same as
    tests/stt/test_catalog.py's TestFakeEngineTestModeGating."""
    import importlib

    from whisper_aloud.stt import catalog as catalog_module

    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    importlib.reload(catalog_module)
    try:
        config = WhisperAloudConfig()
        config.model.engine = "fake"
        config.model.name = "fake-streaming"
        from whisper_aloud.stt.fake_engine import FakeStreamingSpeechEngine

        engine = create_engine(config)
        assert isinstance(engine, FakeStreamingSpeechEngine)
        assert engine.is_streaming is True
    finally:
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        importlib.reload(catalog_module)
