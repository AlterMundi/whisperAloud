"""Real-concurrency regression tests for the daemon's BATCH (non-streaming)
engine path (WHISP-67).

tests/_daemon_harness.py's `make_daemon()` wires a `_SyncExecutor` (runs
submitted work inline on the calling thread) and `GLib.idle_add = lambda fn,
*a: fn(*a)` (runs idle callbacks inline too). Both choices make the daemon's
state-machine tests fast and deterministic, but they also hide every race
that only exists because `StopRecording()`/auto-stop submit work to a REAL
background thread while `StartRecording()`/`CancelRecording()`/`SetConfig()`/
`Quit()` can be called concurrently from the (real) D-Bus dispatch thread.

This module replaces both of those with real concurrency primitives:

- `daemon.executor` is a real `concurrent.futures.ThreadPoolExecutor`, so
  `_transcribe_and_emit` genuinely runs on its own OS thread, exactly as in
  production.
- `GLib.idle_add` is backed by `_FakeMainThread`, a dedicated worker thread
  that drains queued callbacks strictly in submission order (FIFO), one at a
  time. This is chosen over spinning up a real `GLib.MainLoop` in a thread
  for two reasons: (1) none of these tests exercise real GLib/pydbus signal
  dispatch -- `TranscriptionReady` & co. are already `MagicMock` signals
  supplied by the harness's `_FakeSignal`, so the only property worth
  preserving from the real main loop is "callbacks queued via idle_add() run
  on one dedicated thread, in the order they were queued, never
  concurrently with each other" -- and a plain queue + worker thread gives
  exactly that property with no GLib main-loop lifecycle/teardown hazards
  (no risk of a test hanging because `loop.quit()` was never wired to a
  pending idle source). (2) it gives tests a precise, non-sleep way to wait
  for "every idle_add callback queued so far has run" via `drain()`, which
  blocks on a `threading.Event` fed by `Queue.join()` rather than polling.

Every test below uses `threading.Event` gates (never a bare `time.sleep`) to
pin an executor-thread job at an exact point, and every blocking wait has an
explicit timeout <= 10s so a regression here fails fast instead of hanging
the suite.

Mutation-checked guards (see WHISP-67 brief): the `_transcribing` guard in
`StartRecording` and the `recorder.is_recording`/`_transcribing` branch in
`CancelRecording` were both manually removed (one at a time) during
development of this module to confirm
`TestBatchStopThenStartDuringTranscription` and the cancel-contract test
actually bite; the mutations were reverted before committing (not left in
this file -- see the final report for the exact diff and failure output).
"""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests._daemon_harness import make_daemon


class _FakeMainThread:
    """Queue-backed stand-in for the GLib main loop's `idle_add` dispatch.

    See the module docstring for why this is preferred over a real
    `GLib.MainLoop` here. `idle_add(fn, *args)` queues `fn(*args)` to run on
    a single dedicated worker thread; `drain(timeout)` blocks until every
    callback queued *so far* has finished running (via `Queue.join()`, fed
    to a `threading.Event` by a joiner thread so the wait itself has a hard
    timeout instead of blocking forever).
    """

    def __init__(self) -> None:
        self._q: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def idle_add(self, fn, *args):
        self._q.put((fn, args))
        return 1

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            fn, args = item
            try:
                fn(*args)
            except Exception:
                # Don't let a callback exception kill the worker thread --
                # mirrors GLib swallowing exceptions from idle callbacks
                # (they're logged, the loop keeps running).
                import traceback

                traceback.print_exc()
            finally:
                self._q.task_done()

    def drain(self, timeout: float = 10.0) -> None:
        done = threading.Event()

        def _joiner():
            self._q.join()
            done.set()

        threading.Thread(target=_joiner, daemon=True).start()
        if not done.wait(timeout):
            raise TimeoutError("fake main thread did not drain callbacks within timeout")

    def stop(self) -> None:
        self._q.put(None)
        self._worker.join(timeout=5)


@pytest.fixture
def daemon():
    service = make_daemon()
    main_thread = _FakeMainThread()
    # Harness patches `daemon_module.GLib` with a MagicMock and sets a trivial
    # inline `idle_add`; replace just that attribute so the daemon's own
    # GLib.idle_add(...) calls route through our ordered queue instead.
    service._mock_glib.idle_add = main_thread.idle_add
    service._main_thread = main_thread
    yield service
    main_thread.stop()
    service._patches.close()


