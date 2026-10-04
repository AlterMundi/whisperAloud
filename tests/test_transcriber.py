"""Tests for transcriber functionality."""

import math
from unittest.mock import Mock, patch

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import AudioFormatError, ModelLoadError
from whisper_aloud.transcriber import Transcriber, TranscriptionResult


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """Isolate every test in this module from the real
    ~/.config/whisper_aloud/config.json -- WhisperAloudConfig.load() resolves
    its path via Path.home(), so every call in this file must never read (or,
    though nothing here calls save(), write) the real user's config."""
    monkeypatch.setenv("HOME", str(tmp_path))


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
    file-path-only loudness normalization pass (WHISP-77), even if the config
    explicitly turns normalize_loudness on."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio = (0.01 * np.ones(16000, dtype=np.float32))
    config = WhisperAloudConfig.load()
    config.transcription.normalize_loudness = True  # opted in; still must not apply here
    transcriber = Transcriber(config)

    with patch('whisper_aloud.stt.normalize.normalize_loudness') as mock_normalize:
        transcriber.transcribe_numpy(audio)
        mock_normalize.assert_not_called()

    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    np.testing.assert_array_equal(passed_audio, audio)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_applies_highpass_by_default(mock_whisper_model, tmp_path):
    """The file-transcription path applies the config's highpass_hz by
    default -- this is what actually fixes WHISP-77's quiet-remote-speaker
    VAD drop for CLI/batch file transcription (normalize_loudness defaults to
    off; see stt/normalize.py's docstring for why)."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")  # never actually decoded; loader is mocked

    config = WhisperAloudConfig.load()
    assert config.transcription.highpass_hz == 80.0  # the default
    assert config.transcription.normalize_loudness is False  # the default
    transcriber = Transcriber(config)

    raw_samples = np.array([0.01, 0.02, 0.03], dtype=np.float32)
    filtered_samples = np.array([0.3, 0.4, 0.5], dtype=np.float32)

    with patch(
        'whisper_aloud.stt.audio_io.load_audio_16k_mono', return_value=raw_samples
    ) as mock_load, patch(
        'whisper_aloud.stt.normalize.apply_highpass_filter', return_value=filtered_samples
    ) as mock_highpass, patch(
        'whisper_aloud.stt.normalize.normalize_loudness'
    ) as mock_normalize:
        transcriber.transcribe_file(str(audio_file))

    mock_load.assert_called_once_with(str(audio_file))
    mock_highpass.assert_called_once_with(raw_samples, 16000, cutoff_hz=80.0)
    mock_normalize.assert_not_called()
    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    np.testing.assert_array_equal(passed_audio, filtered_samples)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_can_opt_into_normalization(mock_whisper_model, tmp_path):
    """normalize_loudness can still be opted into explicitly, applied after
    the highpass filter."""
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    config = WhisperAloudConfig.load()
    config.transcription.normalize_loudness = True
    transcriber = Transcriber(config)

    raw_samples = np.array([0.01, 0.02, 0.03], dtype=np.float32)
    filtered_samples = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    normalized_samples = np.array([0.3, 0.4, 0.5], dtype=np.float32)

    with patch(
        'whisper_aloud.stt.audio_io.load_audio_16k_mono', return_value=raw_samples
    ), patch(
        'whisper_aloud.stt.normalize.apply_highpass_filter', return_value=filtered_samples
    ), patch(
        'whisper_aloud.stt.normalize.normalize_loudness', return_value=normalized_samples
    ) as mock_normalize:
        transcriber.transcribe_file(str(audio_file))

    mock_normalize.assert_called_once_with(filtered_samples, 16000)
    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    np.testing.assert_array_equal(passed_audio, normalized_samples)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_skips_all_preprocessing_when_disabled(mock_whisper_model, tmp_path):
    mock_model_instance = Mock()
    mock_whisper_model.return_value = mock_model_instance
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    audio_file = tmp_path / "meeting.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    config = WhisperAloudConfig.load()
    config.transcription.highpass_hz = 0  # disabled
    config.transcription.normalize_loudness = False  # already the default
    transcriber = Transcriber(config)

    with patch('whisper_aloud.stt.audio_io.load_audio_16k_mono') as mock_load, patch(
        'whisper_aloud.stt.normalize.apply_highpass_filter'
    ) as mock_highpass, patch(
        'whisper_aloud.stt.normalize.normalize_loudness'
    ) as mock_normalize:
        transcriber.transcribe_file(str(audio_file))

    mock_load.assert_not_called()
    mock_highpass.assert_not_called()
    mock_normalize.assert_not_called()
    passed_audio = mock_model_instance.transcribe.call_args[0][0]
    assert passed_audio == str(audio_file)


@patch('whisper_aloud.transcriber.WhisperModel')
def test_transcribe_file_preprocessing_failure_falls_back_to_raw_path(mock_whisper_model, tmp_path):
    """A decode/filter failure must not break transcription -- fall back to
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


