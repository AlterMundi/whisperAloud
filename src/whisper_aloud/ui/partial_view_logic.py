"""Pure state machine for rendering PartialTranscript/TranscriptionReady signals.

See docs/dbus-api.md's "PartialTranscript" section for the wire contract this
tracks: a monotonically increasing `seq` scoped to a `session_id`, with `committed`
(stable prefix) and `tentative` (unstable tail) text. Kept free of GTK so it can be
unit-tested without a display (WHISP-58).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional


class PartialViewState:
    """Tracks the live partial transcript for the currently rendered session.

    Call `on_partial_transcript` for every PartialTranscript signal and
    `on_transcription_ready` for every TranscriptionReady signal; call `reset` when
    the GUI starts a new recording, cancels, or hits an error. `render()` returns
    the `(committed, tentative)` pair to display.
    """

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._last_seq: Optional[int] = None
        self._committed: str = ""
        self._tentative: str = ""

    def reset(self) -> None:
        """Clear all tracked state (new recording, cancel, or error)."""
        self._session_id = None
        self._last_seq = None
        self._committed = ""
        self._tentative = ""

    @property
    def is_active(self) -> bool:
        """True once a session's partials are being tracked."""
        return self._session_id is not None

    def render(self) -> tuple[str, str]:
        """Return (committed, tentative) text for display."""
        return self._committed, self._tentative

    def on_partial_transcript(
        self, session_id: str, committed: str, tentative: str, seq: int
    ) -> None:
        """Process a PartialTranscript(session_id, committed, tentative, seq) signal.

        Adopts the first session_id it sees after construction/reset. Ignores
        partials belonging to any other session, and ignores stale/out-of-order
        (non-increasing) seq values for the current session.
        """
        if self._session_id is None:
            self._session_id = session_id

        if session_id != self._session_id:
            return  # stale/other session (e.g. cancel/restart race)

        if self._last_seq is not None and seq <= self._last_seq:
            return  # out of order or duplicate

        self._last_seq = seq
        self._committed = committed
        self._tentative = tentative

    def on_transcription_ready(self, text: str, meta: Optional[Mapping[str, Any]]) -> bool:
        """Process a TranscriptionReady(text, meta) signal.

        When `meta["session_id"]` matches the session currently being tracked (or
        there is nothing being tracked yet, e.g. a batch engine that never streamed
        partials), replaces the view with the final `text` and clears partial
        tracking, returning True. When it mismatches an actively tracked session,
        the event is ignored for the partial view (a stale signal from a superseded
        session, e.g. a cancel-then-restart race) and this returns False — callers
        should not render that stale text over the session now being tracked.
        """
        meta_session_id = meta.get("session_id") if isinstance(meta, Mapping) else None

        if (
            self._session_id is not None
            and meta_session_id is not None
            and meta_session_id != self._session_id
        ):
            return False  # stale TranscriptionReady for a session we've moved on from

        self._session_id = None
        self._last_seq = None
        self._committed = text
        self._tentative = ""
        return True