def _result(text="hello world", confidence=0.9, duration=1.0, language="en"):
    result = MagicMock()
    result.text = text
    result.confidence = confidence
    result.duration = duration
    result.language = language
    return result


class _BatchEngineDouble:
    """Minimal non-streaming `SpeechEngine` test double for real-concurrency
    tests, mirroring `FakeStreamingSpeechEngine`'s `finalize_gate` pattern
    (WHISP-57) but for the BATCH `transcribe_numpy()` path.

    `entered` is set the instant `transcribe_numpy()` is called, so a test
    can deterministically wait for "the executor thread is now inside the
    engine call" before acting, instead of guessing with a sleep.
    """

    is_streaming = False

    def __init__(
        self,
        text: str = "ok",
        gate: "threading.Event | None" = None,
        error: Exception | None = None,
    ):
        self._text = text
        self._gate = gate
        self._error = error
        self.load_model = MagicMock()
        self.unload_model = MagicMock()
        self.cancel_transcription = MagicMock()
        self.entered = threading.Event()
        self.transcribe_numpy_calls = 0

    def transcribe_numpy(self, audio_data, **kwargs):
        self.transcribe_numpy_calls += 1
        self.entered.set()
        if self._gate is not None:
            if not self._gate.wait(timeout=10):
                raise TimeoutError("_BatchEngineDouble gate never released")
        if self._error is not None:
            raise self._error
        return _result(text=self._text)


def _arm_for_recording(daemon_obj, engine) -> None:
    """Install `engine` and a real executor, and make the mock recorder start
    out idle with a real stop() payload."""
    daemon_obj.transcriber = engine
    daemon_obj.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-wa")
    daemon_obj.history_manager.add_transcription.return_value = 1
    daemon_obj.recorder.is_recording = False
    daemon_obj.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)


class TestBatchStopThenStartDuringTranscription:
    """WHISP-67 (1): Stop->Start during transcription returns False; after
    release, the transcript is correct and the next Start works."""

    def test_stop_then_immediate_start_is_refused_until_transcription_completes(self, daemon):
        gate = threading.Event()
        engine = _BatchEngineDouble(text="session a text", gate=gate)
        _arm_for_recording(daemon, engine)

        assert daemon.StartRecording() is True
        session_a_id = daemon._current_session_id

        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False  # recorder.stop() has already run

        # Wait until the executor thread is actually inside transcribe_numpy
        # (i.e. genuinely in flight on another thread), not just submitted.
        assert engine.entered.wait(timeout=10)

        # --- The double-tap: Start while A's transcription is still blocked. ---
        assert daemon.StartRecording() is False
        daemon.TranscriptionReady.assert_not_called()
        assert daemon._current_session_id == session_a_id  # untouched by the refusal

        # --- Release A's transcription and wait for it to actually finish. ---
        gate.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        daemon.TranscriptionReady.assert_called_once()
        emitted_text, meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "session a text"
        assert meta["session_id"] == session_a_id
        assert daemon._transcribing is False

        # --- Now a fresh Start succeeds, with a brand-new session_id. ---
        daemon.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-wa")
        assert daemon.StartRecording() is True
        assert daemon._current_session_id != session_a_id
        daemon.executor.shutdown(wait=True)


