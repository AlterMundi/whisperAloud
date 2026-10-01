"""Unit tests for the deterministic FakeSpeechEngine test double (WHISP-46)."""
import time

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.exceptions import ConfigurationError, TranscriptionError
from whisper_aloud.stt.fake_engine import FakeSpeechEngine, FakeStreamingSpeechEngine


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


# ─── FakeStreamingSpeechEngine (WHISP-57) ───────────────────────────────────


def _streaming_config():
    c = WhisperAloudConfig()
    c.model.engine = "fake"
    c.model.name = "fake-streaming"
    return c


def _start(engine, monkeypatch, tmp_path, text="the quick brown fox", name="s.wav"):
    wav = tmp_path / name
    wav.write_bytes(b"")
    wav.with_suffix(".txt").write_text(text)
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
    engine.start_session("en")
    return wav


class TestFakeStreamingSpeechEngine:
    def test_is_streaming_flag(self):
        assert FakeStreamingSpeechEngine.is_streaming is True

    def test_accept_chunk_before_start_session_raises(self):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        with pytest.raises(RuntimeError, match="start_session"):
            engine.accept_chunk(np.zeros(160, dtype=np.float32))

    def test_start_session_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", raising=False)
        engine = FakeStreamingSpeechEngine(_streaming_config())
        with pytest.raises(ConfigurationError, match="WHISPER_ALOUD_FAKE_AUDIO_WAV"):
            engine.start_session("en")

    def test_start_session_missing_sidecar_raises(self, monkeypatch, tmp_path):
        wav = tmp_path / "nosidecar.wav"
        wav.write_bytes(b"")
        monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
        engine = FakeStreamingSpeechEngine(_streaming_config())
        with pytest.raises(ConfigurationError, match="sidecar"):
            engine.start_session("en")

    def test_partial_grows_one_word_per_chunk_and_seq_increments(self, monkeypatch, tmp_path):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="the quick brown fox")

        seen = []
        for _ in range(4):
            engine.accept_chunk(np.zeros(160, dtype=np.float32))
            seen.append(engine.get_partial())

        assert [p.seq for p in seen] == [1, 2, 3, 4]
        assert seen[0].committed == "" and seen[0].tentative == "the"
        assert seen[1].committed == "the" and seen[1].tentative == "quick"
        assert seen[2].committed == "the quick" and seen[2].tentative == "brown"
        assert seen[3].committed == "the quick brown" and seen[3].tentative == "fox"

    def test_seq_keeps_incrementing_past_word_count(self, monkeypatch, tmp_path):
        """More chunks than words: revealed caps at the word count but seq
        (and therefore PartialTranscript emission upstream) keeps advancing,
        matching CommitTracker's "one update, one seq" contract."""
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="hi")

        for _ in range(3):
            engine.accept_chunk(np.zeros(160, dtype=np.float32))
        partial = engine.get_partial()
        assert partial.seq == 3
        assert partial.committed == "" and partial.tentative == "hi"

    def test_finalize_returns_full_sidecar_text(self, monkeypatch, tmp_path):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="hello world")
        engine.accept_chunk(np.zeros(160, dtype=np.float32))
        engine.accept_chunk(np.zeros(160, dtype=np.float32))

        result = engine.finalize()
        assert result.text == "hello world"

    def test_cancel_session_then_finalize_returns_empty_text(self, monkeypatch, tmp_path):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="hello world")
        engine.accept_chunk(np.zeros(160, dtype=np.float32))
        engine.cancel_session()
        assert engine.is_cancelling is True

        result = engine.finalize()
        assert result.text == ""

    def test_accept_chunk_after_cancel_is_a_noop(self, monkeypatch, tmp_path):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="hello world")
        engine.cancel_session()
        before = engine.get_partial()
        engine.accept_chunk(np.zeros(160, dtype=np.float32))
        after = engine.get_partial()
        assert after.seq == before.seq
        assert after.committed == "" and after.tentative == ""

    def test_second_session_resets_state(self, monkeypatch, tmp_path):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="one two", name="first.wav")
        engine.accept_chunk(np.zeros(160, dtype=np.float32))
        engine.accept_chunk(np.zeros(160, dtype=np.float32))
        engine.finalize()

        _start(engine, monkeypatch, tmp_path, text="three four five", name="second.wav")
        partial = engine.get_partial()
        assert partial.seq == 0
        assert partial.committed == "" and partial.tentative == ""

    def test_cancel_transcription_alias_calls_cancel_session(self, monkeypatch, tmp_path):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="hi")
        engine.cancel_transcription()
        assert engine.is_cancelling is True

    def test_load_unload_model_flags(self):
        engine = FakeStreamingSpeechEngine(_streaming_config())
        assert engine.is_loaded is False
        engine.load_model()
        assert engine.is_loaded is True
        engine.unload_model()
        assert engine.is_loaded is False
