"""WHISP-68 e2e scenarios: DownloadModel/CancelDownload against a real daemon
subprocess, using the TEST_MODE-only fake download backend (stt/fake_download.py)
so no real network access happens. Run only via scripts/test_e2e.sh -- see
tests/e2e/conftest.py."""
from __future__ import annotations

import time

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_download_model_reports_progress_and_finishes_ok(daemon):
    """The fake download backend (only reachable with WHISPER_ALOUD_TEST_MODE=1
    AND WHISPER_ALOUD_FAKE_DOWNLOAD=1) reports progress to completion, with no
    real network access."""
    handle = daemon(config_overrides={"FAKE_DOWNLOAD": "1"})
    client = handle.client()
    progress = SignalCapture(client, "DownloadProgress")
    finished = SignalCapture(client, "DownloadFinished")

    assert client.get_status()["downloading"] is False
    started = client.download_model("faster-whisper", "tiny", False)
    assert started is True
    assert client.get_status()["downloading"] is True

    engine, name, ok, error = finished.wait(timeout=10.0)
    assert (engine, name, ok, error) == ("faster-whisper", "tiny", True, "")
    assert client.get_status()["downloading"] is False

    # At least one progress update should have arrived before completion.
    p_engine, p_name, bytes_done, bytes_total = progress.wait(
        timeout=5.0, predicate=lambda e, n, d, t: (e, n) == ("faster-whisper", "tiny")
    )
    assert p_engine == "faster-whisper"
    assert p_name == "tiny"
    assert bytes_total > 0
    assert 0 <= bytes_done <= bytes_total

    client.disconnect()


def test_download_model_refuses_concurrent_call(daemon):
    handle = daemon(config_overrides={"FAKE_DOWNLOAD": "1"})
    client = handle.client()
    finished = SignalCapture(client, "DownloadFinished")

    assert client.download_model("faster-whisper", "tiny", False) is True
    # The fake backend sleeps briefly between its simulated steps, so a second
    # call issued immediately after must find a download already in flight.
    assert client.download_model("faster-whisper", "base", False) is False

    finished.wait(timeout=10.0)
    client.disconnect()


def test_cancel_download_reports_cancelled(daemon):
    handle = daemon(config_overrides={"FAKE_DOWNLOAD": "1"})
    client = handle.client()
    finished = SignalCapture(client, "DownloadFinished")

    assert client.download_model("faster-whisper", "tiny", False) is True
    time.sleep(0.03)  # let at least one simulated step run
    assert client.cancel_download() is True

    engine, name, ok, error = finished.wait(timeout=10.0)
    assert (engine, name, ok, error) == ("faster-whisper", "tiny", False, "cancelled")
    assert client.get_status()["downloading"] is False

    client.disconnect()


def test_cancel_download_returns_false_when_idle(client):
    assert client.cancel_download() is False


def test_download_model_needs_review_refused_without_accept_license(daemon):
    handle = daemon(config_overrides={"FAKE_DOWNLOAD": "1"})
    client = handle.client()
    error_capture = SignalCapture(client, "Error")

    started = client.download_model(
        "nemotron-streaming", "nemotron-3.5-streaming-560ms", False
    )
    assert started is False
    assert client.get_status()["downloading"] is False

    code, _message = error_capture.wait(
        timeout=5.0, predicate=lambda code, message: code == "download_license_review_required"
    )
    assert code == "download_license_review_required"

    client.disconnect()


def test_download_model_failure_path_reports_error(daemon):
    handle = daemon(config_overrides={"FAKE_DOWNLOAD": "1", "FAKE_DOWNLOAD_FAIL": "1"})
    client = handle.client()
    finished = SignalCapture(client, "DownloadFinished")

    assert client.download_model("faster-whisper", "tiny", False) is True

    engine, name, ok, error = finished.wait(timeout=10.0)
    assert (engine, name, ok) == ("faster-whisper", "tiny", False)
    assert "simulated" in error

    client.disconnect()
