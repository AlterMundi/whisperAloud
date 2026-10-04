"""WHISP-96 e2e: TranscribeFile() against a real daemon subprocess on a
private bus, using the fake STT engine (WHISPER_ALOUD_TEST_MODE=1) and a
small generated WAV fixture -- no real model, no real network.

Run only via scripts/test_e2e.sh -- see tests/e2e/conftest.py.
"""
import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_transcribe_file_returns_job_id_and_emits_ready(daemon, audio_fixture):
    """A full TranscribeFile() round trip: job_id comes back immediately,
    FileTranscriptionReady follows with the fake engine's deterministic
    text, and the mic-recording state machine (GetStatus()["state"]) is
    never touched."""
    fixture = audio_fixture("tone")
    handle = daemon(fake_audio="tone")
    client = handle.client()
    ready = SignalCapture(client, "FileTranscriptionReady")
    error = SignalCapture(client, "FileTranscriptionError")

    job_id = client.transcribe_file(str(fixture.wav_path), {})
    assert job_id != ""

    received_job_id, text, meta = ready.wait(timeout=10.0)
    assert received_job_id == job_id
    assert text == fixture.expected_text
    assert meta["history_id"] >= 0
    assert meta["source_name"] == fixture.wav_path.name

    with pytest.raises(AssertionError):
        error.wait(timeout=0.2)

    # Never perturbs the mic-recording state machine.
    status = client.get_status()
    assert status["state"] == "idle"

    client.disconnect()


def test_transcribe_file_invalid_path_returns_empty_job_id(daemon):
    handle = daemon()
    client = handle.client()
    error = SignalCapture(client, "Error")

    job_id = client.transcribe_file("/no/such/file.wav", {})
    assert job_id == ""

    code, _message = error.wait(timeout=5.0)
    assert code == "file_transcription_invalid_path"

    client.disconnect()


def test_transcribe_file_history_entry_is_tagged_with_source_filename(
    daemon, audio_fixture
):
    fixture = audio_fixture("tone")
    handle = daemon(fake_audio="tone")
    client = handle.client()
    ready = SignalCapture(client, "FileTranscriptionReady")

    client.transcribe_file(str(fixture.wav_path), {})
    _job_id, _text, meta = ready.wait(timeout=10.0)

    entries = client.get_history(limit=10)
    tagged = [e for e in entries if e.get("id") == meta["history_id"]]
    assert len(tagged) == 1
    assert f"source:{fixture.wav_path.name}" in tagged[0].get("tags", [])

    client.disconnect()


def test_transcribe_file_can_run_independently_of_mic_recording(daemon, audio_fixture):
    """A file transcription job must not be blocked by (or block) a
    concurrently in-flight mic recording."""
    fixture = audio_fixture("tone")
    handle = daemon(fake_audio="tone")
    client = handle.client()
    file_ready = SignalCapture(client, "FileTranscriptionReady")

    assert client.start_recording() is True

    job_id = client.transcribe_file(str(fixture.wav_path), {})
    assert job_id != ""
    received_job_id, text, _meta = file_ready.wait(timeout=10.0)
    assert received_job_id == job_id
    assert text == fixture.expected_text

    # The mic recording is unaffected by the file job.
    status = client.get_status()
    assert status["state"] == "recording"

    assert client.cancel_recording() is True
    client.disconnect()