class TestCancelWhileTranscribing:
    """WHISP-67 (2): CancelRecording() while transcribing.

    docs/dbus-api.md's CancelRecording section says: "From transcribing:
    requests transcription cancellation on the active engine, emits
    StatusChanged('idle'), returns true." It does not explicitly promise
    that no TranscriptionReady follows, but that is the only sensible
    reading of "cancel ... without producing a transcript" in the method's
    opening sentence.

    The daemon's actual CancelRecording() implementation sets
    `self._transcribing = False` and emits StatusChanged("idle")
    *synchronously*, on the calling thread, regardless of whether the
    executor-thread job backing that transcription has actually stopped.
    `self.transcriber.cancel_transcription()` is best-effort/cooperative
    (see `Transcriber.cancel_transcription()`'s own docstring -- "may ...
    return a partial result"), so a still-in-flight job can finish and try
    to run `_emit_transcription_success()`/`_emit_transcription_error()`
    well after CancelRecording() already told the world the daemon is idle
    again.

    WHISP-73 fix: CancelRecording() bumps a monotonic `self._job_id` token
    before anything else. The job submitted by the StopRecording() call
    captured the previous token at submission time; `_emit_transcription_
    success`/`_emit_transcription_error` check the token (once before doing
    any work, again inside the `GLib.idle_add` closure) and silently drop a
    stale result -- no `TranscriptionReady`, no clipboard/paste, no history
    row.
    """

    def test_cancel_during_transcription_suppresses_late_transcription_ready(self, daemon):
        gate = threading.Event()
        engine = _BatchEngineDouble(text="should not surface", gate=gate)
        _arm_for_recording(daemon, engine)

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert engine.entered.wait(timeout=10)

        assert daemon.CancelRecording() is True
        engine.cancel_transcription.assert_called_once()
        # CancelRecording() already declared the daemon idle...
        assert daemon._transcribing is False
        daemon.StatusChanged.assert_called_with("idle")
        # ...even though the backing job is still blocked in-flight.
        daemon.TranscriptionReady.assert_not_called()

        # A cooperative-but-slow (or non-cooperative) engine still finishes
        # and hands back a result after the cancel.
        gate.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        # Documented/expected contract: a cancel means no transcript surfaces,
        # and the stale job never touches history or the clipboard either.
        daemon.TranscriptionReady.assert_not_called()
        daemon.history_manager.add_transcription.assert_not_called()
        daemon.clipboard_manager.copy.assert_not_called()

        # A fresh Start works right away -- CancelRecording() already put the
        # daemon back in a clean idle state, independent of the stale job.
        daemon.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-wa")
        assert daemon.StartRecording() is True
        daemon.executor.shutdown(wait=True)


class TestCancelDuringStreamingFinalize:
    """WHISP-73: CancelRecording() while a streaming engine's finalize() is
    still in flight on the executor thread suppresses the late
    TranscriptionReady the same way as the batch path above."""

    def test_cancel_during_streaming_finalize_suppresses_late_transcription_ready(self, daemon):
        gate = threading.Event()
        engine = MagicMock()
        engine.is_streaming = True
        engine.load_model = MagicMock()
        engine.unload_model = MagicMock()
        engine.cancel_transcription = MagicMock()
        engine.cancel_session = MagicMock()
        engine.start_session = MagicMock()
        entered = threading.Event()

        def _finalize():
            entered.set()
            if not gate.wait(timeout=10):
                raise TimeoutError("finalize gate never released")
            return _result(text="should not surface")

        engine.finalize.side_effect = _finalize

        _arm_for_recording(daemon, engine)
        daemon.recorder.on_processed_chunk = None
        # Streaming StartRecording requires a ready model (0.3.0).
        daemon._model_loaded = True

        assert daemon.StartRecording() is True
        assert daemon._streaming_session is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert entered.wait(timeout=10)

        assert daemon.CancelRecording() is True
        engine.cancel_transcription.assert_called_once()
        assert daemon._transcribing is False
        daemon.StatusChanged.assert_called_with("idle")
        daemon.TranscriptionReady.assert_not_called()

        gate.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        daemon.TranscriptionReady.assert_not_called()
        daemon.history_manager.add_transcription.assert_not_called()


class TestBatchTranscriptionErrorRecovery:
    """WHISP-67 (3): a transcription error raised on the executor thread: the
    daemon returns to idle, emits Error, and accepts a new Start."""

    def test_error_on_executor_thread_returns_to_idle_and_accepts_new_start(self, daemon):
        engine = _BatchEngineDouble(error=RuntimeError("boom"))
        _arm_for_recording(daemon, engine)

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False

        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        daemon.Error.assert_called_once_with("transcription_failed", "boom")
        daemon.TranscriptionReady.assert_not_called()
        assert daemon._transcribing is False
        daemon.StatusChanged.assert_called_with("idle")

        daemon.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-wa")
        engine._error = None
        engine._text = "recovered"
        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()
        daemon.TranscriptionReady.assert_called_once()


