"""Unit tests for the deterministic FakeSpeechEngine test double (WHISP-46)."""
import threading
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


class _EnteredGate:
    """A finalize_gate stand-in that also reports when `.wait()` was entered,
    so a test can deterministically wait for finalize() to reach its unlocked
    section before racing a concurrent start_session() against it -- no
    sleeps needed beyond waiting on these two events."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self._release = threading.Event()

    def wait(self, timeout=None):
        self.entered.set()
        return self._release.wait(timeout=timeout)

    def set(self) -> None:
        self._release.set()


class TestFinalizeGenerationGuard:
    """WHISP-57 audit (HIGH), defense in depth: finalize()'s unlocked section
    (where `finalize_gate`/latency simulate real decode work happening
    outside the lock, mirroring NemotronStreamingEngine) must not let a stale
    finalize() corrupt a NEWER session's `_session_started` state if a
    start_session() races it. The daemon's own `_transcribing` guard
    (WHISP-57 audit, in daemon.py) should prevent this from ever happening in
    practice, but the engine must not rely on that."""

    def test_finalize_does_not_reset_newer_sessions_started_flag(self, monkeypatch, tmp_path):
        gate = _EnteredGate()
        engine = FakeStreamingSpeechEngine(_streaming_config(), finalize_gate=gate)
        _start(engine, monkeypatch, tmp_path, text="session a text", name="a.wav")
        engine.accept_chunk(np.zeros(160, dtype=np.float32))
        generation_a = engine._generation

        result_holder: dict = {}

        def run_finalize() -> None:
            result_holder["result"] = engine.finalize()

        t = threading.Thread(target=run_finalize)
        t.start()
        assert gate.entered.wait(timeout=5), "finalize() never reached its unlocked section"

        # Session A's finalize() is blocked in its unlocked section. Start a
        # NEW session (B) concurrently.
        _start(engine, monkeypatch, tmp_path, text="session b text", name="b.wav")
        generation_b = engine._generation
        assert generation_b != generation_a
        assert engine._session_started is True  # B's start_session() just set this

        gate.set()
        t.join(timeout=5)
        assert not t.is_alive()

        # finalize() still reports session A's real text...
        assert result_holder["result"].text == "session a text"
        # ...but must not have reset session B's _session_started back to
        # False (what finalize() unconditionally did before the generation
        # guard), which would otherwise make B's next accept_chunk() look
        # like it arrived before any start_session() ever ran.
        assert engine._session_started is True

    def test_finalize_without_gate_still_guards_on_generation(self, monkeypatch, tmp_path):
        """Sanity check: a finalize() call that completes normally (no
        concurrent session) still resets _session_started as before -- the
        generation guard only skips the mutation when a NEWER session exists,
        it isn't a no-op by default."""
        engine = FakeStreamingSpeechEngine(_streaming_config())
        _start(engine, monkeypatch, tmp_path, text="solo")
        engine.accept_chunk(np.zeros(160, dtype=np.float32))

        engine.finalize()

        assert engine._session_started is False


# ─── 0.3.0 model preload: controllable load_model() for e2e ─────────────────


@pytest.mark.parametrize("engine_cls", [FakeSpeechEngine, FakeStreamingSpeechEngine])
def test_load_model_blocks_on_fifo_until_released(monkeypatch, tmp_path, engine_cls):
    """WHISPER_ALOUD_FAKE_MODEL_LOAD_FIFO: load_model() blocks until a writer
    releases the FIFO -- an event (open/write), not a sleep, so e2e tests can
    hold the daemon in `loading` for exactly as long as they need."""
    import os

    fifo = tmp_path / "load.fifo"
    os.mkfifo(fifo)
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_MODEL_LOAD_FIFO", str(fifo))
    engine = engine_cls(_config())

    t = threading.Thread(target=engine.load_model, daemon=True)
    t.start()
    with open(fifo, "wb") as writer:  # rendezvous: returns once the loader opened it
        assert not engine.is_loaded
        writer.write(b"x")
    t.join(timeout=10)
    assert not t.is_alive()
    assert engine.is_loaded


@pytest.mark.parametrize("engine_cls", [FakeSpeechEngine, FakeStreamingSpeechEngine])
def test_load_model_error_injection(monkeypatch, engine_cls):
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_MODEL_LOAD_ERROR", "simulated load failure")
    engine = engine_cls(_config())
    with pytest.raises(Exception, match="simulated load failure"):
        engine.load_model()
    assert not engine.is_loaded