# --- WHISP-94: loading an already-cached model must never touch the network ---


def _mock_loadable_model(mock_whisper_model):
    """Wire `mock_whisper_model` (the patched WhisperModel class) to return an
    instance whose dummy-inference test call in load_model() succeeds."""
    mock_model_instance = Mock()
    mock_segment = Mock(text="test", start=0.0, end=1.0, avg_logprob=-0.1)
    mock_info = Mock(language="en", duration=1.0)
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)
    mock_whisper_model.return_value = mock_model_instance
    return mock_model_instance


@patch('whisper_aloud.transcriber.WhisperModel')
def test_load_model_passes_local_files_only_true_when_cached(mock_whisper_model):
    """A model already in the local cache must load with local_files_only=True
    -- this is what prevents WhisperModel/huggingface_hub from making any
    network request to check the remote revision."""
    _mock_loadable_model(mock_whisper_model)

    config = WhisperAloudConfig.load()
    transcriber = Transcriber(config)

    with patch('whisper_aloud.transcriber.hf_download.is_cached', return_value=True) as mock_is_cached:
        transcriber.load_model()

    mock_is_cached.assert_called_once_with(config.model.name, config.model.download_root)
    assert mock_whisper_model.call_args.kwargs["local_files_only"] is True


@patch('whisper_aloud.transcriber.WhisperModel')
def test_load_model_downloads_when_not_cached(mock_whisper_model, caplog):
    """When the model genuinely isn't cached yet, load_model() may still fall
    back to a network-capable load (today's first-use-downloads behavior),
    but must log clearly that it is about to do so."""
    _mock_loadable_model(mock_whisper_model)

    config = WhisperAloudConfig.load()
    assert config.model.allow_network_download is True
    transcriber = Transcriber(config)

    with patch('whisper_aloud.transcriber.hf_download.is_cached', return_value=False):
        with caplog.at_level("INFO"):
            transcriber.load_model()

    assert mock_whisper_model.call_args.kwargs["local_files_only"] is False
    assert any(
        "not in cache" in record.message or "not in cache" in record.getMessage()
        for record in caplog.records
    )


@patch('whisper_aloud.transcriber.WhisperModel')
def test_load_model_raises_when_network_disabled_and_not_cached(mock_whisper_model):
    """model.allow_network_download=False must make a missing model a hard
    error instead of silently reaching the network -- and WhisperModel must
    never even be constructed in that case."""
    config = WhisperAloudConfig.load()
    config.model.allow_network_download = False
    transcriber = Transcriber(config)

    with patch('whisper_aloud.transcriber.hf_download.is_cached', return_value=False):
        with pytest.raises(ModelLoadError):
            transcriber.load_model()

    mock_whisper_model.assert_not_called()


@patch('whisper_aloud.transcriber.WhisperModel')
def test_try_load_model_cpu_fallback_respects_network_flag(mock_whisper_model):
    """The CUDA->CPU fallback path (_try_load_model) must apply the same
    cached/local_files_only logic as load_model, including refusing to reach
    the network when allow_network_download is False."""
    _mock_loadable_model(mock_whisper_model)

    config = WhisperAloudConfig.load()
    config.model.allow_network_download = False
    transcriber = Transcriber(config)

    with patch('whisper_aloud.transcriber.hf_download.is_cached', return_value=False):
        result = transcriber._try_load_model("cpu", "int8")

    assert result is False
    mock_whisper_model.assert_not_called()
