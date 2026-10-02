"""Fake model-download backend for tests/e2e only (WHISP-68).

Mirrors `stt.engine.create_engine`'s existing `engine == "fake"` escape hatch: the
daemon's `DownloadModel` only ever reaches this module when BOTH
`WHISPER_ALOUD_TEST_MODE=1` and `WHISPER_ALOUD_FAKE_DOWNLOAD=1` are set, so an e2e
test can exercise the full DownloadModel/CancelDownload/DownloadProgress/
DownloadFinished D-Bus flow without any real network access -- no tarball, no HF
Hub, no urllib.

`WHISPER_ALOUD_FAKE_DOWNLOAD_FAIL=1` makes the simulated download raise partway
through, for exercising the daemon's error path end-to-end.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

_STEPS = 5
_TOTAL_BYTES = 1000
_STEP_DELAY_S = 0.02


def download_model(
    name: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Simulate a multi-step download of `name`, reporting progress and
    honoring `cancel_event`, entirely in-process and in-memory."""
    fail_at_step = (
        _STEPS // 2 if os.environ.get("WHISPER_ALOUD_FAKE_DOWNLOAD_FAIL") == "1" else None
    )
    for step in range(1, _STEPS + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("fake download cancelled")
        time.sleep(_STEP_DELAY_S)
        done = min(_TOTAL_BYTES, step * (_TOTAL_BYTES // _STEPS))
        if progress_cb is not None:
            progress_cb(done, _TOTAL_BYTES)
        if fail_at_step is not None and step == fail_at_step:
            raise RuntimeError("simulated fake download failure")
    return f"/fake/models/{name}"
