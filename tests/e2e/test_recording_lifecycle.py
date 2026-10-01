"""WHISP-47 e2e scenarios: the daemon's recording/transcription state machine.

Covers StartRecording/StopRecording/ToggleRecording/CancelRecording, the
StatusChanged sequence they drive, the max_recording_duration auto-stop path,
LevelUpdate emission, the fake engine's error/latency injection, and the
documented ToggleRecording-during-transcribing behavior. Run only via
scripts/test_e2e.sh -- see tests/e2e/conftest.py.
"""
import time

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_start_stop_status_sequence(daemon, audio_fixture):
    """Full start -> stop -> transcribe cycle: StatusChanged observed in order
    recording -> transcribing -> idle, with RecordingStarted/RecordingStopped/
    TranscriptionReady interleaved at the right points (design doc scenarios
    1 and 7)."""
    fixture = audio_fixture("tone")
    handle = daemon(fake_audio="tone")
    client = handle.client()
    started = SignalCapture(client, "RecordingStarted")
    stopped = SignalCapture(client, "RecordingStopped")
    status = SignalCapture(client, "StatusChanged")
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.start_recording() is True
    started.wait(timeout=5.0)
    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "recording")
    assert state == "recording"

    assert client.stop_recording() == "transcribing"
    stopped.wait(timeout=5.0)
    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "transcribing")
    assert state == "transcribing"

    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text
    assert meta["history_id"] >= 0
    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "idle")
    assert state == "idle"

    client.disconnect()


def test_toggle_twice_starts_then_stops(daemon, audio_fixture):
    """Scenario 2: first ToggleRecording starts capture, second stops and
    transcribes, same as a plain start/stop cycle."""
    fixture = audio_fixture("tone")
    handle = daemon(fake_audio="tone")
    client = handle.client()
    started = SignalCapture(client, "RecordingStarted")
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.toggle_recording() == "recording"
    started.wait(timeout=5.0)
    status = client.get_status()
    assert status["state"] == "recording"

    assert client.toggle_recording() == "transcribing"
    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text

    client.disconnect()


def test_cancel_mid_recording_produces_no_transcript(daemon, audio_fixture):
    """Scenario 3: CancelRecording() during recording discards the audio --
    no TranscriptionReady/Error fires, state returns to idle, no history
    row is added."""
    handle = daemon(fake_audio="tone")
    client = handle.client()
    ready = SignalCapture(client, "TranscriptionReady")
    error = SignalCapture(client, "Error")

    before = client.get_history(limit=10)
    assert client.start_recording() is True
    time.sleep(0.1)
    assert client.cancel_recording() is True

    status = client.get_status()
    assert status["state"] == "idle"

    with pytest.raises(AssertionError):
        ready.wait(timeout=1.0)
    with pytest.raises(AssertionError):
        error.wait(timeout=0.2)

    after = client.get_history(limit=10)
    assert len(after) == len(before)

    client.disconnect()


def test_start_recording_while_already_recording(daemon):
    """StartRecording() while already recording does not error: the
    underlying recorder treats it as a no-op (still RECORDING), but the
    daemon re-emits RecordingStarted/StatusChanged("recording") and returns
    True again -- it does not check state first (see docs/dbus-api.md)."""
    handle = daemon(fake_audio="tone")
    client = handle.client()
    started = SignalCapture(client, "RecordingStarted")

    assert client.start_recording() is True
    started.wait(timeout=5.0)

    assert client.start_recording() is True
    started.wait(timeout=5.0)

    assert client.get_status()["state"] == "recording"
    client.disconnect()


def test_idle_state_noop_and_error_paths(client):
    """Two idle-state edge cases sharing one daemon: StopRecording() while
    idle returns "error" and emits Error("stop_failed", ...) (the recorder
    raises because it isn't in a stoppable state, per docs/dbus-api.md), and
    CancelRecording() while idle is a pure no-op (returns False, no
    signal)."""
    error = SignalCapture(client, "Error")

    assert client.get_status()["state"] == "idle"
    assert client.stop_recording() == "error"
    code, message = error.wait(timeout=5.0)
    assert code == "stop_failed"
    assert message

    assert client.get_status()["state"] == "idle"
    assert client.cancel_recording() is False
    with pytest.raises(AssertionError):
        error.wait(timeout=0.2)


