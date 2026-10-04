"""E2E scenarios for the 0.3.0 model preload / readiness state (API 1.6.0).

The daemon loads the configured model in the background right after it
starts and reports readiness via GetStatus()["model_state"] and the
ModelStateChanged signal. These tests drive the fake engine's load through
a FIFO (WHISPER_ALOUD_FAKE_MODEL_LOAD_FIFO): the daemon sits in `loading`
until the test writes to it -- every wait here is on a signal or a FIFO
rendezvous, never a fixed sleep.

Run only via scripts/test_e2e.sh (see tests/e2e/conftest.py's session guard).
"""
import os

import pytest

from tests.e2e.conftest import SignalCapture, wait_for_model_state

pytestmark = pytest.mark.e2e


def _release(fifo) -> None:
    # Blocks until the daemon's loader has opened the FIFO (a rendezvous),
    # then lets load_model() return.
    with open(fifo, "wb") as writer:
        writer.write(b"x")


def test_status_reports_ready_shortly_after_startup_via_signal(daemon):
    """The model preloads at startup: ModelStateChanged("ready") arrives
    (or GetStatus already says ready) without any recording having happened."""
    handle = daemon(wait_model_state=None)
    client = handle.client()
    states = SignalCapture(client, "ModelStateChanged")  # subscribe before reading

    if client.get_status()["model_state"] != "ready":
        state, detail = states.wait(timeout=5.0, predicate=lambda s, d: s == "ready")
        assert (state, detail) == ("ready", "")

    status = client.get_status()
    assert status["model_state"] == "ready"
    assert status["model_error"] == ""
    assert status["state"] == "idle"
    client.disconnect()


def test_recording_before_ready_transcribes_once_model_is_ready(daemon, audio_fixture, tmp_path):
    """Batch engine: record and stop while the model is still loading; the
    job waits for the ready event and the transcript arrives once the load
    completes. The daemon stays responsive over D-Bus throughout."""
    fixture = audio_fixture("tone")
    fifo = tmp_path / "model-load.fifo"
    os.mkfifo(fifo)
    handle = daemon(
        fake_audio="tone",
        config_overrides={"FAKE_MODEL_LOAD_FIFO": str(fifo)},
        wait_model_state=None,
    )
    client = handle.client()
    states = SignalCapture(client, "ModelStateChanged")
    ready = SignalCapture(client, "TranscriptionReady")
    errors = SignalCapture(client, "Error")

    # The loader is blocked on the FIFO, yet D-Bus calls are served.
    assert client.get_status()["model_state"] == "loading"

    assert client.start_recording() is True
    assert client.stop_recording() == "transcribing"
    status = client.get_status()
    assert status["state"] == "transcribing"
    assert status["model_state"] == "loading"

    _release(fifo)

    states.wait(timeout=5.0, predicate=lambda s, d: s == "ready")
    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text
    assert meta["history_id"] >= 0
    assert errors._received == []
    client.disconnect()


def test_missing_model_reports_missing_and_recording_fails_clearly(daemon):
    """A model that is not available locally is never auto-downloaded: the
    state is `missing` (with a reason), and a recording fails with an
    explicit Error instead of silently waiting or vanishing."""
    handle = daemon(
        fake_audio="tone",
        config_overrides={"FAKE_MODEL_MISSING": "1"},
        wait_model_state="missing",
    )
    client = handle.client()
    errors = SignalCapture(client, "Error")
    status = SignalCapture(client, "StatusChanged")

    current = client.get_status()
    assert current["model_state"] == "missing"
    assert "fake" in current["model_error"]

    assert client.start_recording() is True
    assert client.stop_recording() == "transcribing"

    code, message = errors.wait(timeout=10.0, predicate=lambda c, m: c == "model_not_available")
    assert message.startswith("Model not available")
    status.wait(timeout=5.0, predicate=lambda s: s == "idle")
    client.disconnect()


def test_streaming_start_is_refused_until_model_ready(daemon, tmp_path):
    """Streaming engine: StartRecording never loads on the main loop -- it is
    refused with Error("model_not_ready") while loading, and succeeds once
    the model is ready."""
    fifo = tmp_path / "model-load.fifo"
    os.mkfifo(fifo)
    handle = daemon(
        fake_audio="tone",
        config_overrides={"MODEL_NAME": "fake-streaming", "FAKE_MODEL_LOAD_FIFO": str(fifo)},
        wait_model_state=None,
    )
    client = handle.client()
    errors = SignalCapture(client, "Error")

    assert client.start_recording() is False
    code, message = errors.wait(timeout=5.0)
    assert code == "model_not_ready"

    _release(fifo)
    wait_for_model_state(handle.client(), "ready")

    assert client.start_recording() is True
    assert client.cancel_recording() is True
    client.disconnect()