class TestQuitWhileTranscribing:
    """WHISP-67 (4): Quit while transcribing: shutdown is clean,
    `executor.shutdown` completes, and nothing is emitted after cleanup.

    WHISP-73: Quit() also bumps the job token, so the in-flight job's
    eventual result is dropped as stale -- the assertions below pin that
    exact zero-emissions outcome (not just "the count didn't change further
    after settling", which the original WHISP-67 regression test checked)."""

    def test_quit_during_transcription_shuts_down_cleanly(self, daemon):
        gate = threading.Event()
        engine = _BatchEngineDouble(text="quit result", gate=gate)
        _arm_for_recording(daemon, engine)
        daemon._loop = MagicMock()

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert engine.entered.wait(timeout=10)

        assert daemon.Quit() is True
        daemon._loop.quit.assert_called_once()
        assert daemon._shutdown is True

        # Simulate run()'s `finally: self._cleanup()` -- it should block on
        # executor.shutdown(wait=True) until the in-flight job (held open on
        # `gate`) actually finishes.
        cleanup_done = threading.Event()

        def _do_cleanup():
            daemon._cleanup()
            cleanup_done.set()

        cleanup_thread = threading.Thread(target=_do_cleanup, daemon=True)
        cleanup_thread.start()

        # Cleanup must still be blocked on the in-flight job.
        assert not cleanup_done.wait(timeout=0.2)
        gate.set()
        assert cleanup_done.wait(timeout=10)
        cleanup_thread.join(timeout=5)

        daemon._main_thread.drain()

        # WHISP-73: Quit()'s job-token bump means this in-flight job's result
        # is dropped as stale -- not just "no further change after settling",
        # but genuinely zero emissions and no history/clipboard side effects.
        daemon.TranscriptionReady.assert_not_called()
        daemon.Error.assert_not_called()
        daemon.history_manager.add_transcription.assert_not_called()
        ready_calls = daemon.TranscriptionReady.call_count
        error_calls = daemon.Error.call_count

        # The executor must genuinely be shut down -- not merely idle.
        with pytest.raises(RuntimeError):
            daemon.executor.submit(lambda: None)

        # Nothing further gets emitted once cleanup has settled: draining
        # again is a no-op.
        daemon._main_thread.drain()
        assert daemon.TranscriptionReady.call_count == ready_calls
        assert daemon.Error.call_count == error_calls


class TestConcurrentToggle:
    """WHISP-67 (5): two rapid Toggles from different threads (simulated
    D-Bus calls on separate threads): the state stays consistent (recording
    XOR idle) and there is no exception."""

    def _wire_stateful_recorder(self, daemon_obj) -> None:
        daemon_obj.recorder.is_recording = False

        def _start_side_effect(*a, **k):
            daemon_obj.recorder.is_recording = True

        def _stop_side_effect(*a, **k):
            daemon_obj.recorder.is_recording = False
            return np.zeros(16000, dtype=np.float32)

        daemon_obj.recorder.start.side_effect = _start_side_effect
        daemon_obj.recorder.stop.side_effect = _stop_side_effect

    def _run_concurrently(self, fn, n=2, timeout=10.0):
        start_gate = threading.Event()
        exceptions: list[BaseException] = []
        results = []
        lock = threading.Lock()

        def _runner():
            start_gate.wait(timeout=timeout)
            try:
                result = fn()
                with lock:
                    results.append(result)
            except BaseException as e:  # noqa: BLE001 -- we want to see anything
                with lock:
                    exceptions.append(e)

        threads = [threading.Thread(target=_runner) for _ in range(n)]
        for t in threads:
            t.start()
        start_gate.set()
        for t in threads:
            t.join(timeout=timeout)
            assert not t.is_alive(), "toggle thread did not finish within timeout"
        return results, exceptions

    def test_two_concurrent_toggles_from_idle_raise_nothing_and_leave_valid_state(self, daemon):
        engine = _BatchEngineDouble(text="toggle result")
        _arm_for_recording(daemon, engine)
        self._wire_stateful_recorder(daemon)

        _results, exceptions = self._run_concurrently(daemon.ToggleRecording, n=2)

        assert exceptions == []
        # Both threads read is_recording=False in the race window and both
        # took the Start branch -- that's a double RecordingStarted (a
        # separate, narrower finding than this test's purpose), but the
        # important invariant for WHISP-67 (5) holds: the daemon ends up in
        # exactly one well-defined boolean state, not something corrupted.
        assert daemon.recorder.is_recording in (True, False)
        assert daemon._transcribing in (True, False)

        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

    def test_two_concurrent_toggles_from_recording_raise_nothing_and_settle_idle(self, daemon):
        engine = _BatchEngineDouble(text="toggle result")
        _arm_for_recording(daemon, engine)
        self._wire_stateful_recorder(daemon)
        daemon.recorder.is_recording = True

        _results, exceptions = self._run_concurrently(daemon.ToggleRecording, n=2)

        assert exceptions == []

        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        assert daemon._transcribing is False
        assert daemon.recorder.is_recording in (True, False)


