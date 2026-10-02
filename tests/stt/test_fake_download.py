"""Tests for the TEST_MODE-only fake download backend (WHISP-68)."""
from __future__ import annotations

import threading

import pytest

from whisper_aloud.stt import fake_download


def test_fake_download_reports_progress_to_completion():
    events = []
    result = fake_download.download_model(
        "fake-model", progress_cb=lambda d, t: events.append((d, t))
    )
    assert result == "/fake/models/fake-model"
    assert events[-1] == (1000, 1000)
    assert all(total == 1000 for _done, total in events)


def test_fake_download_cancellation_stops_early():
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        fake_download.download_model("fake-model", cancel_event=cancel_event)


def test_fake_download_fail_env_var_raises(monkeypatch):
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_DOWNLOAD_FAIL", "1")
    with pytest.raises(RuntimeError, match="simulated"):
        fake_download.download_model("fake-model")
