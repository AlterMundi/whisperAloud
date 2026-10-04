"""Download faster-whisper (Hugging Face Hub) models with progress + best-effort
cancellation (WHISP-68).

Unlike the sherpa-onnx path (`model_download.py`), `huggingface_hub.snapshot_download`
already manages its own cache layout (per-file temp download + rename into the hub's
blob store), so this module doesn't need its own staging directory -- it only adds a
progress-reporting hook and a cooperative cancellation check on top of it.

Kept as a separate module from `model_download.py` on purpose: that module is
specifically the sherpa-onnx tarball downloader (`MODEL_SOURCES`, `ensure_model`);
faster-whisper models aren't in that catalog of download URLs at all -- they're
fetched lazily by `WhisperModel(...)`/`huggingface_hub` using a repo id, a different
download mechanism entirely.
"""
from __future__ import annotations

import inspect
import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# faster-whisper's official CTranslate2-converted models on the Hub.
_REPO_TEMPLATE = "Systran/faster-whisper-{name}"


class DownloadCancelled(Exception):
    """Raised to unwind a cancelled faster-whisper download."""


def repo_id_for(name: str) -> str:
    """The Hugging Face Hub repo id for a canonical faster-whisper model name."""
    return _REPO_TEMPLATE.format(name=name)


def is_cached(model_size_or_path: str, cache_dir: Optional[str] = None) -> bool:
    """Whether `model_size_or_path` (a canonical faster-whisper model name, a
    Hub repo id, or a local directory) is already fully available offline.

    WHISP-94: WhisperAloud promises offline transcription, but `WhisperModel`
    resolves a model name via `huggingface_hub.snapshot_download`, which --
    unless told `local_files_only=True` -- always makes a network call to
    check the remote revision, even when every file it needs is already in
    the local cache. Deciding "is this cached" by guessing at
    `~/.cache/huggingface` directory layouts is fragile (custom `HF_HOME`,
    custom `download_root`, cache format changes), so instead this performs
    the real resolution `WhisperModel` itself would do
    (`faster_whisper.utils.download_model`, which maps the name to its Hub
    repo id and applies the same `allow_patterns`) but with
    `local_files_only=True`. `huggingface_hub` honors that flag purely from
    local cache metadata -- it never issues an HTTP request when it's set --
    so this call itself never touches the network, whether the model is
    cached or not: a `LocalEntryNotFoundError` (or any other failure to
    resolve locally) simply means "not cached yet", not an error.

    A literal filesystem path (`os.path.isdir`) is always already-local --
    `WhisperModel` never consults the Hub for it either (see
    `faster_whisper.transcribe.WhisperModel.__init__`) -- so it is reported
    cached without attempting any Hub resolution at all.
    """
    if os.path.isdir(model_size_or_path):
        return True
    try:
        from faster_whisper.utils import download_model as _fw_download_model

        _fw_download_model(model_size_or_path, local_files_only=True, cache_dir=cache_dir)
        return True
    except Exception:
        return False


def _snapshot_download_supports_tqdm_class() -> bool:
    """Whether the installed huggingface_hub's snapshot_download accepts a
    `tqdm_class` override -- checked by introspecting the installed version's
    actual signature, not a hardcoded version-number pin, so this keeps
    working across huggingface_hub upgrades/downgrades without edits here."""
    try:
        from huggingface_hub import snapshot_download

        return "tqdm_class" in inspect.signature(snapshot_download).parameters
    except Exception:
        return False


def download_model(
    name: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Download a faster-whisper model snapshot from the Hugging Face Hub.

    `name` is the canonical model name (e.g. "base", "large-v3") -- callers
    should resolve aliases via `stt.catalog.get()` first, same convention as
    `model_download.download_model`.

    Progress reporting: if the installed `huggingface_hub` exposes
    `tqdm_class` on `snapshot_download` (true as of 1.x; checked via
    introspection, see `_snapshot_download_supports_tqdm_class`), a small
    `tqdm` subclass reports the aggregate bytes downloaded across every
    per-file progress bar `snapshot_download` creates. `bytes_total` is the
    sum of only the per-file totals known *so far*: `snapshot_download`
    lists the repo and creates all per-file bars up front before downloading
    any of them, so in practice `bytes_total` converges to the true total
    almost immediately, but it can under-report for the first callback or
    two -- callers should treat an increasing `bytes_total` as normal, not a
    bug, not as "the download shrank".

    If the installed `huggingface_hub` predates `tqdm_class` support,
    per-byte progress isn't feasible without vendoring HTTP download logic
    ourselves (explicitly out of scope here) -- `progress_cb`, if given, is
    invoked once with `(0, 0)` ("indeterminate", per WHISP-68's own
    allowance) before the download starts and once more when it completes.

    Cancellation: `huggingface_hub`'s `snapshot_download()` has no public
    per-file-boundary cancellation hook. `cancel_event` is polled from
    inside the tqdm progress hook (called from whichever worker thread is
    downloading a given file), so cancellation can only take effect between
    chunks of the file(s) in flight at the moment it's requested -- not
    instantly, and not between threads that have already started their
    current file's download. This is a documented limitation, not a bug
    (see docs/dbus-api.md's CancelDownload section and the WHISP-68 ticket
    notes); the sherpa-onnx path above does not have this limitation since
    it's always exactly one file.
    """
    from huggingface_hub import snapshot_download

    repo_id = repo_id_for(name)

    if not _snapshot_download_supports_tqdm_class():
        logger.info(
            "installed huggingface_hub predates tqdm_class support; "
            "reporting indeterminate progress for '%s'",
            repo_id,
        )
        if progress_cb is not None:
            progress_cb(0, 0)
        path = snapshot_download(repo_id=repo_id)
        if progress_cb is not None:
            progress_cb(0, 0)
        return path

    from tqdm import tqdm as _tqdm_base

    state_lock = threading.Lock()
    done_by_bar: dict = {}
    total_by_bar: dict = {}

    def _report() -> None:
        if progress_cb is None:
            return
        with state_lock:
            done = sum(done_by_bar.values())
            total = sum(total_by_bar.values())
        progress_cb(done, total)

    class _ProgressTqdm(_tqdm_base):
        """tqdm subclass snapshot_download instantiates once per downloaded
        file; aggregates bytes across every instance into one progress_cb
        stream and polls `cancel_event` on every chunk update."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with state_lock:
                total_by_bar[id(self)] = self.total or 0
                done_by_bar[id(self)] = 0
            _report()

        def update(self, n=1):
            result = super().update(n)
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled("faster-whisper download cancelled")
            with state_lock:
                done_by_bar[id(self)] = self.n
                if self.total:
                    total_by_bar[id(self)] = self.total
            _report()
            return result

    try:
        return snapshot_download(repo_id=repo_id, tqdm_class=_ProgressTqdm)
    except DownloadCancelled:
        raise
    except Exception:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("faster-whisper download cancelled") from None
        raise
