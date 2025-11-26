"""Integration tests for WhisperAloud (optional, requires real model)."""

import os
import pytest
from pathlib import Path

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.transcriber import Transcriber


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("CI"), reason="Skip in CI environment")
@pytest.mark.skipif(not Path("tests/fixtures/sample_audio.wav").exists(),
                   reason="Test fixture not available")
def test_real_model_transcription():
    """Test with actual Whisper model (downloads ~140MB on first run)."""
    config = WhisperAloudConfig.load()
    config.model.name = "tiny"  # Smallest model for testing

    transcriber = Transcriber(config)
    result = transcriber.transcribe_file("tests/fixtures/sample_audio.wav")

    # Basic validation
    assert isinstance(result.text, str)
    assert len(result.text) >= 0  # Could be empty for tone
    assert result.language in ["en", "es", "auto"]  # Expected languages
    assert result.duration > 0
    assert result.processing_time > 0
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.segments, list)

    # Validate segments structure
    for segment in result.segments:
        assert "text" in segment
        assert "start" in segment
        assert "end" in segment
        assert "confidence" in segment
        assert isinstance(segment["start"], (int, float))
        assert isinstance(segment["end"], (int, float))
        assert 0.0 <= segment["confidence"] <= 1.0


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("CI"), reason="Skip in CI environment")
def test_model_loading_and_unloading():
    """Test model loading and unloading with real model."""
    config = WhisperAloudConfig.load()
    config.model.name = "tiny"

    transcriber = Transcriber(config)
    assert not transcriber.is_loaded

    # Load model
    transcriber.load_model()
    assert transcriber.is_loaded

    # Unload model
    transcriber.unload_model()
    assert not transcriber.is_loaded


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("CI"), reason="Skip in CI environment")
def test_different_model_sizes():
    """Test transcription with different model sizes."""
    test_file = "tests/fixtures/sample_audio.wav"
    if not Path(test_file).exists():
        pytest.skip("Test fixture not available")

    models_to_test = ["tiny", "base"]

    for model_name in models_to_test:
        config = WhisperAloudConfig.load()
        config.model.name = model_name

        transcriber = Transcriber(config)
        result = transcriber.transcribe_file(test_file)

        # Basic validation
        assert isinstance(result.text, str)
        assert result.language in ["en", "es", "auto"]
        assert result.duration > 0
        assert result.processing_time > 0
        assert 0.0 <= result.confidence <= 1.0

        # Clean up
        transcriber.unload_model()


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("CI"), reason="Skip in CI environment")
def test_numpy_array_transcription():
    """Test numpy array transcription with real model."""
    import numpy as np

    config = WhisperAloudConfig.load()
    config.model.name = "tiny"

    # Create a short test audio (1 second silence)
    audio = np.zeros(16000, dtype=np.float32)

    transcriber = Transcriber(config)
    result = transcriber.transcribe_numpy(audio)

    # Basic validation
    assert isinstance(result.text, str)
    assert result.duration == 1.0  # 16000 samples / 16000 Hz
    assert result.processing_time > 0
    assert 0.0 <= result.confidence <= 1.0