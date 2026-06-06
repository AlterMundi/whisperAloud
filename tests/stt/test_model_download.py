"""Tests for sherpa-onnx model download/management (WHISP-36)."""
import tarfile
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
