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
