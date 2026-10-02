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


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_numpy_auto_language_uses_none(mock_whisper_model):
    """'auto' should map to language=None for faster-whisper auto-detection."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance

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
    transcriber.transcribe_numpy(audio, language="auto")

    call_args = mock_model_instance.transcribe.call_args
    assert call_args[1]["language"] is None


# ─── WHISP-77: configurable VAD params + file-path loudness normalization ──


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_numpy_passes_configured_vad_parameters(mock_whisper_model):
    """vad_filter=True should pass vad_parameters built from the config's
    (more permissive than Silero's own defaults) threshold/min_speech_ms."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio = np.zeros(16000, dtype=np.float32)
    config = WhisperAloudConfig.load()
    config.transcription.vad_filter = True
    config.transcription.vad_threshold = 0.22
    config.transcription.vad_min_speech_ms = 75
    transcriber = Transcriber(config)

    transcriber.transcribe_numpy(audio)

    call_kwargs = mock_model_instance.transcribe.call_args[1]
    assert call_kwargs["vad_filter"] is True
    assert call_kwargs["vad_parameters"] == {"threshold": 0.22, "min_speech_duration_ms": 75}


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_numpy_no_vad_parameters_when_vad_disabled(mock_whisper_model):
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio = np.zeros(16000, dtype=np.float32)
    config = WhisperAloudConfig.load()
    config.transcription.vad_filter = False
    transcriber = Transcriber(config)

    transcriber.transcribe_numpy(audio)

    call_kwargs = mock_model_instance.transcribe.call_args[1]
    assert call_kwargs["vad_filter"] is False
    assert "vad_parameters" not in call_kwargs


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_numpy_never_normalizes_loudness(mock_whisper_model):
    """transcribe_numpy backs the daemon's live-dictation path, which already
    ran the audio through AudioPipeline's AGC -- it must never apply a second,
    file-path-only loudness normalization pass (WHISP-77), even when the
    config's normalize_loudness default is True."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio = (0.01 * np.ones(16000, dtype=np.float32))
    config = WhisperAloudConfig.load()
    assert config.transcription.normalize_loudness is True  # the default
    transcriber = Transcriber(config)

    with patch('whisper_aloud.stt.normalize.normalize_loudness') as mock_normalize:
        transcriber.transcribe_numpy(audio)
        mock_normalize.assert_not_called()

    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    np.testing.assert_array_equal(passed_audio, audio)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_normalizes_loudness_by_default(mock_whisper_model, tmp_path):
    """The file-transcription path (unlike transcribe_numpy) applies the
    config's normalize_loudness setting -- this is what actually fixes
    WHISP-77's quiet-remote-speaker drop for CLI/batch file transcription."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")  # never actually decoded; loader is mocked

    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    raw_samples = np.array([0.01, 0.02, 0.03], dtype=np.float32)
    normalized_samples = np.array([0.3, 0.4, 0.5], dtype=np.float32)

    with patch(
        'whisper_aloud.stt.audio_io.load_audio_16k_mono', return_value=raw_samples
    ) as mock_load, patch(
        'whisper_aloud.stt.normalize.normalize_loudness', return_value=normalized_samples
    ) as mock_normalize:
        transcriber.transcribe_file(str(audio_file))

    mock_load.assert_called_once_with(str(audio_file))
    mock_normalize.assert_called_once()
    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    np.testing.assert_array_equal(passed_audio, normalized_samples)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_skips_normalization_when_disabled(mock_whisper_model, tmp_path):
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    config = WhisperAloudConfig.load()
    config.transcription.normalize_loudness = False
    transcriber = Transcriber(config)

    with patch('whisper_aloud.stt.audio_io.load_audio_16k_mono') as mock_load, patch(
        'whisper_aloud.stt.normalize.normalize_loudness'
    ) as mock_normalize:
        transcriber.transcribe_file(str(audio_file))

    mock_load.assert_not_called()
    mock_normalize.assert_not_called()
    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    assert passed_audio == str(audio_file)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_normalization_failure_falls_back_to_raw_path(mock_whisper_model, tmp_path):
    """A decode/normalize failure must not break transcription -- fall back to
    letting faster-whisper decode the file itself."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    with patch(
        'whisper_aloud.stt.audio_io.load_audio_16k_mono', side_effect=RuntimeError("boom")
    ):
        transcriber.transcribe_file(str(audio_file))

    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    assert passed_audio == str(audio_file)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_passes_configured_vad_parameters(mock_whisper_model, tmp_path):
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    config = WhisperAloudConfig.load()
    config.transcription.vad_filter = True
    config.transcription.vad_threshold = 0.3
    config.transcription.vad_min_speech_ms = 120
    config.transcription.normalize_loudness = False  # isolate VAD-param behavior
    transcriber = Transcriber(config)

    transcriber.transcribe_file(str(audio_file))

    call_kwargs = mock_model_instance.transcribe.call_args[1]
    assert call_kwargs["vad_parameters"] == {"threshold": 0.3, "min_speech_duration_ms": 120}


def test_transcriber_initialization_logging():
    """Test that transcriber logs initialization."""
    config = WhisperAloudConfig.load()
    with patch('whisper_aloud.transcriber.logger') as mock_logger:
        Transcriber(config)
        mock_logger.info.assert_called_with(
            "Transcriber initialized with model: %s, device: %s",
            config.model.name, config.model.device
        )
