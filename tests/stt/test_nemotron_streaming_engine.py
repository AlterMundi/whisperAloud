"""Tests for NemotronStreamingEngine (WHISP-56), the sherpa-onnx OnlineRecognizer
adapter for NVIDIA Nemotron 3.5 ASR Streaming.

Everything goes through a FAKE sherpa_onnx module injected into sys.modules
(FakeOnlineRecognizer/FakeStream) -- no real model, no network, no sherpa-onnx
dependency required to run this file.
"""
from __future__ import annotations

import sys
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
