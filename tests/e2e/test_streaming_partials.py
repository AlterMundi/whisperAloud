"""E2E scenario: the daemon's streaming session + PartialTranscript signal
(WHISP-57), using the deterministic streaming fake engine
(FakeStreamingSpeechEngine) so this test never needs a real streaming model.

Run only via scripts/test_e2e.sh (see tests/e2e/conftest.py's session guard).
"""
import time

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_start_stop_emits_partials_then_final_transcript(daemon, audio_fixture):
    """Start recording with the streaming fake engine wired in (model.name=
    fake-streaming): PartialTranscript must fire at least twice with
    monotonically increasing seq and growing committed text, and the
    eventual TranscriptionReady must carry the full sidecar text and the
    same session_id as the partials."""
    fixture = audio_fixture("tone")  # 1s fixture, sidecar: "the quick brown fox"
    handle = daemon(
        fake_audio="tone",
        config_overrides={"MODEL_NAME": "fake-streaming"},
    )
    client = handle.client()
    partials = SignalCapture(client, "PartialTranscript")
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.start_recording() is True
    # Let the fake audio fully play out (1s of audio, 0.1s chunks) so the
    # streaming engine's accept_chunk() sees all of it and the ~5Hz partial
    # timer has multiple chances to fire before we stop.
    time.sleep(1.3)
    assert client.stop_recording() == "transcribing"

    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text

    received = list(partials._received)
    assert len(received) >= 2, f"expected >= 2 PartialTranscript signals, got {len(received)}"

    seqs = [args[3] for args in received]
    assert seqs == sorted(seqs), "seq must be monotonically non-decreasing"
    assert len(set(seqs)) == len(seqs), "no duplicate seq should ever be emitted"

    committed_lengths = [len(args[1]) for args in received]
    assert committed_lengths[-1] > committed_lengths[0], "committed text must grow over time"

    session_ids = {args[0] for args in received}
    assert len(session_ids) == 1, "every partial in one recording must share one session_id"
    assert session_ids == {meta["session_id"]}, (
        "PartialTranscript's session_id must match TranscriptionReady's meta['session_id']"
    )

    client.disconnect()
