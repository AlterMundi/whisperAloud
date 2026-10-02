"""Download + manage sherpa-onnx speech models (WHISP-36).

Maps a canonical model name to a sherpa-onnx GitHub release tarball, downloads it
to the user cache, extracts and flattens it, and verifies the expected files. The
`_download` function is module-level so tests can monkeypatch it (no real network).

WHISP-68 adds `download_model()`, a progress-reporting + cancellable sibling of
`ensure_model()` used by the daemon's `DownloadModel`/`CancelDownload` D-Bus
methods. `ensure_model()`/`_download()` are left exactly as they were (same
signature, same behavior) so existing callers/tests are unaffected.
"""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import catalog

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):
    """Raised internally to unwind a cancelled `download_model()` call."""

# Derived from stt.catalog, the single source of truth for model metadata (WHISP-50).
# Kept as a module-level dict (not a function) for backward compat with existing callers.
MODEL_SOURCES: dict[str, str] = {
    entry.name: entry.download_url for entry in catalog.entries() if entry.download_url
}


def model_dir(name: str) -> Path:
    """Cache directory for a model (honors $XDG_CACHE_HOME)."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return cache / "whisper_aloud" / "models" / name


def _is_present(d: Path) -> bool:
    return (d / "tokens.txt").exists() and any(d.glob("encoder*.onnx"))


def is_present(name: str) -> bool:
    """Public, name-based wrapper over _is_present — used by the Settings UI (WHISP-42) to
    show a model's downloaded/not-downloaded state without reaching into model_dir()/
    _is_present() directly."""
    return _is_present(model_dir(name))


def _refuse_or_present(d: Path, name: str) -> Optional[Path]:
    """WHISP-68 audit HIGH #2: never `rmtree()` an existing `model_dir(name)`
    blindly -- it may be a symlink (this is literally true on at least one
    dev machine, where `~/.cache/whisper_aloud/models/parakeet-tdt-0.6b-v3`
    is a symlink) or a directory managed/populated by something else.

    Returns `d` (treated as a no-op success) if it already holds a complete
    model. Raises `FileExistsError` with a clear message if `d` exists and
    is either a symlink or a non-empty directory not recognized as a
    complete model -- the caller must stop, not overwrite it. Returns
    `None` when `d` doesn't exist yet, or exists only as an empty,
    non-symlink directory (safe to replace via `os.replace`).
    """
    if _is_present(d):
        return d
    if d.is_symlink():
        raise FileExistsError(
            f"model path {d} exists and is a symlink -- refusing to overwrite or "
            "remove it. Remove or repoint it manually if you intend to replace "
            "this model."
        )
    if d.is_dir() and any(d.iterdir()):
        raise FileExistsError(
            f"model path {d} exists and is a non-empty directory not recognized as "
            "a complete model (missing tokens.txt / encoder*.onnx) -- it may be "
            "managed elsewhere; refusing to overwrite or remove it."
        )
    return None


def _extract_tarball_to_staging(tarball_path: str, staging_parent: Path, name: str) -> Path:
    """Extract `tarball_path` and flatten sherpa's single top-level dir into a
    fresh "staged" subdirectory of `staging_parent`. Returns the staged dir,
    already validated via `_is_present` (raises `RuntimeError` otherwise).
    `staging_parent` is always inside the caller's `tempfile.TemporaryDirectory`,
    so nothing here ever touches the real model cache."""
    extract_root = staging_parent / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball_path) as tf:
        tf.extractall(extract_root, filter="data")
    entries = list(extract_root.iterdir())
    top = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root
    if not (len(entries) == 1 and entries[0].is_dir()):
        logger.warning("unexpected tarball layout for model (%d top-level entries)", len(entries))
    staged = staging_parent / "staged"
    staged.mkdir()
    for item in top.iterdir():
        shutil.move(str(item), str(staged / item.name))
    if not _is_present(staged):
        raise RuntimeError(
            f"model '{name}' extracted but is missing tokens.txt / encoder*.onnx"
        )
    return staged


def _finalize_model_dir(
    staged: Path,
    d: Path,
    name: str,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Move a fully-staged, validated model directory into its final
    `model_dir(name)` location, as a single atomic `os.replace()` -- safe
    because `staged` was created inside a `tempfile.TemporaryDirectory(dir=
    d.parent)`, i.e. the same filesystem as `d` (WHISP-68 audit HIGH #2:
    staging on the cache's own filesystem rather than wherever $TMPDIR/tmpfs
    happens to be, and turning the final step into a same-filesystem rename
    instead of a cross-filesystem copy+rmtree).

    Checks `cancel_event` one last time immediately before the rename
    (WHISP-68 audit HIGH #3): a download cancelled a moment earlier can
    therefore never still land as a complete model directory, even if the
    cancellation raced the very end of the transfer. Re-checks
    `_refuse_or_present` right before the rename too, for the same
    never-rmtree-a-symlink-or-foreign-dir safety as the pre-download check
    (covers a `d` that appeared/changed during the download, e.g. a
    concurrent process).
    """
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled(f"download of model '{name}' was cancelled")
    present = _refuse_or_present(d, name)
    if present is not None:
        return present
    os.replace(str(staged), str(d))
    return d


