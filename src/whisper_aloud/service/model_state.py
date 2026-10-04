"""Explicit, observable model-readiness state for the daemon (0.3.0).

Before this module the daemon deferred model loading to the first
transcription (commit 2791f9b), so every daemon start cost the user a
silent ~9 s wait on their first dictation, and a streaming StartRecording
loaded the model synchronously on the D-Bus/main-loop thread. Readiness is
now a first-class state the daemon exposes (GetStatus "model_state" and the
ModelStateChanged signal), driven by a background preload at startup.

States (the strings are part of the D-Bus API, see docs/dbus-api.md):

- ``unloaded``: no load attempted yet. Only observable before the daemon's
  main loop starts (run() kicks the preload) or for a service object that
  was never run (unit tests); a transcription in this state falls back to
  the legacy load-on-first-use, on the worker thread.
- ``missing``: the configured model is not available locally. Never
  auto-downloaded; the user starts DownloadModel explicitly.
- ``downloading``: a DownloadModel for the configured model is in flight
  (progress via the existing DownloadProgress signal).
- ``loading``: a background load (including its offline availability
  check) is in flight.
- ``ready``: the model is loaded; transcription starts immediately.
- ``error``: the last load failed; ``detail`` carries the message.

This module is pure logic (no GLib/D-Bus): the daemon wires ``on_change`` to
a GLib.idle_add that emits ModelStateChanged on the main loop.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

UNLOADED = "unloaded"
MISSING = "missing"
DOWNLOADING = "downloading"
LOADING = "loading"
READY = "ready"
ERROR = "error"

STATES = (UNLOADED, MISSING, DOWNLOADING, LOADING, READY, ERROR)

# Outcomes a load attempt can settle into.
_SETTLED_LOAD_STATES = (READY, MISSING, ERROR)


class ModelStateTracker:
    """Thread-safe model-readiness state with a generation counter.

    Every ``begin_load()``/``set_state()`` bumps the generation; a load
    worker reports its outcome with ``finish_load(generation, ...)``, which
    is silently dropped if a newer load (e.g. a config change switched
    models) or an explicit transition superseded it. ``wait_while_loading()``
    lets a transcription job block on the *event* of the load settling (a
    Condition notify), never on a poll/sleep.

    ``on_change(state, detail)`` is invoked while holding the internal lock,
    so emissions are strictly ordered across threads; it must therefore be
    cheap and must not call back into the tracker (the daemon only schedules
    a GLib.idle_add there).
    """

    def __init__(self, on_change: Optional[Callable[[str, str], None]] = None) -> None:
        self._cond = threading.Condition(threading.RLock())
        self._state = UNLOADED
        self._detail = ""
        self._generation = 0
        self._closed = False
        self._on_change = on_change

    # ─── Read side ──────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._cond:
            return self._state

    @property
    def detail(self) -> str:
        with self._cond:
            return self._detail

    @property
    def generation(self) -> int:
        with self._cond:
            return self._generation

    @property
    def is_ready(self) -> bool:
        return self.state == READY

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed

    def snapshot(self) -> tuple[str, str]:
        with self._cond:
            return self._state, self._detail

    # ─── Transitions ────────────────────────────────────────────────────

    def begin_load(self) -> int:
        """Enter ``loading`` and return the generation the load worker must
        hand back to ``finish_load``."""
        with self._cond:
            self._generation += 1
            self._set_locked(LOADING, "")
            return self._generation

    def finish_load(self, generation: int, state: str, detail: str = "") -> bool:
        """Record a load outcome. Returns False (and changes nothing) when
        ``generation`` is no longer current or the tracker is closed."""
        if state not in _SETTLED_LOAD_STATES:
            raise ValueError(f"finish_load: invalid outcome state {state!r}")
        with self._cond:
            if self._closed or generation != self._generation:
                return False
            self._set_locked(state, detail)
            return True

    def set_state(self, state: str, detail: str = "") -> int:
        """Unconditional transition (e.g. ``downloading``); supersedes any
        in-flight load. Returns the new generation."""
        if state not in STATES:
            raise ValueError(f"unknown model state {state!r}")
        with self._cond:
            self._generation += 1
            self._set_locked(state, detail)
            return self._generation

    def wait_while_loading(self, timeout: Optional[float] = None) -> str:
        """Block the calling (worker) thread while the state is ``loading``,
        woken by the next transition or ``close()``. Returns the state seen
        on wake-up. Never call this on the main loop."""
        with self._cond:
            self._cond.wait_for(lambda: self._state != LOADING or self._closed, timeout=timeout)
            return self._state

    def close(self) -> None:
        """Shutdown: wake every waiter; later ``finish_load`` calls are dropped."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _set_locked(self, state: str, detail: str) -> None:
        changed = (state, detail) != (self._state, self._detail)
        self._state = state
        self._detail = detail
        self._cond.notify_all()
        if changed and self._on_change is not None:
            try:
                self._on_change(state, detail)
            except Exception:
                logger.exception("model state listener failed")


# ─── Offline availability check ─────────────────────────────────────────


def is_model_available(config) -> bool:
    """Whether ``config``'s model can be loaded without any network access.

    Never touches the network: faster-whisper goes through WHISP-94's
    ``hf_download.is_cached`` (a ``local_files_only`` resolution), sherpa-onnx
    engines through ``model_download.is_present`` (a file-presence check).
    The test-only ``fake`` engine is available unless
    ``WHISPER_ALOUD_FAKE_MODEL_MISSING=1``. An engine this function does not
    know is reported available so ``load_model()`` itself decides.
    """
    engine = config.model.engine
    name = config.model.name
    if engine == "faster-whisper":
        from ..stt import hf_download

        return hf_download.is_cached(name, getattr(config.model, "download_root", None))
    if engine in ("parakeet", "nemotron-streaming"):
        from ..stt import model_download

        return model_download.is_present(name)
    if engine == "fake":
        return os.environ.get("WHISPER_ALOUD_FAKE_MODEL_MISSING") != "1"
    return True


def missing_message(config) -> str:
    """Human-readable ``detail`` for the ``missing`` state."""
    return (
        f"Model '{config.model.name}' ({config.model.engine}) is not downloaded; "
        f"download it from Settings or with `whisper-aloud download-model {config.model.name}`"
    )


def unavailable_message(state: str, detail: str = "") -> str:
    """Error text for a transcription/recording refused because the model
    is not ready."""
    reason = {
        MISSING: detail or "the model is not downloaded",
        ERROR: f"loading failed: {detail}" if detail else "loading failed",
        DOWNLOADING: "the model is still downloading",
        LOADING: "the model is still loading",
        UNLOADED: "the model is not loaded yet",
    }.get(state, detail or state)
    return f"Model not available ({reason})"
