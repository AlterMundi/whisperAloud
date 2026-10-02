"""Tests for sherpa-onnx model download/management (WHISP-36, WHISP-68)."""
import tarfile
import threading
from pathlib import Path

import pytest

from whisper_aloud.stt import model_download as md


def test_model_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    assert d == tmp_path / "whisper_aloud" / "models" / "parakeet-tdt-0.6b-v3"


def test_unknown_model_raises(monkeypatch):
    with pytest.raises(ValueError, match="Unknown model"):
        md.ensure_model("does-not-exist")


def test_ensure_model_idempotent_skips_download(monkeypatch, tmp_path):
    """If the model files already exist, ensure_model returns the dir without downloading."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    (d / "encoder.onnx").write_bytes(b"\x00")
    called = {"download": False}
    def _fake_download(url, dest):
        called["download"] = True
    monkeypatch.setattr(md, "_download", _fake_download)
    result = md.ensure_model("parakeet-tdt-0.6b-v3")
    assert result == d
    assert called["download"] is False


def test_ensure_model_downloads_and_flattens(monkeypatch, tmp_path):
    """ensure_model downloads the tarball, extracts, and flattens the single top dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    src = tmp_path / "src" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
    src.mkdir(parents=True)
    (src / "tokens.txt").write_text("<blk> 0\n")
    (src / "encoder.onnx").write_bytes(b"\x00")
    (src / "decoder.onnx").write_bytes(b"\x00")
    (src / "joiner.onnx").write_bytes(b"\x00")
    fixture = tmp_path / "fixture.tar.bz2"
    with tarfile.open(fixture, "w:bz2") as tf:
        tf.add(src, arcname=src.name)

    def _fake_download(url, dest):
        Path(dest).write_bytes(fixture.read_bytes())
    monkeypatch.setattr(md, "_download", _fake_download)

    d = md.ensure_model("parakeet-tdt-0.6b-v3")
    assert (d / "tokens.txt").exists()
    assert (d / "encoder.onnx").exists()
    assert d == md.model_dir("parakeet-tdt-0.6b-v3")


def test_is_present_true_when_model_files_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    (d / "encoder.onnx").write_bytes(b"\x00")
    assert md.is_present("parakeet-tdt-0.6b-v3") is True


def test_is_present_false_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert md.is_present("parakeet-tdt-0.6b-v3") is False


def test_is_present_false_for_partial_download(monkeypatch, tmp_path):
    """tokens.txt alone (no encoder*.onnx) is not a complete model."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    assert md.is_present("parakeet-tdt-0.6b-v3") is False


# ─── download_model (WHISP-68: progress + cancellation) ─────────────────────


def _make_fixture_tarball(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
    src.mkdir(parents=True)
    (src / "tokens.txt").write_text("<blk> 0\n")
    (src / "encoder.onnx").write_bytes(b"\x00")
    (src / "decoder.onnx").write_bytes(b"\x00")
    (src / "joiner.onnx").write_bytes(b"\x00")
    fixture = tmp_path / "fixture.tar.bz2"
    with tarfile.open(fixture, "w:bz2") as tf:
        tf.add(src, arcname=src.name)
    return fixture


def test_download_model_idempotent_skips_download(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    (d / "encoder.onnx").write_bytes(b"\x00")
    called = {"download": False}

    def _fake(url, dest, progress_cb=None, cancel_event=None):
        called["download"] = True

    monkeypatch.setattr(md, "_download_with_progress", _fake)
    result = md.download_model("parakeet-tdt-0.6b-v3")
    assert result == d
    assert called["download"] is False


def test_download_model_reports_progress_and_flattens(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    fixture = _make_fixture_tarball(tmp_path)
    progress_events = []

    def _fake(url, dest, progress_cb=None, cancel_event=None):
        Path(dest).write_bytes(fixture.read_bytes())
        if progress_cb:
            progress_cb(50, 100)
            progress_cb(100, 100)

    monkeypatch.setattr(md, "_download_with_progress", _fake)
    d = md.download_model(
        "parakeet-tdt-0.6b-v3",
        progress_cb=lambda done, total: progress_events.append((done, total)),
    )
    assert (d / "tokens.txt").exists()
    assert (d / "encoder.onnx").exists()
    assert progress_events == [(50, 100), (100, 100)]


def test_download_model_cancel_leaves_no_partial_model_dir(monkeypatch, tmp_path):
    """A cancellation mid-download must never create model_dir()."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    cancel_event = threading.Event()

    def _fake(url, dest, progress_cb=None, cancel_event=None):
        # Simulate the real reporthook observing cancellation mid-transfer.
        Path(dest).write_bytes(b"partial-bytes-should-never-land")
        raise md.DownloadCancelled("cancelled mid-transfer")

    monkeypatch.setattr(md, "_download_with_progress", _fake)
    cancel_event.set()
    with pytest.raises(md.DownloadCancelled):
        md.download_model("parakeet-tdt-0.6b-v3", cancel_event=cancel_event)

    assert not d.exists(), "cancelled download must not leave a partial model dir"


def test_download_model_failure_leaves_no_partial_model_dir(monkeypatch, tmp_path):
    """An extraction/validation failure must also never create model_dir()."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")

    def _fake(url, dest, progress_cb=None, cancel_event=None):
        # Not a valid tarball -- tarfile.open() will raise.
        Path(dest).write_bytes(b"not-a-tarball")

    monkeypatch.setattr(md, "_download_with_progress", _fake)
    with pytest.raises(Exception):
        md.download_model("parakeet-tdt-0.6b-v3")

    assert not d.exists(), "a failed download must not leave a partial model dir"


def test_download_model_unknown_name_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="Unknown model"):
        md.download_model("does-not-exist")