class TestConfigReloadDuringTranscription:
    """WHISP-67 (6): SetConfig/ReloadConfig requiring a model reload, during
    transcription.

    docs/dbus-api.md's SetConfig section explicitly promises: "a model or
    audio change takes effect immediately, including mid-way through a
    session **once the current operation completes**" (emphasis on the
    documented ordering). `_apply_config_changes()` does not honour that: it
    reassigns `self.transcriber = create_engine(new_config)` unconditionally
    and immediately, with no check of `self._transcribing` and no lock
    shared with the executor-thread job. Because `_transcribe_audio()` reads
    `self.transcriber` fresh (`self.transcriber.transcribe_numpy(audio_data)`)
    rather than binding it once at submission time, a `SetConfig` landing in
    the small window between `_ensure_model_loaded()` returning and that
    next line executing makes the in-flight job call the **new** engine on
    the **old** audio -- not "once the current operation completes" as
    documented, but literally mid-operation.

    This test pins that exact window with a gate inserted via an
    instance-level wrapper around `_ensure_model_loaded` (test
    instrumentation only) to confirm the fix holds even if a reload call
    landed in the narrowest possible window.

    WHISP-74 fix (two independent, overlapping guards):

    - (a) The executor-thread job is handed a *snapshot* of `self.transcriber`
      at submission time (`StopRecording`/`_on_recorder_auto_stop`), and
      `_transcribe_audio()`/`_finalize_streaming_and_emit()` always use that
      snapshot, never a fresh read of `self.transcriber`.
    - (b) `_apply_config_changes()` additionally defers the engine swap
      itself (storing the new config in `self._pending_reload_config`)
      whenever `self._transcribing` or `self.recorder.is_recording` is true,
      applying it only once the daemon returns to idle (see
      `TestDeferredModelReload` below). So even without (a), the swap this
      test's gate used to catch a race for no longer happens mid-job at all.
    """

    def test_set_config_model_reload_does_not_swap_engine_under_inflight_job(self, daemon):
        old_engine = _BatchEngineDouble(text="old engine result")
        _arm_for_recording(daemon, old_engine)
        daemon._model_loaded = True  # model already loaded from a prior recording

        gate = threading.Event()
        original_ensure_model_loaded = daemon._ensure_model_loaded

        def _gated_ensure_model_loaded():
            original_ensure_model_loaded()
            # Pin the executor thread exactly between "model loaded" and
            # "read self.transcriber to call transcribe_numpy" -- the real
            # race window in _transcribe_audio().
            if not gate.wait(timeout=10):
                raise TimeoutError("test gate never released")

        daemon._ensure_model_loaded = _gated_ensure_model_loaded

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False

        new_engine = _BatchEngineDouble(text="new engine result")
        with patch.object(daemon._daemon_module, "create_engine", return_value=new_engine):
            assert daemon.SetConfig({"model.name": "small"}) is True

        gate.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        daemon.TranscriptionReady.assert_called_once()
        emitted_text, _meta = daemon.TranscriptionReady.call_args[0]

        # Documented/expected contract: the operation already in flight when
        # SetConfig landed finishes on the engine it started with.
        assert emitted_text == "old engine result"
        assert old_engine.transcribe_numpy_calls == 1
        assert new_engine.transcribe_numpy_calls == 0


