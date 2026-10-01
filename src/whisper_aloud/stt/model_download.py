"""Download + manage sherpa-onnx speech models (WHISP-36).

Maps a canonical model name to a sherpa-onnx GitHub release tarball, downloads it
to the user cache, extracts and flattens it, and verifies the expected files. The
`_download` function is module-level so tests can monkeypatch it (no real network).
"""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import catalog

logger = logging.getLogger(__name__)

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
