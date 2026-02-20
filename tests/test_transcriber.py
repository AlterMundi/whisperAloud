"""Tests for transcriber functionality."""

import math
from unittest.mock import Mock, patch

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import AudioFormatError
from whisper_aloud.transcriber import Transcriber, TranscriptionResult


def test_transcriber_lazy_loading():
    """Test model is not loaded until first use."""
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)
    assert not transcriber.is_loaded
    # Note: We don't call load_model here to avoid downloading


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_silence(mock_whisper_model):
    """Test transcribing 1 second of silence."""
    # Mock the model and its transcribe method
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance

    # Mock segments generator
    mock_segment = Mock()
    mock_segment.text = ""
    mock_segment.start = 0.0
    mock_segment.end = 1.0
    mock_segment.avg_logprob = -0.1  # Low confidence for silence

    mock_info = Mock()
    mock_info.language = "en"
    mock_info.duration = 1.0

    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio = np.zeros(16000, dtype=np.float32)  # 1 second silence
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)
    result = transcriber.transcribe_numpy(audio)

    assert isinstance(result, TranscriptionResult)
    assert result.text == "" or len(result.text.strip()) < 10  # Should be empty or noise
    assert result.language == "en"
    assert result.duration == 1.0
    assert result.confidence > 0  # exp(-0.1) ≈ 0.905


@patch('whisper_aloud.transcriber.WhisperModel')
def test_gpu_fallback(mock_whisper_model):
    """Test that model loads successfully with mocked WhisperModel."""
    # Create a properly mocked model instance
    mock_model_instance = Mock()

    # Mock the transcribe method to return proper format for test inference
    mock_segment = Mock()
    mock_segment.text = "test"
    mock_info = Mock()
    mock_info.language = "en"
    mock_info.duration = 1.0
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    mock_whisper_model.return_value = mock_model_instance

    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)
    transcriber.load_model()

    assert transcriber.is_loaded
    mock_whisper_model.assert_called_once()


def test_invalid_audio_format_numpy():
    """Test proper error for invalid audio."""
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    # Test wrong dtype
    with pytest.raises(AudioFormatError):
        transcriber.transcribe_numpy(np.array([1, 2, 3], dtype=np.int32))

    # Test wrong shape
    with pytest.raises(AudioFormatError):
        transcriber.transcribe_numpy(np.array([[1, 2], [3, 4]], dtype=np.float32))

    # Test empty array
    with pytest.raises(AudioFormatError):
        transcriber.transcribe_numpy(np.array([], dtype=np.float32))


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_not_found(mock_whisper_model):
    """Test error when audio file not found."""
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    with pytest.raises(AudioFormatError, match="Audio file not found"):
        transcriber.transcribe_file("nonexistent.wav")


@patch('whisper_aloud.transcriber.WhisperModel')
def test_unload_model(mock_whisper_model):
    """Test unloading model."""
    mock_model_instance = Mock()

    # Mock transcribe for test inference during load
    mock_segment = Mock()
    mock_segment.text = "test"
    mock_info = Mock()
    mock_info.language = "en"
    mock_info.duration = 1.0
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    mock_whisper_model.return_value = mock_model_instance

    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)
    transcriber.load_model()
    assert transcriber.is_loaded

    transcriber.unload_model()
    assert not transcriber.is_loaded


def test_process_segments_method():
    """Test the _process_segments helper method."""
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    # Create mock segments
    mock_segments = [
        Mock(text="Hello", start=0.0, end=1.0, avg_logprob=-0.1),
        Mock(text="world", start=1.0, end=2.0, avg_logprob=-0.2),
    ]

    text, segment_list, confidence, was_cancelled = transcriber._process_segments(mock_segments, 2.0)

    assert text == "Helloworld"
    assert len(segment_list) == 2
    assert segment_list[0]["text"] == "Hello"
    assert segment_list[0]["confidence"] == pytest.approx(math.exp(-0.1))
    assert confidence == pytest.approx(math.exp((-0.1 - 0.2) / 2))
    assert was_cancelled is False


def test_process_segments_empty():
    """Test _process_segments with empty segments."""
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    text, segment_list, confidence, was_cancelled = transcriber._process_segments([], 0.0)

    assert text == ""
    assert segment_list == []
    assert confidence == 0.0
    assert was_cancelled is False


def test_process_segments_none_logprob():
    """Test _process_segments with None avg_logprob."""
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    mock_segments = [
        Mock(text="test", start=0.0, end=1.0, avg_logprob=None),
    ]

    text, segment_list, confidence, was_cancelled = transcriber._process_segments(mock_segments, 1.0)

    assert text == "test"
    assert segment_list[0]["confidence"] == 0.0
    assert confidence == 0.0
    assert was_cancelled is False


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_numpy_with_kwargs(mock_whisper_model):
    """Test transcribe_numpy with config overrides."""
    # Mock the model and its transcribe method
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance

    # Mock segments generator
    mock_segment = Mock()
    mock_segment.text = "test"
    mock_segment.start = 0.0
    mock_segment.end = 1.0
    mock_segment.avg_logprob = -0.1

    mock_info = Mock()
    mock_info.language = "en"
    mock_info.duration = 1.0

    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio = np.zeros(16000, dtype=np.float32)
    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    # Test with kwargs override
    result = transcriber.transcribe_numpy(audio, language="es", beam_size=3)

    # Verify transcribe was called with overridden parameters
    call_args = mock_model_instance.transcribe.call_args
    assert call_args[1]["language"] == "es"
    assert call_args[1]["beam_size"] == 3
    assert result.language == "en"  # From mock info


def test_transcriber_initialization_logging():
    """Test that transcriber logs initialization."""
    config = WhisperAloudConfig.load()
    with patch('whisper_aloud.transcriber.logger') as mock_logger:
        Transcriber(config)
        mock_logger.info.assert_called_with(
            "Transcriber initialized with model: %s, device: %s",
            config.model.name, config.model.device
        )