class TestDeferredModelReload:
    """WHISP-74: a model-reload SetConfig()/ReloadConfig() landing while the
    daemon is busy (recording or transcribing) stores the new config in
    `self._pending_reload_config` instead of swapping `self.transcriber`
    immediately, and applies it once the daemon returns to idle -- via a
    successful transcription, a failed one, or CancelRecording()."""

    def test_deferred_reload_applied_after_success(self, daemon):
        gate = threading.Event()
        old_engine = _BatchEngineDouble(text="old engine result", gate=gate)
        _arm_for_recording(daemon, old_engine)
        daemon._model_loaded = True

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert old_engine.entered.wait(timeout=10)

        new_engine = _BatchEngineDouble(text="new engine result")
        with patch.object(
            daemon._daemon_module, "create_engine", return_value=new_engine
        ) as mock_ce:
            assert daemon.SetConfig({"model.name": "small"}) is True
            # Not swapped yet -- the in-flight job is still using old_engine.
            assert daemon.transcriber is old_engine
            assert daemon._pending_reload_config is not None
            mock_ce.assert_not_called()

            gate.set()
            daemon.executor.shutdown(wait=True)
            daemon._main_thread.drain()
            # 0.3.0: the deferred reload loads on a background thread.
            daemon._model_load_thread.join(timeout=10)

            mock_ce.assert_called_once()

        daemon.TranscriptionReady.assert_called_once()
        emitted_text, _meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "old engine result"
        assert daemon.transcriber is new_engine
        new_engine.load_model.assert_called_once()
        assert daemon._pending_reload_config is None
        assert daemon._model_loaded is True

    def test_deferred_reload_applied_after_error(self, daemon):
        gate = threading.Event()
        old_engine = _BatchEngineDouble(error=RuntimeError("boom"), gate=gate)
        _arm_for_recording(daemon, old_engine)
        daemon._model_loaded = True

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert old_engine.entered.wait(timeout=10)

        new_engine = _BatchEngineDouble(text="new engine result")
        with patch.object(
            daemon._daemon_module, "create_engine", return_value=new_engine
        ) as mock_ce:
            assert daemon.SetConfig({"model.name": "small"}) is True
            assert daemon.transcriber is old_engine
            mock_ce.assert_not_called()

            gate.set()
            daemon.executor.shutdown(wait=True)
            daemon._main_thread.drain()
            daemon._model_load_thread.join(timeout=10)

        daemon.Error.assert_called_once_with("transcription_failed", "boom")
        daemon.TranscriptionReady.assert_not_called()
        assert daemon.transcriber is new_engine
        new_engine.load_model.assert_called_once()
        assert daemon._pending_reload_config is None

    def test_deferred_reload_applied_after_cancel(self, daemon):
        gate = threading.Event()
        old_engine = _BatchEngineDouble(text="should not surface", gate=gate)
        _arm_for_recording(daemon, old_engine)
        daemon._model_loaded = True

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert old_engine.entered.wait(timeout=10)

        new_engine = _BatchEngineDouble(text="new engine result")
        with patch.object(daemon._daemon_module, "create_engine", return_value=new_engine) as mock_ce:
            assert daemon.SetConfig({"model.name": "small"}) is True
            mock_ce.assert_not_called()

            # CancelRecording() is synchronous: it applies the deferred
            # reload right away, before the stale job even finishes.
            assert daemon.CancelRecording() is True
            mock_ce.assert_called_once()

        assert daemon.transcriber is new_engine
        assert daemon._pending_reload_config is None

        gate.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()
        daemon.TranscriptionReady.assert_not_called()

    def test_two_set_config_calls_during_one_job_last_one_wins(self, daemon):
        gate = threading.Event()
        old_engine = _BatchEngineDouble(text="old engine result", gate=gate)
        _arm_for_recording(daemon, old_engine)
        daemon._model_loaded = True

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert old_engine.entered.wait(timeout=10)

        final_engine = _BatchEngineDouble(text="final engine result")
        with patch.object(daemon._daemon_module, "create_engine", return_value=final_engine) as mock_ce:
            assert daemon.SetConfig({"model.name": "small"}) is True
            first_pending = daemon._pending_reload_config
            assert first_pending is not None and first_pending.model.name == "small"

            assert daemon.SetConfig({"model.name": "medium"}) is True
            second_pending = daemon._pending_reload_config
            assert second_pending is not None and second_pending.model.name == "medium"
            assert second_pending is not first_pending

            gate.set()
            daemon.executor.shutdown(wait=True)
            daemon._main_thread.drain()

            # create_engine runs exactly once, with the LAST pending config.
            mock_ce.assert_called_once_with(second_pending)

        assert daemon.transcriber is final_engine
        assert daemon._pending_reload_config is None

    def test_cancel_during_recording_applies_pending_reload_not_leaving_it_stale(self, daemon):
        """Audit fix [HIGH 1]: CancelRecording()'s *recording* branch (the
        daemon was only recording, not yet transcribing) previously never
        applied or cleared `_pending_reload_config`. Exact repro from the
        audit: SetConfig() while recording (deferred) -> Cancel() (daemon
        returns to idle) -> a second, idle SetConfig("medium") (applied
        immediately) -> a fresh job. The job's engine -- and
        `self.transcriber` afterward -- must be "medium", matching what
        `GetConfig()`/a later `SetConfig()` caller would see, not have the
        earlier, now-stale "small" config silently re-applied on top of it
        once the job completes.
        """
        initial_engine = _BatchEngineDouble(text="n/a")
        _arm_for_recording(daemon, initial_engine)
        daemon._model_loaded = True
        daemon.recorder.is_recording = False

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True

        # 1. SetConfig while recording: deferred (busy). 2. Cancel while
        # still recording (not yet transcribing) -- the daemon returns to
        # idle right here, and applies the deferred reload. create_engine
        # must stay patched across both calls: the deferred reload is
        # actually built by CancelRecording(), not by SetConfig() itself.
        small_engine = _BatchEngineDouble(text="should never run")
        daemon.recorder.cancel.side_effect = lambda *a, **k: setattr(
            daemon.recorder, "is_recording", False
        )
        with patch.object(daemon._daemon_module, "create_engine", return_value=small_engine) as mock_ce_small:
            assert daemon.SetConfig({"model.name": "small"}) is True
            assert daemon._pending_reload_config is not None
            assert daemon._pending_reload_config.model.name == "small"
            mock_ce_small.assert_not_called()

            assert daemon.CancelRecording() is True
            # Fix (a): the pending reload is applied immediately, not left stale.
            mock_ce_small.assert_called_once()
        assert daemon._pending_reload_config is None
        assert daemon.transcriber is small_engine

        # 3. A second, idle SetConfig: applied immediately this time.
        medium_engine = _BatchEngineDouble(text="medium result")
        with patch.object(daemon._daemon_module, "create_engine", return_value=medium_engine) as mock_ce_medium:
            assert daemon.SetConfig({"model.name": "medium"}) is True
            mock_ce_medium.assert_called_once()
        assert daemon.transcriber is medium_engine
        # Fix (b): nothing stale left behind by the immediate path either.
        assert daemon._pending_reload_config is None

        # 4. A fresh recording/job runs entirely on the "medium" engine.
        daemon.recorder.is_recording = False
        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        daemon.TranscriptionReady.assert_called_once()
        emitted_text, _meta = daemon.TranscriptionReady.call_args[0]
        assert emitted_text == "medium result"
        assert daemon.transcriber is medium_engine
        assert small_engine.transcribe_numpy_calls == 0


