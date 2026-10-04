"""Daemon model-readiness state machine and startup preload (0.3.0 design fix).

Field log (Flatpak): the daemon deferred model loading to the first
transcription, so the user waited ~9 s after every daemon start, and a
streaming StartRecording loaded the model synchronously on the main loop.
These tests pin the new contract:

- run() kicks a background preload once the object is registered;
- the state machine goes loading -> ready / missing / error, observable via
  GetStatus and the ModelStateChanged signal;
- a batch transcription submitted while loading waits for the ready EVENT
  (no polling) and then runs; missing/error fail with a clear Error;
- a streaming StartRecording never loads on the calling (main-loop) thread;
- model-switching config changes go through the same background path.

All waits are Event/join based with explicit timeouts -- never a bare sleep.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests._daemon_harness import make_daemon
from whisper_aloud.service import model_state as ms


def _available(daemon_obj, value: bool):
    """Patch the offline availability check on the module object the daemon
    under test actually uses (the harness re-imports the daemon module)."""
    return patch.object(
        daemon_obj._daemon_module.model_state, "is_model_available", return_value=value
    )


@pytest.fixture
def daemon():
    service = make_daemon()
    yield service
    service._model_state.close()
    service._patches.close()


def _result(text="hello"):
    result = MagicMock()
    result.text = text
    result.confidence = 0.9
    result.duration = 1.0
    result.language = "en"
    return result


class _GatedEngine:
    """Batch engine whose load_model() blocks on `gate` and records the
    thread it ran on."""

    is_streaming = False

    def __init__(self, gate: threading.Event | None = None, error: Exception | None = None):
        self.gate = gate
        self.error = error
        self.load_entered = threading.Event()
        self.load_threads: list = []
        self.transcribe_numpy = MagicMock(return_value=_result("from engine"))
        self.unload_model = MagicMock()
        self.cancel_transcription = MagicMock()

    def load_model(self):
        self.load_threads.append(threading.current_thread())
        self.load_entered.set()
        if self.gate is not None and not self.gate.wait(timeout=10):
            raise TimeoutError("test gate never released")
        if self.error is not None:
            raise self.error


class _StreamingEngine(_GatedEngine):
    is_streaming = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.start_session = MagicMock()
        self.accept_chunk = MagicMock()
        self.cancel_session = MagicMock()


def _join_loader(daemon_obj, timeout=10.0):
    thread = daemon_obj._model_load_thread
    assert thread is not None, "no background model load was started"
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "background model load did not finish"


def _state_signals(daemon_obj):
    return [c.args for c in daemon_obj.ModelStateChanged.call_args_list]


class TestStartupPreload:
    def test_run_kicks_preload_after_registering_object(self):
        daemon_obj = make_daemon()
        try:
            order = []
            fake_bus = MagicMock()
            fake_bus.register_object.side_effect = lambda *a, **k: order.append("register")
            daemon_obj._bus = fake_bus
            daemon_obj._name_owner = MagicMock()
            with patch.object(
                daemon_obj, "_start_model_load", side_effect=lambda *a, **k: order.append("preload")
            ):
                daemon_obj.run()
            assert order == ["register", "preload"]
        finally:
            daemon_obj._patches.close()

    def test_cached_model_goes_loading_then_ready_without_blocking_caller(self, daemon):
        gate = threading.Event()
        engine = _GatedEngine(gate=gate)
        daemon.transcriber = engine

        with _available(daemon, True):
            daemon._start_model_load()  # must return while load_model is still blocked
            assert engine.load_entered.wait(timeout=10)
            assert daemon._model_state.state == ms.LOADING
            assert daemon.GetStatus()["model_state"] == "loading"
            gate.set()
            _join_loader(daemon)

        assert daemon._model_state.state == ms.READY
        assert engine.load_threads[0] is not threading.current_thread()
        assert _state_signals(daemon) == [("loading", ""), ("ready", "")]
        status = daemon.GetStatus()
        assert status["model_state"] == "ready"
        assert status["model_error"] == ""

    def test_missing_model_is_reported_and_never_loaded(self, daemon):
        engine = _GatedEngine()
        daemon.transcriber = engine

        with _available(daemon, False):
            daemon._start_model_load()
            _join_loader(daemon)

        assert engine.load_threads == []
        state, detail = daemon._model_state.snapshot()
        assert state == ms.MISSING
        assert daemon.config.model.name in detail
        assert daemon.GetStatus()["model_error"] == detail
        assert _state_signals(daemon)[-1] == ("missing", detail)
        daemon.Error.assert_not_called()

    def test_load_failure_reports_error_state_and_signal(self, daemon):
        daemon.transcriber = _GatedEngine(error=RuntimeError("CUDA exploded"))

        with _available(daemon, True):
            daemon._start_model_load()
            _join_loader(daemon)

        assert daemon._model_state.snapshot() == (ms.ERROR, "CUDA exploded")
        assert daemon.GetStatus()["model_error"] == "CUDA exploded"
        assert _state_signals(daemon)[-1] == ("error", "CUDA exploded")
        daemon.Error.assert_called_once_with("model_load_failed", "CUDA exploded")


class TestBatchJobWaitsForReady:
    def _arm(self, daemon_obj, engine):
        daemon_obj.transcriber = engine
        daemon_obj.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-wa")
        daemon_obj.history_manager.add_transcription.return_value = 1
        daemon_obj.recorder.is_recording = False
        daemon_obj.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)

    def test_job_submitted_while_loading_waits_for_ready_event_then_transcribes(self, daemon):
        gate = threading.Event()
        engine = _GatedEngine(gate=gate)
        self._arm(daemon, engine)

        waiting = threading.Event()
        real_wait = daemon._model_state.wait_while_loading

        def _spy_wait(*a, **kw):
            waiting.set()
            return real_wait(*a, **kw)

        with _available(daemon, True):
            daemon._start_model_load()
            assert engine.load_entered.wait(timeout=10)

            # Recording is allowed while the model is still loading.
            assert daemon.StartRecording() is True
            daemon.recorder.is_recording = True
            with patch.object(daemon._model_state, "wait_while_loading", side_effect=_spy_wait):
                assert daemon.StopRecording() == "transcribing"
                daemon.recorder.is_recording = False
                assert waiting.wait(timeout=10), "job never blocked on the ready event"
                engine.transcribe_numpy.assert_not_called()

                gate.set()  # model finishes loading -> job proceeds
                _join_loader(daemon)
                daemon.executor.shutdown(wait=True)

        engine.transcribe_numpy.assert_called_once()
        daemon.TranscriptionReady.assert_called_once()
        assert daemon.TranscriptionReady.call_args[0][0] == "from engine"
        daemon.Error.assert_not_called()

    def test_start_recording_rejects_all_engines_when_model_missing(self, daemon):
        """1.6.1: a `missing` model now refuses the start up front for every
        engine (batch included), instead of letting the user record into a
        job that is guaranteed to fail afterwards."""
        engine = _GatedEngine()
        self._arm(daemon, engine)
        daemon._model_state.set_state(ms.MISSING, "Model 'base' is not downloaded")

        with _available(daemon, False):
            # Rejected immediately; the refusal still re-checks availability
            # in the background (the model may have been downloaded
            # out-of-band), so it settles back to `missing` here.
            assert daemon.StartRecording() is False
            _join_loader(daemon)

        daemon.recorder.start.assert_not_called()
        engine.transcribe_numpy.assert_not_called()
        daemon.TranscriptionReady.assert_not_called()
        code, message = daemon.Error.call_args[0]
        assert code == "model_not_available"
        assert message.startswith("Model not available")
        assert "base" in message
        daemon.notifications.show_error.assert_called_once_with(message)
        assert daemon._transcribing is False
        assert daemon._model_state.state == ms.MISSING

    def test_start_recording_rejects_all_engines_when_model_errored(self, daemon):
        engine = _GatedEngine()
        self._arm(daemon, engine)
        daemon._model_state.set_state(ms.ERROR, "CUDA exploded")

        with _available(daemon, True):
            assert daemon.StartRecording() is False
            _join_loader(daemon)

        daemon.recorder.start.assert_not_called()
        code, message = daemon.Error.call_args[0]
        assert code == "model_not_available"
        assert "CUDA exploded" in message

    def test_start_recording_rechecks_missing_model_and_succeeds_once_ready(self, daemon):
        engine = _GatedEngine()
        self._arm(daemon, engine)
        daemon._model_state.set_state(ms.MISSING, "gone")

        with _available(daemon, True):  # downloaded out-of-band meanwhile
            # The first attempt still observes the stale `missing` snapshot
            # and is refused, but that refusal is what kicks the recheck.
            assert daemon.StartRecording() is False
            _join_loader(daemon)
            assert daemon._model_state.state == ms.READY

            # The next attempt sees `ready` and proceeds normally.
            assert daemon.StartRecording() is True
            daemon.recorder.is_recording = True
            assert daemon.StopRecording() == "transcribing"
            daemon.recorder.is_recording = False
            daemon.executor.shutdown(wait=True)

        daemon.TranscriptionReady.assert_called_once()
        assert daemon._model_state.state == ms.READY

    def test_quit_wakes_a_job_waiting_for_the_model(self, daemon):
        gate = threading.Event()
        engine = _GatedEngine(gate=gate)
        self._arm(daemon, engine)
        daemon._loop = MagicMock()

        with _available(daemon, True):
            daemon._start_model_load()
            assert engine.load_entered.wait(timeout=10)
            assert daemon.StartRecording() is True
            daemon.recorder.is_recording = True
            assert daemon.StopRecording() == "transcribing"
            daemon.recorder.is_recording = False

            daemon.Quit()
            daemon.executor.shutdown(wait=True)  # returns only because Quit woke the job
            gate.set()
            _join_loader(daemon)

        engine.transcribe_numpy.assert_not_called()
        daemon.TranscriptionReady.assert_not_called()


class TestStreamingNeverLoadsOnMainThread:
    def test_streaming_start_refused_while_not_ready_and_loads_in_background(self, daemon):
        engine = _StreamingEngine()
        daemon.transcriber = engine
        daemon.recorder.is_recording = False
        assert daemon._model_state.state == ms.UNLOADED

        with _available(daemon, True):
            assert daemon.StartRecording() is False
            _join_loader(daemon)

        engine.start_session.assert_not_called()
        daemon.recorder.start.assert_not_called()
        code, message = daemon.Error.call_args[0]
        assert code == "model_not_ready"
        assert message.startswith("Model not available")
        assert engine.load_threads, "a background load should have been started"
        assert all(t is not threading.current_thread() for t in engine.load_threads)
        assert daemon._model_state.state == ms.READY

        # Once ready, the next StartRecording proceeds normally.
        assert daemon.StartRecording() is True
        engine.start_session.assert_called_once()

    def test_streaming_start_rejected_as_model_not_available_when_missing(self, daemon):
        """1.6.1: `missing`/`error` is refused the same way for streaming and
        batch engines alike -- `model_not_available`, not the
        loading/downloading-only `model_not_ready`."""
        engine = _StreamingEngine()
        daemon.transcriber = engine
        daemon.recorder.is_recording = False
        daemon._model_state.set_state(ms.MISSING, "gone")

        with _available(daemon, False):
            assert daemon.StartRecording() is False
            _join_loader(daemon)

        engine.start_session.assert_not_called()
        code, message = daemon.Error.call_args[0]
        assert code == "model_not_available"
        assert daemon._model_state.state == ms.MISSING

    def test_streaming_start_while_loading_does_not_start_a_second_load(self, daemon):
        gate = threading.Event()
        engine = _StreamingEngine(gate=gate)
        daemon.transcriber = engine

        with _available(daemon, True):
            daemon._start_model_load()
            first = daemon._model_load_thread
            assert engine.load_entered.wait(timeout=10)
            assert daemon.StartRecording() is False
            assert daemon._model_load_thread is first
            gate.set()
            _join_loader(daemon)

        assert len(engine.load_threads) == 1


class TestConfigChangeReload:
    def test_idle_model_change_reloads_in_background(self, daemon):
        daemon.recorder.is_recording = False
        daemon._model_state.set_state(ms.READY)
        gate = threading.Event()
        new_engine = _GatedEngine(gate=gate)

        with _available(daemon, True), patch.object(
            daemon._daemon_module, "create_engine", return_value=new_engine
        ):
            assert daemon.SetConfig({"model.name": "small"}) is True  # returns immediately
            assert new_engine.load_entered.wait(timeout=10)
            assert daemon.transcriber is new_engine
            assert daemon._model_state.state == ms.LOADING
            gate.set()
            _join_loader(daemon)

        assert daemon._model_state.state == ms.READY
        assert new_engine.load_threads[0] is not threading.current_thread()
        assert ("loading", "") in _state_signals(daemon)

    def test_superseded_load_never_reports_ready_for_the_old_engine(self, daemon):
        daemon.recorder.is_recording = False
        old_gate = threading.Event()
        old_engine = _GatedEngine(gate=old_gate)
        daemon.transcriber = old_engine
        new_gate = threading.Event()
        new_engine = _GatedEngine(gate=new_gate)

        with _available(daemon, True):
            daemon._start_model_load()
            old_thread = daemon._model_load_thread
            assert old_engine.load_entered.wait(timeout=10)
            with patch.object(daemon._daemon_module, "create_engine", return_value=new_engine):
                assert daemon.SetConfig({"model.name": "small"}) is True

            old_gate.set()
            old_thread.join(timeout=10)
            assert not old_thread.is_alive()
            assert daemon._model_state.state == ms.LOADING  # old outcome dropped
            old_engine.unload_model.assert_called_once()

            new_gate.set()
            _join_loader(daemon)

        assert daemon._model_state.state == ms.READY
        assert daemon.transcriber is new_engine


class TestDownloadDrivesState:
    def test_downloading_configured_model_then_loads_it(self, daemon, monkeypatch):
        from whisper_aloud.stt import hf_download

        engine = _GatedEngine()
        daemon.transcriber = engine
        daemon._model_state.set_state(ms.MISSING, "not downloaded")
        release = threading.Event()

        def _fake_download(name, progress_cb=None, cancel_event=None):
            assert release.wait(timeout=10)
            return "/fake"

        monkeypatch.setattr(hf_download, "download_model", _fake_download)
        name = daemon.config.model.name

        with _available(daemon, True):
            assert daemon.DownloadModel("faster-whisper", name, False) is True
            assert daemon._model_state.snapshot() == (ms.DOWNLOADING, f"faster-whisper/{name}")
            release.set()
            daemon._download_thread.join(timeout=10)
            _join_loader(daemon)

        assert daemon._model_state.state == ms.READY
        daemon.DownloadFinished.assert_called_once_with("faster-whisper", name, True, "")

    def test_downloading_other_model_leaves_state_alone(self, daemon, monkeypatch):
        from whisper_aloud.stt import hf_download

        daemon._model_state.set_state(ms.READY)
        monkeypatch.setattr(hf_download, "download_model", lambda *a, **k: "/fake")
        other = "tiny" if daemon.config.model.name != "tiny" else "small"

        assert daemon.DownloadModel("faster-whisper", other, False) is True
        daemon._download_thread.join(timeout=10)

        assert daemon._model_state.state == ms.READY
        assert daemon._model_load_thread is None
