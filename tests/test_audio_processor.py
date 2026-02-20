"""Tests for audio processing."""

import numpy as np
import pytest

from whisper_aloud.audio import AudioProcessor


def test_normalize():
    """Test audio normalization."""
    audio = np.array([0.5, -0.5, 0.25], dtype=np.float32)
    normalized = AudioProcessor.normalize(audio, target_level=0.95)

    assert np.max(np.abs(normalized)) == pytest.approx(0.95, abs=0.01)


def test_normalize_empty():
    """Test normalization with empty array."""
    audio = np.array([], dtype=np.float32)
    normalized = AudioProcessor.normalize(audio)
    assert len(normalized) == 0


def test_stereo_to_mono():
    """Test stereo to mono conversion."""
    stereo = np.array([[0.5, 0.3], [0.2, 0.4]], dtype=np.float32)
    mono = AudioProcessor.stereo_to_mono(stereo)

    assert mono.ndim == 1
    assert len(mono) == 2
    assert mono[0] == pytest.approx(0.4, abs=0.01)  # Average of 0.5 and 0.3


def test_stereo_to_mono_already_mono():
    """Test stereo to mono with already mono audio."""
    mono = np.array([0.5, 0.3, 0.2], dtype=np.float32)
    result = AudioProcessor.stereo_to_mono(mono)

    assert np.array_equal(result, mono)


def test_resample():
    """Test audio resampling."""
    audio = np.sin(2 * np.pi * 440 * np.linspace(0, 1, 44100)).astype(np.float32)
    resampled = AudioProcessor.resample(audio, 44100, 16000)

    expected_length = int(len(audio) * 16000 / 44100)
    assert len(resampled) == expected_length


def test_resample_same_rate():
    """Test resampling with same input/output rate."""
    audio = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    resampled = AudioProcessor.resample(audio, 16000, 16000)

    assert np.array_equal(resampled, audio)


def test_detect_voice_activity():
    """Test VAD."""
    # Silent audio
    silent = np.zeros(16000, dtype=np.float32)
    activity = AudioProcessor.detect_voice_activity(silent, threshold=0.02)
    assert not activity.any()

    # Loud audio
    loud = np.ones(16000, dtype=np.float32) * 0.5
    activity = AudioProcessor.detect_voice_activity(loud, threshold=0.02)
    assert activity.any()


def test_detect_voice_activity_empty():
    """Test VAD with empty audio."""
    empty = np.array([], dtype=np.float32)
    activity = AudioProcessor.detect_voice_activity(empty)
    assert len(activity) == 0


def test_trim_silence():
    """Test silence trimming."""
    # Create audio with silence at start and end
    silence = np.zeros(8000, dtype=np.float32)  # 0.5s silence
    voice = np.ones(8000, dtype=np.float32) * 0.1  # 0.5s voice
    audio = np.concatenate([silence, voice, silence])

    trimmed, start, end = AudioProcessor.trim_silence(audio, 16000)

    # Should trim most silence, keep some padding
    assert len(trimmed) < len(audio)
    assert start >= 0
    assert end <= len(audio)


def test_trim_silence_no_voice():
    """Test trimming when no voice is detected."""
    silent = np.zeros(16000, dtype=np.float32)
    trimmed, start, end = AudioProcessor.trim_silence(silent, 16000)

    # Should return original with start=0, end=len
    assert len(trimmed) == len(silent)
    assert start == 0
    assert end == len(silent)


def test_process_recording():
    """Test complete audio processing pipeline."""
    # Create stereo audio at 44.1kHz (samples x channels)
    audio = np.random.randn(44100, 2).astype(np.float32) * 0.1

    processed = AudioProcessor.process_recording(
        audio,
        sample_rate=44100,
        target_rate=16000,
        normalize=True,
        trim_silence_enabled=False
    )

    # Should be mono, resampled, normalized
    assert processed.ndim == 1
    assert len(processed) < 44100  # Resampled down from 44100 samples
    assert np.max(np.abs(processed)) <= 1.0


def test_process_recording_empty():
    """Test processing pipeline with empty audio."""
    empty = np.array([], dtype=np.float32)
    processed = AudioProcessor.process_recording(empty, 16000)

    assert len(processed) == 0