def test_auto_stop_at_max_recording_duration(daemon):
    """Scenario 5: with a short max_recording_duration, the daemon
    auto-stops and transcribes without an explicit StopRecording() call."""
    handle = daemon(
        config_overrides={"MAX_RECORDING_DURATION": "0.3"},
        fake_audio="tone",
    )
    client = handle.client()
    stopped = SignalCapture(client, "RecordingStopped")
    status = SignalCapture(client, "StatusChanged")
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.start_recording() is True
    stopped.wait(timeout=5.0)
    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "transcribing")
    assert state == "transcribing"

    text, meta = ready.wait(timeout=10.0)
    assert text

    client.disconnect()


def test_level_update_fires_during_recording_and_stops_after(daemon):
    """Scenario 6: LevelUpdate fires repeatedly (~10Hz) during recording for
    non-silent fixture content, and stops firing once recording stops."""
    handle = daemon(fake_audio="tone")
    client = handle.client()
    levels = SignalCapture(client, "LevelUpdate")

    assert client.start_recording() is True
    # Non-silent fixture: expect at least one LevelUpdate with level > 0.
    (level,) = levels.wait(timeout=5.0, predicate=lambda lvl: lvl > 0.0)
    assert level > 0.0

    assert client.stop_recording() == "transcribing"
    # No further LevelUpdate should arrive once the recorder has stopped.
    with pytest.raises(AssertionError):
        levels.wait(timeout=0.5, predicate=lambda lvl: True)

    client.disconnect()


def test_transcription_error_path_returns_to_idle(daemon):
    """Scenario: WHISPER_ALOUD_FAKE_ENGINE_ERROR makes the fake engine raise;
    the daemon emits Error + StatusChanged("idle") and does not emit
    TranscriptionReady or write a history row."""
    handle = daemon(
        config_overrides={"FAKE_ENGINE_ERROR": "synthetic transcription failure"},
        fake_audio="tone",
    )
    client = handle.client()
    error = SignalCapture(client, "Error")
    ready = SignalCapture(client, "TranscriptionReady")
    status = SignalCapture(client, "StatusChanged")

    before = client.get_history(limit=10)
    assert client.start_recording() is True
    time.sleep(0.1)
    assert client.stop_recording() == "transcribing"

    code, message = error.wait(timeout=10.0, predicate=lambda c, m: c == "transcription_failed")
    assert code == "transcription_failed"
    assert "synthetic transcription failure" in message

    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "idle")
    assert state == "idle"

    with pytest.raises(AssertionError):
        ready.wait(timeout=0.2)

    after = client.get_history(limit=10)
    assert len(after) == len(before)

    client.disconnect()


def test_toggle_during_transcribing_starts_a_new_recording(daemon):
    """Scenario: ToggleRecording() while the daemon is "transcribing" (the
    recorder itself is already idle) dispatches to StartRecording() rather
    than CancelRecording/StopRecording, per docs/dbus-api.md's documented
    "valid in any state" semantics for ToggleRecording. A long
    WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S keeps the first transcription
    in flight long enough to toggle during it deterministically."""
    handle = daemon(
        config_overrides={"FAKE_ENGINE_LATENCY_S": "2.0"},
        fake_audio="tone",
    )
    client = handle.client()
    started = SignalCapture(client, "RecordingStarted")
    status = SignalCapture(client, "StatusChanged")

    assert client.start_recording() is True
    started.wait(timeout=5.0)
    assert client.stop_recording() == "transcribing"
    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "transcribing")
    assert state == "transcribing"

    # The first transcription is now sleeping for 2s. Toggling now must
    # start a second recording (recorder.is_recording is False).
    assert client.toggle_recording() == "recording"
    started.wait(timeout=5.0)
    (state,) = status.wait(timeout=5.0, predicate=lambda s: s == "recording")
    assert state == "recording"

    client.disconnect()
