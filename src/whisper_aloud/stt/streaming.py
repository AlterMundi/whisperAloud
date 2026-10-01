"""Streaming speech-engine contract + commit policy (WHISP-56).

`StreamingSpeechEngine` is the daemon-facing surface a native-streaming ASR adapter
(e.g. `NemotronStreamingEngine`) implements, parallel to the batch `SpeechEngine`
Protocol in `stt/engine.py`. `CommitTracker` is pure, sherpa-independent logic that
turns a sequence of raw whole-utterance partial hypotheses (as returned by
`sherpa_onnx.OnlineRecognizer.get_result()`, which always re-states the full
decoded-so-far text, not a delta) into a stable `committed` prefix and a `tentative`
remainder, so it is unit-testable without any model or audio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from ..transcriber import TranscriptionResult


@dataclass
class PartialResult:
    """A snapshot of a streaming session's hypothesis at one point in time.

    `committed` is text the commit policy considers final and will not retract.
    `tentative` is everything after it, which may still change on a later update.
    `seq` is a monotonically increasing counter, one per `accept_chunk()`/`update()`
    call, so callers (e.g. a future D-Bus signal in WHISP-57) can detect drops/reorders.
    """

    committed: str
    tentative: str
    seq: int


@runtime_checkable
class StreamingSpeechEngine(Protocol):
    """The native-streaming STT surface a session-oriented adapter implements.

    Distinct from (but designed to sit alongside) `stt.engine.SpeechEngine`: a
    streaming adapter may also satisfy `SpeechEngine` for the existing batch daemon
    path by feeding a whole buffer through `start_session`/`accept_chunk`/`finalize`
    internally (see `NemotronStreamingEngine.transcribe_numpy`).
    """

    is_streaming: bool

    def load_model(self) -> None: ...

    def start_session(self, language: str) -> None: ...

    def accept_chunk(self, samples: "np.ndarray") -> None: ...

    def get_partial(self) -> PartialResult: ...

    def finalize(self) -> "TranscriptionResult": ...

    def cancel_session(self) -> None: ...


_SENTENCE_END_RE = re.compile(r"[.!?]")


class CommitTracker:
    """Turns a stream of raw whole-utterance partials into committed/tentative text.

    Commit policy (WHISP-56, intentionally simple):
      - A word-prefix becomes committed once it is stable (byte-identical, same
        position) across the last `stable_count` (default N=2) consecutive raw
        partials.
      - Independently, any prefix ending in sentence-ending punctuation (``.``,
        ``!``, ``?``) in the *current* partial commits immediately, even on the
        very first update (useful for short utterances / fast speakers where two
        partials may never arrive before the sentence is spoken).
      - Once committed, text is never retracted: the committed length is
        monotonically non-decreasing, and already-committed words are kept
        verbatim even if a later raw partial revises that span (greedy streaming
        decode occasionally touches up earlier words; we do not take those edits
        back once we've told the caller they're final).
    """

    def __init__(self, stable_count: int = 2) -> None:
        if stable_count < 1:
            raise ValueError("stable_count must be >= 1")
        self._stable_count = stable_count
        self._history: list[list[str]] = []
        self._committed_words: list[str] = []
        self._seq = 0

    def reset(self) -> None:
        """Start a fresh session: clear history, committed text, and the seq counter."""
        self._history.clear()
        self._committed_words.clear()
        self._seq = 0

    def update(self, raw_text: str) -> PartialResult:
        """Feed one new raw whole-utterance partial and return the committed/tentative split."""
        words = raw_text.split()
        self._history.append(words)
        if len(self._history) > self._stable_count:
            self._history.pop(0)

        current_len = len(self._committed_words)
        stable_len = self._stable_prefix_len() if len(self._history) >= self._stable_count else 0
        punct_len = self._punctuation_prefix_len(words)
        candidate_len = min(max(current_len, stable_len, punct_len), len(words))

        if candidate_len > current_len:
            self._committed_words.extend(words[current_len:candidate_len])

        tentative_words = words[len(self._committed_words):]
        self._seq += 1
        return PartialResult(
            committed=" ".join(self._committed_words),
            tentative=" ".join(tentative_words),
            seq=self._seq,
        )

    def flush(self) -> PartialResult:
        """Commit everything outstanding (used by `finalize()`): no more revisions coming."""
        if self._history:
            self._committed_words = list(self._history[-1])
        self._seq += 1
        return PartialResult(committed=" ".join(self._committed_words), tentative="", seq=self._seq)

    def _stable_prefix_len(self) -> int:
        recent = self._history[-self._stable_count:]
        shortest = min(len(w) for w in recent)
        length = 0
        for i in range(shortest):
            token = recent[0][i]
            if all(w[i] == token for w in recent):
                length += 1
            else:
                break
        return length

    @staticmethod
    def _punctuation_prefix_len(words: list[str]) -> int:
        length = 0
        for i, w in enumerate(words):
            if _SENTENCE_END_RE.search(w):
                length = i + 1
        return length
