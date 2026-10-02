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