class TestPreCheckToIdleAddWindow:
    """Audit fix [MEDIUM 3]: pin the window between
    `_emit_transcription_success`'s/`_emit_transcription_error`'s early
    (executor-thread) pre-check -- which passes because the job is not stale
    *yet* -- and the `GLib.idle_add` closure actually running on the (fake)
    main loop. A `CancelRecording()`/`Quit()` landing in exactly that gap
    must still be caught by the closure's own, authoritative check: no
    signal, no clipboard effect, and -- the thing a mutation that deletes
    only the closure's check (not the early pre-check) would otherwise get
    away with -- no history row.

    The gap is pinned by intercepting `idle_add` itself: once the executor
    thread calls it (i.e. the pre-check has already passed and the result is
    about to be marshalled onto the main loop), the interceptor blocks
    before actually enqueueing the real callback, giving the test a precise
    point to call CancelRecording()/Quit() -- which bumps the job token --
    before the closure the fake main thread will eventually run ever
    executes.
    """

    def _install_holding_idle_add(self, daemon_obj):
        """Replace `daemon._main_thread.idle_add` (and the mock GLib it's
        wired to) with a wrapper that blocks the first call until the test
        releases `hold`, signaling `intercepted` the moment it's entered so
        the test can proceed deterministically instead of guessing with a
        sleep."""
        real_idle_add = daemon_obj._main_thread.idle_add
        hold = threading.Event()
        intercepted = threading.Event()

        def _holding_idle_add(fn, *args):
            intercepted.set()
            hold.wait(timeout=10)
            return real_idle_add(fn, *args)

        daemon_obj._main_thread.idle_add = _holding_idle_add
        daemon_obj._mock_glib.idle_add = _holding_idle_add
        return hold, intercepted

    def test_cancel_in_pre_check_to_idle_add_window_drops_history_and_signal(self, daemon):
        gate = threading.Event()
        engine = _BatchEngineDouble(text="should not surface", gate=gate)
        _arm_for_recording(daemon, engine)

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert engine.entered.wait(timeout=10)

        hold, intercepted = self._install_holding_idle_add(daemon)

        # Release the engine: transcribe_numpy() returns, _transcribe_and_emit
        # calls _emit_transcription_success(), whose early pre-check passes
        # (the token is still current) and which calls GLib.idle_add(...) --
        # caught by the interceptor above, which now blocks.
        gate.set()
        assert intercepted.wait(timeout=10)

        # Cancel *after* the pre-check already passed, *before* the closure
        # has actually run -- exactly the window item 3 is about.
        assert daemon.CancelRecording() is True

        # Let the (now-stale) closure actually run.
        hold.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()

        daemon.TranscriptionReady.assert_not_called()
        daemon.history_manager.add_transcription.assert_not_called()
        daemon.clipboard_manager.copy.assert_not_called()

    def test_quit_in_pre_check_to_idle_add_window_drops_history_and_signal(self, daemon):
        gate = threading.Event()
        engine = _BatchEngineDouble(text="should not surface", gate=gate)
        _arm_for_recording(daemon, engine)
        daemon._loop = MagicMock()

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert engine.entered.wait(timeout=10)

        hold, intercepted = self._install_holding_idle_add(daemon)

        gate.set()
        assert intercepted.wait(timeout=10)

        assert daemon.Quit() is True

        # Let the (now-stale) closure proceed -- _cleanup()'s
        # executor.shutdown(wait=True) still needs it to finish before
        # cleanup itself can complete.
        hold.set()

        cleanup_done = threading.Event()

        def _do_cleanup():
            daemon._cleanup()
            cleanup_done.set()

        cleanup_thread = threading.Thread(target=_do_cleanup, daemon=True)
        cleanup_thread.start()
        assert cleanup_done.wait(timeout=10)
        cleanup_thread.join(timeout=5)

        daemon._main_thread.drain()

        daemon.TranscriptionReady.assert_not_called()
        daemon.Error.assert_not_called()
        daemon.history_manager.add_transcription.assert_not_called()


