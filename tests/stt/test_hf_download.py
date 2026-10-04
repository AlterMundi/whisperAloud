"""Tests for the faster-whisper (Hugging Face Hub) download backend (WHISP-68).

No real network access anywhere here: huggingface_hub.snapshot_download is always
monkeypatched. These tests exercise hf_download.py's own aggregation/cancellation
logic, not huggingface_hub's internals.
"""
from __future__ import annotations

import threading

import pytest

from whisper_aloud.stt import hf_download


def test_repo_id_for_uses_systran_template():
    assert hf_download.repo_id_for("base") == "Systran/faster-whisper-base"
    assert hf_download.repo_id_for("large-v3") == "Systran/faster-whisper-large-v3"


def test_is_cached_true_when_local_resolution_succeeds(monkeypatch):
    """WHISP-94: `is_cached` must decide via an actual local-only resolution
    attempt (never by guessing the cache directory layout)."""
    calls = []

    def _fake_download_model(size_or_id, local_files_only=False, cache_dir=None):
        calls.append((size_or_id, local_files_only, cache_dir))
        return "/fake/cache/Systran/faster-whisper-base"

    import faster_whisper.utils

    monkeypatch.setattr(faster_whisper.utils, "download_model", _fake_download_model)

    assert hf_download.is_cached("base") is True
    # Must always resolve with local_files_only=True -- never touches the network.
    assert calls == [("base", True, None)]


def test_is_cached_false_when_local_resolution_raises(monkeypatch):
    """When the model genuinely isn't in the cache, huggingface_hub raises
    (e.g. LocalEntryNotFoundError) rather than falling back to the network --
    `is_cached` must treat that as simply "not cached", not propagate."""
    import huggingface_hub

    def _fake_download_model(size_or_id, local_files_only=False, cache_dir=None):
        raise huggingface_hub.errors.LocalEntryNotFoundError("not cached")

    import faster_whisper.utils

    monkeypatch.setattr(faster_whisper.utils, "download_model", _fake_download_model)

    assert hf_download.is_cached("medium") is False


def test_is_cached_true_for_existing_local_directory(tmp_path, monkeypatch):
    """A literal filesystem path (not a canonical model name) is always
    treated as already-local -- WhisperModel never touches the hub for it,
    so `is_cached` must never even attempt the hub resolution in that case."""
    import faster_whisper.utils

    def _boom(*args, **kwargs):
        raise AssertionError("must not attempt hub resolution for a local directory")

    monkeypatch.setattr(faster_whisper.utils, "download_model", _boom)

    assert hf_download.is_cached(str(tmp_path)) is True


def test_is_cached_never_calls_hf_api_even_when_cached(monkeypatch):
    """WHISP-94 regression guard: the real reason the daemon phoned home for
    an already-cached model was `huggingface_hub.snapshot_download` calling
    the Hub API (`HfApi.model_info`/the repo-info endpoint) to check the
    remote revision. `is_cached` must resolve purely from the local cache --
    if anything on this path ever regresses to calling the Hub API, this
    test fails loudly instead of silently reaching the network again."""
    import huggingface_hub

    def _boom(*args, **kwargs):
        raise AssertionError(
            "is_cached() must never reach the Hugging Face Hub API -- this "
            "is exactly the WHISP-94 regression (a GET to huggingface.co "
            "for an already-cached model)"
        )

    monkeypatch.setattr(huggingface_hub.HfApi, "model_info", _boom)

    def _fake_download_model(size_or_id, local_files_only=False, cache_dir=None):
        # Faithful stand-in for huggingface_hub's real local_files_only=True
        # behavior: resolved purely from local cache metadata, no HfApi call.
        assert local_files_only is True
        return "/fake/cache/Systran/faster-whisper-base"

    import faster_whisper.utils

    monkeypatch.setattr(faster_whisper.utils, "download_model", _fake_download_model)

    assert hf_download.is_cached("base") is True


def test_is_cached_passes_through_cache_dir(monkeypatch):
    captured = {}

    def _fake_download_model(size_or_id, local_files_only=False, cache_dir=None):
        captured["cache_dir"] = cache_dir
        return "/fake/path"

    import faster_whisper.utils

    monkeypatch.setattr(faster_whisper.utils, "download_model", _fake_download_model)

    hf_download.is_cached("base", cache_dir="/custom/root")
    assert captured["cache_dir"] == "/custom/root"


def test_download_model_indeterminate_when_tqdm_class_unsupported(monkeypatch):
    """An old huggingface_hub without tqdm_class support reports (0, 0) twice
    (start, end) instead of per-byte progress."""
    monkeypatch.setattr(hf_download, "_snapshot_download_supports_tqdm_class", lambda: False)

    calls = []

    def _fake_snapshot_download(repo_id):
        return f"/fake/cache/{repo_id}"

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_snapshot_download)

    path = hf_download.download_model("base", progress_cb=lambda d, t: calls.append((d, t)))
    assert path == "/fake/cache/Systran/faster-whisper-base"
    assert calls == [(0, 0), (0, 0)]


class _FakeTqdm:
    """Minimal stand-in for tqdm's public surface used by hf_download's subclass."""

    def __init__(self, total=None, **kwargs):
        self.total = total
        self.n = 0

    def update(self, n=1):
        self.n += n
        return None


def test_download_model_aggregates_progress_across_files(monkeypatch):
    """Simulates snapshot_download creating two per-file tqdm bars and
    reports aggregated (done, total) through progress_cb."""
    monkeypatch.setattr(hf_download, "_snapshot_download_supports_tqdm_class", lambda: True)

    # Patch the `tqdm` import hf_download does lazily inside download_model.
    import sys
    import types

    fake_tqdm_module = types.ModuleType("tqdm")
    fake_tqdm_module.tqdm = _FakeTqdm
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm_module)

    captured_cls = {}

    def _fake_snapshot_download(repo_id, tqdm_class=None):
        captured_cls["cls"] = tqdm_class
        bar1 = tqdm_class(total=100)
        bar2 = tqdm_class(total=50)
        bar1.update(40)
        bar2.update(20)
        bar1.update(60)
        bar2.update(30)
        return f"/fake/cache/{repo_id}"

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_snapshot_download)

    events = []
    path = hf_download.download_model("base", progress_cb=lambda d, t: events.append((d, t)))

    assert path == "/fake/cache/Systran/faster-whisper-base"
    # Final aggregated state: 100 + 50 bytes done out of 100 + 50 total.
    assert events[-1] == (150, 150)


def test_download_model_cancellation_raises_download_cancelled(monkeypatch):
    monkeypatch.setattr(hf_download, "_snapshot_download_supports_tqdm_class", lambda: True)

    import sys
    import types

    fake_tqdm_module = types.ModuleType("tqdm")
    fake_tqdm_module.tqdm = _FakeTqdm
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm_module)

    cancel_event = threading.Event()

    def _fake_snapshot_download(repo_id, tqdm_class=None):
        bar = tqdm_class(total=100)
        cancel_event.set()
        bar.update(10)  # must raise DownloadCancelled once cancel_event is set
        return "should-not-reach-here"

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_snapshot_download)

    with pytest.raises(hf_download.DownloadCancelled):
        hf_download.download_model("base", cancel_event=cancel_event)