def _download(url: str, dest: str) -> None:
    """Download url to dest with a coarse progress log. Monkeypatched in tests."""
    def _hook(block_num, block_size, total):
        if total > 0:
            pct = min(100, block_num * block_size * 100 // total)
            logger.info("downloading model: %d%%", pct)
    urllib.request.urlretrieve(url, dest, _hook)


def ensure_model(name: str) -> Path:
    """Return the local model dir, downloading + extracting it if absent. Idempotent.

    WHISP-68 audit HIGH #2: stages the download+extraction inside a temp dir
    created on the cache's own filesystem (`tempfile.TemporaryDirectory(dir=
    model_dir(name).parent)`), then moves it into place with a single atomic
    `os.replace()` -- never `rmtree()`s an existing `model_dir(name)` (see
    `_refuse_or_present`), so a symlinked or foreign-managed model dir is
    refused loudly instead of being destroyed.
    """
    if name not in MODEL_SOURCES:
        raise ValueError(
            f"Unknown model '{name}'. Known: {', '.join(sorted(MODEL_SOURCES))}"
        )
    d = model_dir(name)
    present = _refuse_or_present(d, name)
    if present is not None:
        return present
    d.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=d.parent) as tmp:
        tmp_path = Path(tmp)
        tarball = tmp_path / "model.tar.bz2"
        logger.info("fetching model '%s' from %s", name, MODEL_SOURCES[name])
        _download(MODEL_SOURCES[name], str(tarball))
        staged = _extract_tarball_to_staging(str(tarball), tmp_path, name)
        d = _finalize_model_dir(staged, d, name)
    return d


def _download_with_progress(
    url: str,
    dest: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Like `_download` but reports progress and can be cancelled mid-transfer.

    Module-level (like `_download`) so WHISP-68 tests can monkeypatch it the
    same way existing tests monkeypatch `_download`. Raises
    `DownloadCancelled` (from inside urlretrieve's reporthook, which
    propagates out of `urlretrieve` itself) as soon as `cancel_event` is
    observed set -- `urlretrieve` writes to `dest` incrementally, but `dest`
    always lives inside the caller's `tempfile.TemporaryDirectory()`, so an
    interrupted partial file is discarded with that directory, never
    touching the real model cache.
    """

    def _hook(block_num, block_size, total):
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("download cancelled")
        total = max(total, 0)
        done = block_num * block_size
        if total > 0:
            done = min(done, total)
            pct = done * 100 // total
            logger.info("downloading model: %d%%", pct)
        if progress_cb is not None:
            progress_cb(done, total)

    urllib.request.urlretrieve(url, dest, _hook)


def download_model(
    name: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Like `ensure_model` but reports progress and supports cancellation (WHISP-68).

    The entire download + extraction happens inside a `tempfile.TemporaryDirectory`
    staged on the cache's own filesystem (`dir=model_dir(name).parent`, WHISP-68
    audit HIGH #2 -- keeps the data off tmpfs/RAM and makes the final move a
    same-filesystem, atomic `os.replace()` rather than a cross-filesystem copy).
    `cancel_event` is checked one last time immediately before that final move
    (audit HIGH #3), so a cancellation can never still land as a complete model
    directory. The real `model_dir(name)` is never `rmtree()`'d -- see
    `_refuse_or_present` -- so a symlinked or foreign-managed path is refused
    loudly instead of destroyed.

    Raises `DownloadCancelled` if `cancel_event` was set during the download
    or right before the final move. Idempotent: returns immediately (no
    network access) if the model is already present.
    """
    if name not in MODEL_SOURCES:
        raise ValueError(
            f"Unknown model '{name}'. Known: {', '.join(sorted(MODEL_SOURCES))}"
        )
    d = model_dir(name)
    present = _refuse_or_present(d, name)
    if present is not None:
        return present
    d.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=d.parent) as tmp:
        tmp_path = Path(tmp)
        tarball = tmp_path / "model.tar.bz2"
        logger.info("fetching model '%s' from %s", name, MODEL_SOURCES[name])
        _download_with_progress(MODEL_SOURCES[name], str(tarball), progress_cb, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled(f"download of model '{name}' was cancelled")

        staged = _extract_tarball_to_staging(str(tarball), tmp_path, name)
        d = _finalize_model_dir(staged, d, name, cancel_event=cancel_event)
    return d
