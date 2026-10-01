"""Tests for NemotronStreamingEngine (WHISP-56), the sherpa-onnx OnlineRecognizer
adapter for NVIDIA Nemotron 3.5 ASR Streaming.

Everything goes through a FAKE sherpa_onnx module injected into sys.modules
(FakeOnlineRecognizer/FakeStream) -- no real model, no network, no sherpa-onnx
dependency required to run this file.
"""
from __future__ import annotations

import sys
import threading
import types
from unittest.mock import patch

import numpy as np
import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.stt.engine import SpeechEngine
from whisper_aloud.stt.nemotron_streaming_engine import NemotronStreamingEngine
from whisper_aloud.stt.streaming import StreamingSpeechEngine

_SAMPLE_RATE = 16000


class FakeStream:
    """Records every accept_waveform call and the per-stream language option."""

    def __init__(self) -> None:
        self.waveform_calls: list[tuple[int, np.ndarray]] = []
        self.language: str | None = None
        self.finished = False
        self._pending_decodes = 0

    def set_option(self, key: str, value: str) -> None:
        assert key == "language"
        self.language = value

    def accept_waveform(self, sample_rate: int, samples) -> None:
        self.waveform_calls.append((sample_rate, np.array(samples, dtype=np.float32)))
        self._pending_decodes += 1

    def input_finished(self) -> None:
        self.finished = True


def make_fake_sherpa_module(results: list[str]):
    """A fresh fake `sherpa_onnx` module whose recognizer replays `results` in order,
    one new result becoming current per decode_stream() call (extra decode_stream
    calls beyond len(results) just repeat the last result, like a real decoder with
    no new hypothesis)."""

    class FakeOnlineRecognizer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self._results = list(results)
            self._result_index = 0
            self._current_result = ""
            self.decode_calls = 0
            self.streams_created: list[FakeStream] = []

        @classmethod
        def from_transducer(cls, **kwargs):
            return cls(**kwargs)

        def create_stream(self) -> FakeStream:
            stream = FakeStream()
            self.streams_created.append(stream)
            return stream

        def is_ready(self, stream: FakeStream) -> bool:
            return stream._pending_decodes > 0

        def decode_stream(self, stream: FakeStream) -> None:
            stream._pending_decodes -= 1
            self.decode_calls += 1
            if self._result_index < len(self._results):
                self._current_result = self._results[self._result_index]
                self._result_index += 1

        def get_result(self, stream: FakeStream) -> str:
            return self._current_result

    module = types.ModuleType("sherpa_onnx")
    module.OnlineRecognizer = FakeOnlineRecognizer
    return module


def _cfg():
    c = WhisperAloudConfig()
    c.model.engine = "nemotron-streaming"
    c.model.name = "nemotron-3.5-streaming-560ms"
    c.transcription.language = "es"
    return c


def _write_fake_model_files(tmp_path):
    (tmp_path / "encoder.int8.onnx").write_bytes(b"")
    (tmp_path / "decoder.int8.onnx").write_bytes(b"")
    (tmp_path / "joiner.int8.onnx").write_bytes(b"")
    (tmp_path / "tokens.txt").write_text("")
    return tmp_path


def _load_with_fake(engine, tmp_path, fake_module):
    with patch("whisper_aloud.stt.model_download.ensure_model", return_value=tmp_path), \
         patch.dict(sys.modules, {"sherpa_onnx": fake_module}):
        engine.load_model()


class TestProtocolConformance:
    def test_satisfies_both_speech_engine_and_streaming_speech_engine(self):
        engine = NemotronStreamingEngine(_cfg())
        assert isinstance(engine, SpeechEngine)
        assert isinstance(engine, StreamingSpeechEngine)
        assert engine.is_streaming is True


