"""Unit tests for the daemon's explicit model-readiness state machine
(service/model_state.py, 0.3.0 model-preload design fix).

Pure logic: no D-Bus, no GLib, no real engine. Every cross-thread wait in
here is bounded by an Event/join timeout, never a bare sleep.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from whisper_aloud.service import model_state as ms


def _recorder():
    calls = []
    return calls, (lambda state, detail: calls.append((state, detail)))


class TestTracker:
    def test_initial_state_is_unloaded_and_silent(self):
        calls, cb = _recorder()
        tracker = ms.ModelStateTracker(on_change=cb)
        assert tracker.state == ms.UNLOADED
        assert tracker.detail == ""
        assert not tracker.is_ready
        assert calls == []

    def test_begin_then_finish_ready(self):
        calls, cb = _recorder()
        tracker = ms.ModelStateTracker(on_change=cb)
        gen = tracker.begin_load()
        assert tracker.state == ms.LOADING
        assert tracker.finish_load(gen, ms.READY) is True
        assert tracker.is_ready
        assert calls == [(ms.LOADING, ""), (ms.READY, "")]

    def test_finish_with_stale_generation_is_dropped(self):
        calls, cb = _recorder()
        tracker = ms.ModelStateTracker(on_change=cb)
        old = tracker.begin_load()
        new = tracker.begin_load()
        assert tracker.finish_load(old, ms.READY) is False
        assert tracker.state == ms.LOADING
        assert tracker.finish_load(new, ms.ERROR, "boom") is True
        assert tracker.snapshot() == (ms.ERROR, "boom")

    def test_set_state_supersedes_inflight_load(self):
        tracker = ms.ModelStateTracker()
        gen = tracker.begin_load()
        tracker.set_state(ms.DOWNLOADING, "fake/fake")
        assert tracker.finish_load(gen, ms.READY) is False
        assert tracker.snapshot() == (ms.DOWNLOADING, "fake/fake")

    def test_unchanged_state_does_not_reemit(self):
        calls, cb = _recorder()
        tracker = ms.ModelStateTracker(on_change=cb)
        tracker.set_state(ms.MISSING, "x")
        tracker.set_state(ms.MISSING, "x")
        assert calls == [(ms.MISSING, "x")]

    def test_rejects_unknown_state(self):
        tracker = ms.ModelStateTracker()
        with pytest.raises(ValueError):
            tracker.set_state("bogus")
        gen = tracker.begin_load()
        with pytest.raises(ValueError):
            tracker.finish_load(gen, ms.LOADING)

    def test_wait_while_loading_returns_immediately_when_settled(self):
        tracker = ms.ModelStateTracker()
        tracker.set_state(ms.READY)
        assert tracker.wait_while_loading() == ms.READY

    def test_wait_while_loading_wakes_on_finish_event(self):
        tracker = ms.ModelStateTracker()
        gen = tracker.begin_load()
        waiting = threading.Event()
        result = {}

        def _waiter():
            waiting.set()
            result["state"] = tracker.wait_while_loading()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        assert waiting.wait(timeout=5)
        tracker.finish_load(gen, ms.READY)
        t.join(timeout=5)
        assert not t.is_alive()
        assert result["state"] == ms.READY

    def test_close_wakes_waiters(self):
        tracker = ms.ModelStateTracker()
        tracker.begin_load()
        result = {}
        t = threading.Thread(
            target=lambda: result.setdefault("state", tracker.wait_while_loading()),
            daemon=True,
        )
        t.start()
        tracker.close()
        t.join(timeout=5)
        assert not t.is_alive()
        assert result["state"] == ms.LOADING
        assert tracker.closed

    def test_on_change_exception_does_not_break_tracker(self):
        def _boom(state, detail):
            raise RuntimeError("listener failed")

        tracker = ms.ModelStateTracker(on_change=_boom)
        gen = tracker.begin_load()
        assert tracker.finish_load(gen, ms.READY) is True
        assert tracker.is_ready


def _cfg(engine, name, download_root=None):
    return SimpleNamespace(
        model=SimpleNamespace(engine=engine, name=name, download_root=download_root)
    )


class TestIsModelAvailable:
    def test_faster_whisper_uses_offline_hf_cache_check(self):
        with patch("whisper_aloud.stt.hf_download.is_cached", return_value=True) as m:
            assert ms.is_model_available(_cfg("faster-whisper", "base", "/root")) is True
        m.assert_called_once_with("base", "/root")

    def test_faster_whisper_not_cached(self):
        with patch("whisper_aloud.stt.hf_download.is_cached", return_value=False):
            assert ms.is_model_available(_cfg("faster-whisper", "base")) is False

    @pytest.mark.parametrize("engine", ["parakeet", "nemotron-streaming"])
    def test_sherpa_engines_use_presence_check(self, engine):
        with patch("whisper_aloud.stt.model_download.is_present", return_value=False) as m:
            assert ms.is_model_available(_cfg(engine, "some-model")) is False
        m.assert_called_once_with("some-model")

    def test_fake_engine_available_unless_env_says_missing(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_FAKE_MODEL_MISSING", raising=False)
        assert ms.is_model_available(_cfg("fake", "fake")) is True
        monkeypatch.setenv("WHISPER_ALOUD_FAKE_MODEL_MISSING", "1")
        assert ms.is_model_available(_cfg("fake", "fake")) is False

    def test_unknown_engine_defers_to_load(self):
        assert ms.is_model_available(_cfg("mystery", "x")) is True


class TestMessages:
    def test_missing_message_names_model_and_download(self):
        msg = ms.missing_message(_cfg("faster-whisper", "base"))
        assert "base" in msg and "download" in msg.lower()

    @pytest.mark.parametrize(
        "state,detail,needle",
        [
            (ms.MISSING, "not downloaded", "not downloaded"),
            (ms.ERROR, "CUDA exploded", "CUDA exploded"),
            (ms.DOWNLOADING, "fake/fake", "downloading"),
            (ms.LOADING, "", "loading"),
            (ms.UNLOADED, "", "not loaded"),
        ],
    )
    def test_unavailable_message(self, state, detail, needle):
        msg = ms.unavailable_message(state, detail)
        assert needle.lower() in msg.lower()
        assert msg.lower().startswith("model not available")
