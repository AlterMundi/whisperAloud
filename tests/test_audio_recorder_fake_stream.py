"""Unit tests for AudioRecorder's fake WAV-backed input stream (WHISP-46 e2e
harness): WHISPER_ALOUD_FAKE_AUDIO_WAV (only honored under
WHISPER_ALOUD_TEST_MODE=1) substitutes a _FakeInputStream for sd.InputStream
so tests never need a real microphone or PortAudio device."""
import time

import numpy as np
import pytest
import soundfile as sf

from whisper_aloud.audio.recorder import AudioRecorder
from whisper_aloud.config import AudioConfig


def _write_wav(path, seconds=0.5, sample_rate=16000, freq=440.0):
    t = np.linspace(0, seconds, int(seconds * sample_rate), endpoint=False)
    data = (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), data, sample_rate)
    return data


def test_fake_stream_feeds_callback_without_real_device(monkeypatch, tmp_path):
    """No real sd.InputStream/device query happens when the fake-audio env is set."""
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path)
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav_path))

    import sounddevice as sd

    def _boom(*a, **k):
        raise AssertionError("real sd.InputStream must not be constructed in fake-audio mode")

    monkeypatch.setattr(sd, "InputStream", _boom)

    recorder = AudioRecorder(AudioConfig())
    recorder.start()
    time.sleep(0.6)  # let the fake stream push the whole 0.5s fixture
    audio = recorder.stop()
    assert len(audio) > 0


def test_fake_stream_pads_silence_past_eof(monkeypatch, tmp_path):
    """A recording held open longer than the fixture gets trailing silence, not a loop."""
    wav_path = tmp_path / "short.wav"
    _write_wav(wav_path, seconds=0.1)
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav_path))

    recorder = AudioRecorder(AudioConfig())
    recorder.start()
    time.sleep(0.4)
    audio = recorder.stop()
    # At 16kHz, 0.4s should be fully populated (padded), not truncated at ~0.1s.
    assert len(audio) >= int(0.3 * 16000)


def test_without_env_var_uses_real_sounddevice(monkeypatch):
    """Default path is untouched when the fake-audio env var is absent."""
    monkeypatch.delenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", raising=False)
    recorder = AudioRecorder(AudioConfig())
    assert recorder._fake_wav_path is None


def test_fake_audio_ignored_without_test_mode(monkeypatch, tmp_path):
    """Defense in depth: WHISPER_ALOUD_FAKE_AUDIO_WAV alone (without
    WHISPER_ALOUD_TEST_MODE=1) must not activate the fake stream."""
    wav_path = tmp_path / "fixture.wav"
    _write_wav(wav_path)
    monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav_path))

    recorder = AudioRecorder(AudioConfig())
    assert recorder._fake_wav_path is None