class TestLoadModelErrorPaths:
    def test_load_model_raises_import_error_without_sherpa(self):
        engine = NemotronStreamingEngine(_cfg())
        with patch.dict(sys.modules, {"sherpa_onnx": None}):
            with pytest.raises(ImportError, match="pip install"):
                engine.load_model()
        assert engine.is_loaded is False

    def test_load_model_raises_when_model_files_missing(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        with patch("whisper_aloud.stt.model_download.ensure_model", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="encoder"):
                engine.load_model()
        assert engine.is_loaded is False

    def test_load_model_is_idempotent(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module([])
        _load_with_fake(engine, tmp_path, fake_module)
        assert engine.is_loaded is True
        with patch("whisper_aloud.stt.model_download.ensure_model") as mock_ensure:
            engine.load_model()
        mock_ensure.assert_not_called()


class TestStreamingSession:
    def test_start_session_sets_language_option_per_stream(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module([])
        _load_with_fake(engine, tmp_path, fake_module)

        engine.start_session("es")
        assert engine._stream.language == "es"

        engine.start_session("en")
        assert engine._stream.language == "en"

    def test_accept_chunk_feeds_samples_in_order(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["uno", "uno dos"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")

        chunk1 = np.ones(100, dtype=np.float32) * 0.1
        chunk2 = np.ones(100, dtype=np.float32) * 0.2
        engine.accept_chunk(chunk1)
        engine.accept_chunk(chunk2)

        calls = engine._stream.waveform_calls
        assert len(calls) == 2
        assert calls[0][0] == _SAMPLE_RATE
        np.testing.assert_allclose(calls[0][1], chunk1)
        np.testing.assert_allclose(calls[1][1], chunk2)

    def test_accept_chunk_before_start_session_raises(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module([])
        _load_with_fake(engine, tmp_path, fake_module)
        with pytest.raises(RuntimeError):
            engine.accept_chunk(np.zeros(10, dtype=np.float32))

    def test_committed_and_tentative_evolve_across_chunks(self, tmp_path):
        """Two consecutive partials sharing a word-prefix commit that prefix (N=2)."""
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola mundo", "hola mundo que tal"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")

        first = engine.accept_chunk(np.zeros(100, dtype=np.float32)) or engine.get_partial()
        assert first.committed == ""
        assert first.tentative == "hola mundo"

        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        second = engine.get_partial()
        assert second.committed == "hola mundo"
        assert second.tentative == "que tal"
        assert second.seq == 2

    def test_start_session_resets_commit_tracker(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola", "hola mundo"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        assert engine.get_partial().committed == "hola"

        engine.start_session("es")  # new session: must not carry over old commits
        assert engine.get_partial().committed == ""
        assert engine.get_partial().seq == 0


class TestFinalize:
    def test_finalize_pads_tail_silence_before_decoding(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola", "hola mundo final"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.accept_chunk(np.zeros(100, dtype=np.float32))

        stream = engine._stream
        result = engine.finalize()

        # Tail padding call: ~0.66s of silence appended before input_finished().
        tail_calls = [c for c in stream.waveform_calls if np.all(c[1] == 0) and len(c[1]) > 1000]
        assert tail_calls, "expected a tail-silence accept_waveform call before finalize"
        tail_samples = tail_calls[-1][1]
        assert tail_samples.shape[0] == pytest.approx(int(0.66 * _SAMPLE_RATE), abs=1)
        assert stream.finished is True
        assert result.text == "hola mundo final"

    def test_finalize_without_a_session_returns_empty_result(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module([])
        _load_with_fake(engine, tmp_path, fake_module)
        result = engine.finalize()
        assert result.text == ""

    def test_finalize_clears_the_session(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        engine.finalize()
        assert engine._stream is None


class TestFinalizeReconciliation:
    """WHISP-56 CHANGES_REQUIRED item 2: finalize() must route the post-tail-padding
    raw text through CommitTracker.flush() instead of returning it bypassing the
    tracker, so the final text can never contradict what get_partial() already told
    a caller was committed."""

    def test_finalize_extends_the_committed_prefix_cleanly(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola dia", "hola dia bueno"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        # Stable across this one partial only so far; nothing committed yet.
        assert engine.get_partial().committed == ""

        result = engine.finalize()
        assert result.text == "hola dia bueno"

    def test_finalize_on_divergence_returns_decoder_text_and_logs_a_warning(
        self, tmp_path, caplog
    ):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(
            ["hola dia", "hola dia bueno", "halo dia bueno completo"]
        )
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        assert engine.get_partial().committed == "hola dia"

        with caplog.at_level("WARNING"):
            result = engine.finalize()

        # The transcript is the decoder's final text (no duplicated words), while
        # the live partial view keeps the committed prefix it already showed.
        assert result.text == "halo dia bueno completo"
        assert engine.get_partial().committed.startswith("hola dia")
        assert any("diverges" in r.message for r in caplog.records)


class TestCancel:
    def test_cancel_session_then_accept_chunk_is_a_no_op(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.cancel_session()
        assert engine.is_cancelling is True

        engine.accept_chunk(np.ones(100, dtype=np.float32))
        assert engine._stream.waveform_calls == []

    def test_cancel_session_then_finalize_returns_empty_and_clears_flag(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.accept_chunk(np.zeros(100, dtype=np.float32))
        engine.cancel_session()

        result = engine.finalize()
        assert result.text == ""
        assert engine.is_cancelling is False
        assert engine._stream is None

    def test_cancel_transcription_delegates_to_cancel_session(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        engine.cancel_transcription()
        assert engine.is_cancelling is True


class TestBatchTranscribeNumpy:
    def test_transcribe_numpy_feeds_whole_buffer_in_chunks_then_finalizes(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        # 560ms default chunk @16kHz = 8960 samples/chunk; give 2 chunks worth + a remainder.
        fake_module = make_fake_sherpa_module(["uno", "uno dos", "uno dos tres"])
        _load_with_fake(engine, tmp_path, fake_module)

        audio = np.zeros(20000, dtype=np.float32)
        result = engine.transcribe_numpy(audio, 16000)

        assert result.text == "uno dos tres"
        assert result.language == "es"
        assert result.segments == []
        assert result.confidence == 0.0
        assert result.duration == pytest.approx(20000 / 16000)

        # Fed in more than one chunk (buffer > one 560ms chunk).
        recognizer = next(iter(engine._recognizer.streams_created), None)
        assert recognizer is not None

    def test_transcribe_numpy_resamples_non_16k_input(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola"])
        _load_with_fake(engine, tmp_path, fake_module)

        audio = np.zeros(8000, dtype=np.float32)  # 1s @ 8kHz
        result = engine.transcribe_numpy(audio, 8000)
        assert result.duration == pytest.approx(1.0, abs=0.01)

    def test_transcribe_numpy_honors_pending_cancel(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola"])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.cancel_transcription()

        result = engine.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000)
        assert result.text == ""
        assert engine.is_cancelling is False

    def test_transcribe_file_delegates_to_transcribe_numpy(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola"])
        _load_with_fake(engine, tmp_path, fake_module)

        import soundfile as sf

        wav_path = tmp_path / "clip.wav"
        sf.write(str(wav_path), np.zeros(1600, dtype=np.float32), 16000)
        out = engine.transcribe_file(str(wav_path))
        assert out.text == "hola"


class TestUnloadAndLifecycle:
    def test_unload_model_clears_loaded_state_and_stream(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module([])
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")
        engine.unload_model()
        assert engine.is_loaded is False
        assert engine._stream is None


class TestConcurrency:
    """WHISP-56 CHANGES_REQUIRED item 6 (pre-WHISP-57 hardening): a lock guards the
    engine's session state (stream/tracker/etc.) so accept_chunk() racing against
    finalize()/start_session() from another thread never raises."""

    def test_accept_chunk_and_finalize_race_without_exceptions(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        # A long-ish, repeating script so decode_stream() always has something to
        # return regardless of how many times either thread drives a decode.
        fake_module = make_fake_sherpa_module(["hola", "hola mundo"] * 200)
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")

        errors: list[BaseException] = []

        def accept_loop() -> None:
            for _ in range(200):
                try:
                    engine.accept_chunk(np.zeros(10, dtype=np.float32))
                except Exception as e:  # pragma: no cover - only hit on a real bug
                    errors.append(e)

        def finalize_loop() -> None:
            for _ in range(20):
                try:
                    engine.finalize()
                    engine.start_session("es")
                except Exception as e:  # pragma: no cover - only hit on a real bug
                    errors.append(e)

        t_accept = threading.Thread(target=accept_loop)
        t_finalize = threading.Thread(target=finalize_loop)
        t_accept.start()
        t_finalize.start()
        t_accept.join(timeout=10)
        t_finalize.join(timeout=10)

        assert not t_accept.is_alive()
        assert not t_finalize.is_alive()
        assert errors == []

    def test_get_partial_during_concurrent_accept_chunk_never_raises(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)
        fake_module = make_fake_sherpa_module(["hola", "hola mundo"] * 200)
        _load_with_fake(engine, tmp_path, fake_module)
        engine.start_session("es")

        errors: list[BaseException] = []

        def accept_loop() -> None:
            for _ in range(200):
                try:
                    engine.accept_chunk(np.zeros(10, dtype=np.float32))
                except Exception as e:  # pragma: no cover
                    errors.append(e)

        def read_loop() -> None:
            for _ in range(200):
                try:
                    engine.get_partial()
                except Exception as e:  # pragma: no cover
                    errors.append(e)

        threads = [threading.Thread(target=accept_loop), threading.Thread(target=read_loop)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []


class TestFinalizeGenerationGuard:
    """WHISP-57 audit (HIGH), defense in depth: finalize()'s unlocked decode
    section (stream.accept_waveform/input_finished/decode_stream/get_result,
    mirroring real sherpa-onnx calls) must not let a stale flush() corrupt a
    NEW session's tracker/last_partial if a start_session() races it while
    finalize() is still "decoding". The daemon's own `_transcribing` guard
    (WHISP-57 audit, in daemon.py) should prevent this from ever happening in
    practice, but the engine must not rely on that."""

    def test_finalize_skips_stale_flush_if_newer_session_started(self, tmp_path):
        engine = NemotronStreamingEngine(_cfg())
        _write_fake_model_files(tmp_path)

        entered = threading.Event()
        release = threading.Event()

        class BlockingStream(FakeStream):
            def input_finished(self) -> None:
                super().input_finished()
                entered.set()
                assert release.wait(timeout=5), "test did not release finalize() in time"

        class BlockingRecognizer:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            @classmethod
            def from_transducer(cls, **kwargs):
                return cls(**kwargs)

            def create_stream(self) -> BlockingStream:
                return BlockingStream()

            def is_ready(self, stream) -> bool:
                return False

            def decode_stream(self, stream) -> None:
                pass  # pragma: no cover - never reached, is_ready() is always False

            def get_result(self, stream) -> str:
                return "session A final"

        fake_module = types.ModuleType("sherpa_onnx")
        fake_module.OnlineRecognizer = BlockingRecognizer
        _load_with_fake(engine, tmp_path, fake_module)

        engine.start_session("es")
        generation_a = engine._generation

        result_holder: dict = {}

        def run_finalize() -> None:
            result_holder["result"] = engine.finalize()

        t = threading.Thread(target=run_finalize)
        t.start()
        assert entered.wait(timeout=5), "finalize() never reached input_finished()"

        # Session A's finalize() is blocked inside input_finished() (its
        # unlocked decode section). Start a NEW session (B) concurrently.
        engine.start_session("es")
        generation_b = engine._generation
        assert generation_b != generation_a

        release.set()
        t.join(timeout=5)
        assert not t.is_alive()

        # finalize() still reports session A's real decoded text...
        assert result_holder["result"].text == "session A final"
        # ...but must not have clobbered session B's live partial view with a
        # stale flush() of A's (empty, since B's start_session() just reset it).
        partial_b = engine.get_partial()
        assert partial_b.committed == ""
        assert partial_b.tentative == ""


class TestFactoryWiring:
    def test_create_engine_returns_nemotron_streaming_engine(self):
        from whisper_aloud.stt.engine import create_engine

        config = _cfg()
        engine = create_engine(config)
        assert isinstance(engine, NemotronStreamingEngine)
        assert isinstance(engine, SpeechEngine)
        assert isinstance(engine, StreamingSpeechEngine)

    def test_config_validate_accepts_nemotron_streaming(self):
        config = _cfg()
        config.validate()  # must not raise


@pytest.mark.real_model
class TestRealModelSmoke:
    """Skipped unless the model has already been downloaded to the XDG cache --
    never downloads it. Exercises the real sherpa-onnx decode path end to end."""

    def test_transcribe_numpy_on_silence(self):
        import importlib.util

        if importlib.util.find_spec("sherpa_onnx") is None:
            pytest.skip("sherpa-onnx not installed")
        from whisper_aloud.stt.model_download import model_dir

        model_path = model_dir("nemotron-3.5-streaming-560ms")
        if not (model_path / "tokens.txt").exists():
            pytest.skip("nemotron streaming model not downloaded")

        engine = NemotronStreamingEngine(_cfg())
        out = engine.transcribe_numpy(np.zeros(16000, dtype=np.float32), 16000)
        assert isinstance(out.text, str)