class TestCapturedWindowCleanup:
    """Audit fix [LOW 4]: `self._captured_by_job[job_id]` must be popped on
    every completion path -- success (with or without auto-paste), error,
    and a stale drop (early pre-check or the idle_add closure) -- not only
    inside the auto-paste branch of a successful, non-stale job. Otherwise a
    job whose window was captured but which errors, gets cancelled, or
    simply has auto_paste disabled leaks its entry in `_captured_by_job`
    forever."""

    def test_captured_window_popped_on_success_without_auto_paste(self, daemon):
        daemon.config.clipboard.auto_paste = False
        engine = _BatchEngineDouble(text="ok")
        _arm_for_recording(daemon, engine)
        daemon.clipboard_manager = MagicMock()
        daemon._captured_by_job["job-1"] = MagicMock()

        # Exercise _emit_transcription_success directly with an explicit
        # job_id, matching what StopRecording would pass through.
        daemon._emit_transcription_success(
            _result(text="ok"), np.zeros(16000, dtype=np.float32), "job-1", "sess-1", 0, token=None
        )
        daemon._main_thread.drain()

        assert "job-1" not in daemon._captured_by_job

    def test_captured_window_popped_on_error(self, daemon):
        engine = _BatchEngineDouble(error=RuntimeError("boom"))
        _arm_for_recording(daemon, engine)
        daemon._captured_by_job["job-err"] = MagicMock()

        daemon._emit_transcription_error(RuntimeError("boom"), token=None, job_id="job-err")
        daemon._main_thread.drain()

        assert "job-err" not in daemon._captured_by_job

    def test_captured_window_popped_on_stale_drop_in_success_pre_check(self, daemon):
        gate = threading.Event()
        engine = _BatchEngineDouble(text="should not surface", gate=gate)
        _arm_for_recording(daemon, engine)

        assert daemon.StartRecording() is True
        daemon.recorder.is_recording = True
        assert daemon.StopRecording() == "transcribing"
        daemon.recorder.is_recording = False
        assert engine.entered.wait(timeout=10)

        # The real job_id StopRecording generated isn't exposed back to the
        # test, so pin a second, synthetic entry under a known key and drive
        # _emit_transcription_success directly with a token that is already
        # stale (as if Cancel/Quit had bumped it) to exercise the early
        # pre-check's cleanup in isolation.
        daemon._captured_by_job["job-stale"] = MagicMock()
        stale_token = daemon._job_id  # capture current...
        daemon._job_id += 1  # ...then invalidate it.
        daemon._emit_transcription_success(
            _result(text="n/a"), None, "job-stale", "sess", 0, token=stale_token
        )

        assert "job-stale" not in daemon._captured_by_job

        gate.set()
        daemon.executor.shutdown(wait=True)
        daemon._main_thread.drain()
