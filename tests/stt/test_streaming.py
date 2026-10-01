"""Tests for the streaming-engine protocol and commit policy (WHISP-56).

CommitTracker is pure logic (no sherpa-onnx, no numpy audio) so it is tested in
isolation here; the sherpa-backed adapter is tested separately in
test_nemotron_streaming_engine.py against a fake sherpa_onnx module.
"""
from __future__ import annotations

import pytest

from whisper_aloud.stt.streaming import CommitTracker, PartialResult, StreamingSpeechEngine


class TestPartialResult:
    def test_is_a_simple_dataclass_with_expected_fields(self):
        result = PartialResult(committed="hello", tentative="world", seq=1)
        assert result.committed == "hello"
        assert result.tentative == "world"
        assert result.seq == 1


class TestCommitTrackerStability:
    """Default policy: a word-prefix becomes committed once stable across N=2
    consecutive partials, or once it ends in sentence punctuation."""

    def test_first_partial_with_no_repeat_and_no_punctuation_commits_nothing(self):
        tracker = CommitTracker(stable_count=2)
        result = tracker.update("hello there")
        assert result.committed == ""
        assert result.tentative == "hello there"
        assert result.seq == 1

    def test_second_identical_prefix_commits_the_stable_words(self):
        tracker = CommitTracker(stable_count=2)
        tracker.update("hello there")
        result = tracker.update("hello there general")
        # "hello there" repeated unchanged across two consecutive partials -> committed.
        assert result.committed == "hello there"
        assert result.tentative == "general"
        assert result.seq == 2

    def test_divergent_partial_does_not_commit_anything_new(self):
        tracker = CommitTracker(stable_count=2)
        tracker.update("hello there")
        result = tracker.update("hi friend")
        assert result.committed == ""
        assert result.tentative == "hi friend"

    def test_committed_length_never_shrinks_even_if_a_later_partial_revises_it(self):
        tracker = CommitTracker(stable_count=2)
        tracker.update("hello there")
        tracker.update("hello there general")  # commits "hello there"
        # A later partial that (implausibly) revises already-committed words must
        # not retract the commit -- committed length is monotonically non-decreasing.
        result = tracker.update("halo there general kenobi")
        assert result.committed.startswith("hello there")

    def test_three_consecutive_identical_words_with_stable_count_3(self):
        tracker = CommitTracker(stable_count=3)
        tracker.update("a b c")
        r2 = tracker.update("a b c d")
        assert r2.committed == ""  # only 2 samples so far, need 3
        r3 = tracker.update("a b c d e")
        assert r3.committed == "a b c"
        assert r3.tentative == "d e"


class TestCommitTrackerPunctuation:
    def test_sentence_ending_punctuation_commits_immediately_even_on_first_partial(self):
        tracker = CommitTracker(stable_count=2)
        result = tracker.update("Hello there.")
        assert result.committed == "Hello there."
        assert result.tentative == ""

    def test_only_the_prefix_up_to_the_last_punctuation_mark_commits(self):
        tracker = CommitTracker(stable_count=2)
        result = tracker.update("Hello there. And then")
        assert result.committed == "Hello there."
        assert result.tentative == "And then"

    def test_question_and_exclamation_marks_also_commit(self):
        tracker = CommitTracker(stable_count=2)
        assert tracker.update("Really?").committed == "Really?"
        tracker.reset()
        assert tracker.update("Wow!").committed == "Wow!"


class TestCommitTrackerReset:
    def test_reset_clears_history_and_committed_state(self):
        tracker = CommitTracker(stable_count=2)
        tracker.update("hello there")
        tracker.update("hello there general")
        tracker.reset()
        result = tracker.update("brand new start")
        assert result.committed == ""
        assert result.tentative == "brand new start"
        assert result.seq == 1


class TestCommitTrackerValidation:
    def test_stable_count_must_be_at_least_one(self):
        with pytest.raises(ValueError):
            CommitTracker(stable_count=0)


class TestCommitTrackerFlush:
    def test_flush_commits_all_outstanding_tentative_text(self):
        tracker = CommitTracker(stable_count=2)
        tracker.update("hello there")
        result = tracker.flush()
        assert result.committed == "hello there"
        assert result.tentative == ""

    def test_flush_on_empty_history_returns_empty(self):
        tracker = CommitTracker(stable_count=2)
        result = tracker.flush()
        assert result.committed == ""
        assert result.tentative == ""


class TestStreamingSpeechEngineProtocol:
    def test_protocol_is_runtime_checkable_and_matches_a_conforming_fake(self):
        class _FakeStreamingEngine:
            is_streaming = True

            def load_model(self) -> None: ...
            def start_session(self, language: str) -> None: ...
            def accept_chunk(self, samples) -> None: ...
            def get_partial(self) -> PartialResult: ...
            def finalize(self): ...
            def cancel_session(self) -> None: ...

        assert isinstance(_FakeStreamingEngine(), StreamingSpeechEngine)

    def test_an_object_missing_methods_does_not_satisfy_the_protocol(self):
        class _Incomplete:
            is_streaming = True

            def load_model(self) -> None: ...

        assert not isinstance(_Incomplete(), StreamingSpeechEngine)
