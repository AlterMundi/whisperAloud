"""E2E scenario: a Stop->Start double-tap (e.g. a hotkey) while the previous
recording's streaming finalize() is still running must be refused, not
corrupt the session (WHISP-57 audit, HIGH finding). Uses the deterministic
streaming fake engine with an injected finalize latency
(WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S) so the race window is wide enough to
hit reliably without any sleeps on the test's side beyond waiting for the
real daemon subprocess's signals.

Run only via scripts/test_e2e.sh (see tests/e2e/conftest.py's session guard).
"""
import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_start_while_finalize_in_flight_is_refused(daemon, audio_fixture):
    fixture = audio_fixture("tone")  # sidecar: "the quick brown fox"
    handle = daemon(
        fake_audio="tone",
        config_overrides={
            "MODEL_NAME": "fake-streaming",
            "FAKE_ENGINE_LATENCY_S": "0.5",
        },
    )
    client = handle.client()
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.start_recording() is True
    assert client.stop_recording() == "transcribing"

    # The daemon's finalize() is now sleeping ~0.5s in the background.
    # Starting again right now must be refused -- it must not call
    # engine.start_session() for a new session while finalize() is still
    # running against the same engine instance.
    assert client.start_recording() is False

    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text

    # Now that the previous recording's transcription has completed, a new
    # recording is accepted again.
    assert client.start_recording() is True
    assert client.stop_recording() == "transcribing"
    text2, meta2 = ready.wait(timeout=10.0, predicate=lambda t, m: m["session_id"] != meta["session_id"])
    assert text2 == fixture.expected_text
    assert meta2["session_id"] != meta["session_id"]

    client.disconnect()
