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

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from ..transcriber import TranscriptionResult

logger = logging.getLogger(__name__)


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


_SENTENCE_PUNCT = ".!?"

# Abbreviations (es/en) whose trailing period must never be read as a sentence end.
# Matched case-insensitively against the whole token (punctuation included).
_ABBREVIATIONS = frozenset(
    {
        "sr.", "sra.", "dr.", "dra.", "ud.", "uds.", "p.ej.", "ee.uu.",
        "mr.", "mrs.", "ms.", "e.g.", "i.e.", "vs.",
    }
)

# A numeric token (possibly with a trailing sentence-punctuation char stripped, e.g.
# the "." in "3." or "3.5."): digits optionally grouped by "." or "," separators.
_NUMERIC_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*")


def _is_sentence_end(word: str) -> bool:
    """True if `word` ends a sentence: its last char is one of `.!?`, it isn't a
    known abbreviation, and stripping that punctuation doesn't leave a bare number
    or decimal (so "3.5" / "El Sr." / "3." never trigger an early commit, but
    "Hello." / "Really?" / "Wow!" do)."""
    if not word or word[-1] not in _SENTENCE_PUNCT:
        return False
    if word.lower() in _ABBREVIATIONS:
        return False
    core = word.rstrip(_SENTENCE_PUNCT)
    if core and _NUMERIC_RE.fullmatch(core):
        return False
    return True


class CommitTracker:
    """Turns a stream of raw whole-utterance partials into committed/tentative text.

    Commit policy (WHISP-56, intentionally simple):
      - A word-prefix becomes committed once it is stable (byte-identical, same
        position) across the last `stable_count` (default N=2) consecutive raw
        partials.
      - Independently, any prefix ending in sentence-ending punctuation (``.``,
        ``!``, ``?``) in the *current* partial commits immediately, even on the
        very first update (useful for short utterances / fast speakers where two
        partials may never arrive before the sentence is spoken). A token only
        counts as a sentence end when the punctuation is its *last* character, the
        token isn't a number/decimal (``3.5``, ``3.``), and it isn't in a small
        es/en abbreviation set (``Sr.``, ``Dr.``, ``Mr.``, ``e.g.``, ...) -- see
        `_is_sentence_end`.
      - Once committed, text is never retracted: the committed length is
        monotonically non-decreasing, and already-committed words are kept
        verbatim even if a later raw partial revises that span (greedy streaming
        decode occasionally touches up earlier words; we do not take those edits
        back once we've told the caller they're final).

    `flush()` (used by `finalize()`) applies a separate reconciliation rule against
    the final, authoritative raw text -- see its docstring.
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

    def flush(self, final_raw_text: str | None = None) -> PartialResult:
        """Commit everything outstanding: no more revisions are coming.

        `final_raw_text`, when given, is the authoritative post-tail-padding decode
        (what `finalize()` gets back from the recognizer after `input_finished()`).
        Reconciliation rule against the already-committed prefix:
          - If `final_raw_text`'s words extend (or exactly match) the already
            committed prefix, the final text wins outright -- it is the most
            authoritative hypothesis the decoder will ever produce for this
            session, so no committed length is lost.
          - If it *diverges* (the committed prefix is not a prefix of the final
            words -- e.g. the decoder's one-shot finalization revised words we had
            already told the caller were final), we do NOT retract: the result is
            the existing committed words, followed by whatever of the final text
            comes after the longest common word-prefix with committed. A warning
            is logged with both strings so a mismatch is visible, but the
            already-committed text is never shortened or replaced.
        With no `final_raw_text` (legacy/defensive path), the last raw partial from
        `update()` history is used the same way -- it can only ever extend or match
        committed, by construction, since it's what committed was derived from.
        """
        if final_raw_text is not None:
            final_words = final_raw_text.split()
        elif self._history:
            final_words = list(self._history[-1])
        else:
            final_words = []

        committed = self._committed_words
        if final_words[: len(committed)] == committed:
            merged = final_words
        else:
            common_len = self._common_prefix_len(committed, final_words)
            merged = committed + final_words[common_len:]
            logger.warning(
                "CommitTracker.flush(): final text diverges from the committed "
                "prefix; keeping committed text and appending the remainder after "
                "the longest common word-prefix. committed=%r final=%r",
                " ".join(committed),
                " ".join(final_words),
            )
        self._committed_words = merged
        self._seq += 1
        return PartialResult(committed=" ".join(self._committed_words), tentative="", seq=self._seq)

    @staticmethod
    def _common_prefix_len(a: list[str], b: list[str]) -> int:
        length = 0
        for x, y in zip(a, b):
            if x != y:
                break
            length += 1
        return length

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
            if _is_sentence_end(w):
                length = i + 1
        return length
