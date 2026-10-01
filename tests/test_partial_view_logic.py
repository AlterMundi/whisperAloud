"""Unit tests for ui/partial_view_logic.py (WHISP-58).

Pure state machine for rendering PartialTranscript/TranscriptionReady signals in the
GUI. No GTK involved — see tests/gui/test_main_window_partial_smoke.py for the
widget-level rendering test.
"""

from whisper_aloud.ui.partial_view_logic import PartialViewState


def test_initial_state_is_empty():
    state = PartialViewState()
    assert state.render() == ("", "")


def test_first_partial_adopts_session_and_renders():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    assert state.render() == ("Hello ", "wor")


def test_later_partial_same_session_updates():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    state.on_partial_transcript("sess-1", "Hello world", "", 2)
    assert state.render() == ("Hello world", "")


def test_ignores_partial_from_different_session():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    # A stray partial from a previous/other session must not disturb current view.
    state.on_partial_transcript("sess-other", "Ignore me entirely", "", 99)
    assert state.render() == ("Hello ", "wor")


def test_ignores_out_of_order_seq():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello world", "", 5)
    # Stale update with a lower seq must be ignored.
    state.on_partial_transcript("sess-1", "Hel", "lo", 3)
    assert state.render() == ("Hello world", "")


def test_ignores_duplicate_seq():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello", "", 5)
    state.on_partial_transcript("sess-1", "Hello world, duplicate seq", "", 5)
    assert state.render() == ("Hello", "")


def test_transcription_ready_matching_session_replaces_and_clears():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    applied = state.on_transcription_ready("Hello world.", {"session_id": "sess-1"})
    assert applied is True
    assert state.render() == ("Hello world.", "")
    # Partial state fully cleared: there is no tracked session any more, so the
    # very next partial observed (whatever its session_id) is adopted fresh rather
    # than compared against the just-finished session.
    state.on_partial_transcript("sess-2", "New ", "one", 1)
    assert state.render() == ("New ", "one")


def test_transcription_ready_mismatching_session_ignored_for_partial_view():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    applied = state.on_transcription_ready(
        "Unrelated final text", {"session_id": "sess-other"}
    )
    assert applied is False
    # Must not touch the partial view at all.
    assert state.render() == ("Hello ", "wor")


def test_transcription_ready_without_prior_partial_still_applies():
    """Batch (non-streaming) engines never emit PartialTranscript; TranscriptionReady
    must still populate the view with the final text."""
    state = PartialViewState()
    applied = state.on_transcription_ready(
        "Final text from batch engine.", {"session_id": "sess-1"}
    )
    assert applied is True
    assert state.render() == ("Final text from batch engine.", "")


def test_transcription_ready_with_missing_meta_session_id_still_applies():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    assert state.on_transcription_ready("Final text", {}) is True
    assert state.render() == ("Final text", "")


def test_transcription_ready_with_non_dict_meta_still_applies():
    state = PartialViewState()
    assert state.on_transcription_ready("Final text", None) is True
    assert state.render() == ("Final text", "")


def test_reset_clears_everything():
    state = PartialViewState()
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    state.reset()
    assert state.render() == ("", "")
    # After reset, a partial from the same old session is treated as a fresh session.
    state.on_partial_transcript("sess-1", "Brand new", "", 1)
    assert state.render() == ("Brand new", "")


def test_is_active_reflects_whether_a_session_is_tracked():
    state = PartialViewState()
    assert state.is_active is False
    state.on_partial_transcript("sess-1", "Hello ", "wor", 1)
    assert state.is_active is True
    state.reset()
    assert state.is_active is False
