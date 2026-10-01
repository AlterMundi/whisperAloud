"""First e2e tests: prove the harness itself works end-to-end (real daemon
subprocess, real D-Bus, fake audio + fake STT) before WHISP-47 builds the
full scenario list on top of it. Run only via scripts/test_e2e.sh."""
import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_daemon_starts_and_responds_to_status(client):
    status = client.get_status()
    assert status["state"] == "idle"
    assert status["hotkey_backend"] == "none"


def test_start_stop_transcribe_yields_sidecar_text(daemon, audio_fixture):
    """Start -> stop with the fake engine must produce the fixture's sidecar
    text both in the TranscriptionReady signal and in history. The daemon
    needs WHISPER_ALOUD_FAKE_AUDIO_WAV set at spawn time (the recorder reads
    it once, at construction), so this uses daemon(fake_audio=...) directly
    rather than the zero-argument `client` fixture (which would spawn a
    second, unrelated daemon with no fake audio configured)."""
    fixture = audio_fixture("tone")
    handle = daemon(fake_audio="tone")
    client = handle.client()
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.start_recording() is True
    import time

    time.sleep(0.2)
    assert client.stop_recording() == "transcribing"

    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text
    assert meta["history_id"] >= 0

    history = client.get_history(limit=5)
    assert any(entry["text"] == fixture.expected_text for entry in history)
    client.disconnect()


def test_quit_stops_daemon_cleanly(daemon):
    handle = daemon()
    assert handle.proc.poll() is None  # still running
    handle.stop()
    assert handle.proc.poll() is not None  # exited
