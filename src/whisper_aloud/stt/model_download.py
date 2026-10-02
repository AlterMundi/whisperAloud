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


def _download(url: str, dest: str) -> None:
    """Download url to dest with a coarse progress log. Monkeypatched in tests."""
    def _hook(block_num, block_size, total):
        if total > 0:
            pct = min(100, block_num * block_size * 100 // total)
            logger.info("downloading model: %d%%", pct)
    urllib.request.urlretrieve(url, dest, _hook)


def ensure_model(name: str) -> Path:
    """Return the local model dir, downloading + extracting it if absent. Idempotent."""
    if name not in MODEL_SOURCES:
        raise ValueError(
            f"Unknown model '{name}'. Known: {', '.join(sorted(MODEL_SOURCES))}"
        )
    d = model_dir(name)
    if _is_present(d):
        return d
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tarball = os.path.join(tmp, "model.tar.bz2")
        logger.info("fetching model '%s' from %s", name, MODEL_SOURCES[name])
        _download(MODEL_SOURCES[name], tarball)
        extract_root = os.path.join(tmp, "extract")
        os.makedirs(extract_root, exist_ok=True)
        with tarfile.open(tarball) as tf:
            tf.extractall(extract_root, filter="data")
        # sherpa tarballs ship a single top-level dir; flatten its contents into d.
        entries = list(Path(extract_root).iterdir())
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(extract_root)
        if not (len(entries) == 1 and entries[0].is_dir()):
            logger.warning("unexpected tarball layout for model (%d top-level entries)", len(entries))
        for item in top.iterdir():
            target = d / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
    if not _is_present(d):
        raise RuntimeError(
            f"model '{name}' extracted but is missing tokens.txt / encoder*.onnx in {d}"
        )
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

    The entire download + extraction happens inside a single
    `tempfile.TemporaryDirectory()`; the real `model_dir(name)` is only ever
    touched once, as a single final `shutil.move()`, after extraction is
    complete and validated. A cancellation or any failure before that point
    therefore never leaves a partial model directory behind -- there is
    nothing to clean up at `model_dir(name)` because nothing was ever
    written there.

    Raises `DownloadCancelled` if `cancel_event` was set during the
    download. Idempotent: returns immediately (no network access) if the
    model is already present.
    """
    if name not in MODEL_SOURCES:
        raise ValueError(
            f"Unknown model '{name}'. Known: {', '.join(sorted(MODEL_SOURCES))}"
        )
    d = model_dir(name)
    if _is_present(d):
        return d
    with tempfile.TemporaryDirectory() as tmp:
        tarball = os.path.join(tmp, "model.tar.bz2")
        logger.info("fetching model '%s' from %s", name, MODEL_SOURCES[name])
        _download_with_progress(MODEL_SOURCES[name], tarball, progress_cb, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled(f"download of model '{name}' was cancelled")

        extract_root = os.path.join(tmp, "extract")
        os.makedirs(extract_root, exist_ok=True)
        with tarfile.open(tarball) as tf:
            tf.extractall(extract_root, filter="data")
        entries = list(Path(extract_root).iterdir())
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(extract_root)
        if not (len(entries) == 1 and entries[0].is_dir()):
            logger.warning("unexpected tarball layout for model (%d top-level entries)", len(entries))

        staged = Path(tmp) / "staged"
        staged.mkdir()
        for item in top.iterdir():
            shutil.move(str(item), str(staged / item.name))
        if not _is_present(staged):
            raise RuntimeError(
                f"model '{name}' extracted but is missing tokens.txt / encoder*.onnx"
            )

        # Only now, with a fully validated model in hand, touch the real cache dir.
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            shutil.rmtree(d)
        shutil.move(str(staged), str(d))
    return d
