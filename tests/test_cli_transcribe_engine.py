"""Tests for WHISP-76: catalog-driven --engine/--model, chunking, formats,
--output, --no-vad and --normalize on `whisper-aloud transcribe`.

Uses the hidden "fake" engine/model (WHISPER_ALOUD_TEST_MODE=1) so the chunked,
non-faster-whisper code path can be exercised deterministically and fast, with
no real model. See stt/fake_engine.py and stt/catalog.py's TEST_MODE gating.
"""
import importlib

import numpy as np
import pytest
import soundfile as sf

from whisper_aloud.__main__ import _resolve_transcribe_model, main
from whisper_aloud.stt import catalog as catalog_module


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def fake_engine_mode(monkeypatch):
    """Registers the hidden "fake"/"fake" catalog entry for the duration of a test,
    by reloading the catalog module under WHISPER_ALOUD_TEST_MODE=1 -- the same
    pattern used by tests/stt/test_engine_factory.py and tests/stt/test_catalog.py.
    `whisper_aloud.__main__` looks the module up lazily (`from .stt import
    catalog`) inside build_parser()/cmd_transcribe, so the reloaded entries are
    visible without reimporting __main__ itself.
    """
    monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
    importlib.reload(catalog_module)
    try:
        yield
    finally:
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        importlib.reload(catalog_module)


def _fake_wav_with_sidecar(tmp_path, text="hello world from the fake engine"):
    wav = tmp_path / "fixture.wav"
    sf.write(str(wav), np.zeros(1600, dtype=np.float32), 16000)
    wav.with_suffix(".txt").write_text(text)
    return wav


# ─── _resolve_transcribe_model (pure logic) ────────────────────────────────────


class _Args:
    def __init__(self, engine=None, model=None, language="auto", accept_license=False):
        self.engine = engine
        self.model = model
        self.language = language
        self.accept_license = accept_license


def test_resolve_default_matches_pre_whisp76_default():
    """No --engine/--model, language=auto -> the catalog's recommendation, which
    was (and remains) faster-whisper/base -- the historical hardcoded default."""
    from whisper_aloud.stt import catalog

    engine, model = _resolve_transcribe_model(_Args(), catalog)
    assert (engine, model) == ("faster-whisper", "base")


def test_resolve_explicit_engine_and_model():
    from whisper_aloud.stt import catalog

    engine, model = _resolve_transcribe_model(
        _Args(engine="parakeet", model="parakeet-tdt-0.6b-v3"), catalog
    )
    assert (engine, model) == ("parakeet", "parakeet-tdt-0.6b-v3")


def test_resolve_unknown_model_for_engine_raises():
    from whisper_aloud.stt import catalog

    with pytest.raises(ValueError, match="no model"):
        _resolve_transcribe_model(_Args(engine="faster-whisper", model="parakeet-tdt-0.6b-v3"), catalog)


def test_resolve_engine_only_picks_engines_recommendation():
    from whisper_aloud.stt import catalog

    engine, model = _resolve_transcribe_model(_Args(engine="parakeet"), catalog)
    assert (engine, model) == ("parakeet", "parakeet-tdt-0.6b-v3")


def test_resolve_model_only_finds_its_engine():
    from whisper_aloud.stt import catalog

    engine, model = _resolve_transcribe_model(_Args(model="parakeet-tdt-0.6b-v3"), catalog)
    assert (engine, model) == ("parakeet", "parakeet-tdt-0.6b-v3")


def test_resolve_needs_review_model_rejected_without_accept_license(monkeypatch):
    """WHISP-86: nemotron-streaming's needs_review is now False, so it's no longer
    this gate's real-world example -- exercise it against a synthetic needs_review
    entry instead to keep the mechanism covered."""
    from whisper_aloud.stt import catalog
    from whisper_aloud.stt.catalog import ModelEntry

    pending = ModelEntry(
        engine="fake-pending-engine", name="fake-pending-model", label="Fake Pending",
        size_mb=1, languages=None, streaming=False, license="Fake-License",
        devices=("cpu",), tier="fast", recommended=False, needs_review=True,
    )
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (pending,))

    with pytest.raises(ValueError, match="pending license review"):
        _resolve_transcribe_model(_Args(model="fake-pending-model"), catalog)


def test_resolve_needs_review_model_allowed_with_accept_license(monkeypatch):
    from whisper_aloud.stt import catalog
    from whisper_aloud.stt.catalog import ModelEntry

    pending = ModelEntry(
        engine="fake-pending-engine", name="fake-pending-model", label="Fake Pending",
        size_mb=1, languages=None, streaming=False, license="Fake-License",
        devices=("cpu",), tier="fast", recommended=False, needs_review=True,
    )
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (pending,))

    engine, model = _resolve_transcribe_model(
        _Args(model="fake-pending-model", accept_license=True), catalog
    )
    assert (engine, model) == ("fake-pending-engine", "fake-pending-model")


def test_resolve_nemotron_no_longer_requires_accept_license():
    """WHISP-86: nemotron's OpenMDW-1.1 license review completed, so it resolves
    like any other explicitly-named model -- no --accept-license needed."""
    from whisper_aloud.stt import catalog

    engine, model = _resolve_transcribe_model(
        _Args(model="nemotron-3.5-streaming-560ms"), catalog
    )
    assert (engine, model) == ("nemotron-streaming", "nemotron-3.5-streaming-560ms")


# ─── CLI end-to-end with the fake engine (chunked code path) ───────────────────


def test_cli_transcribe_fake_engine_plain_txt(fake_engine_mode, tmp_path, monkeypatch, capsys):
    wav = _fake_wav_with_sidecar(tmp_path)
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    with monkeypatch.context() as m:
        m.setattr(
            "sys.argv",
            ["whisper-aloud", "transcribe", "--engine", "fake", "--model", "fake", str(wav)],
        )
        exit_code = main()

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "hello world from the fake engine"


def test_cli_transcribe_fake_engine_srt_format(fake_engine_mode, tmp_path, monkeypatch, capsys):
    wav = _fake_wav_with_sidecar(tmp_path, text="segment one")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    with monkeypatch.context() as m:
        m.setattr(
            "sys.argv",
            ["whisper-aloud", "transcribe", "--engine", "fake", "--model", "fake",
             "--format", "srt", str(wav)],
        )
        exit_code = main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("1\n00:00:00,000 -->")
    assert "segment one" in out


def test_cli_transcribe_fake_engine_json_format(fake_engine_mode, tmp_path, monkeypatch, capsys):
    import json

    wav = _fake_wav_with_sidecar(tmp_path, text="json segment")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))

    with monkeypatch.context() as m:
        m.setattr(
            "sys.argv",
            ["whisper-aloud", "transcribe", "--engine", "fake", "--model", "fake",
             "--format", "json", str(wav)],
        )
        exit_code = main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["text"] == "json segment"
    assert payload["segments"][0]["text"] == "json segment"
    assert payload["language"] == "auto"


def test_cli_transcribe_fake_engine_writes_output_file(fake_engine_mode, tmp_path, monkeypatch):
    wav = _fake_wav_with_sidecar(tmp_path, text="to a file")
    monkeypatch.setenv("WHISPER_ALOUD_FAKE_AUDIO_WAV", str(wav))
    out_file = tmp_path / "out.txt"

    with monkeypatch.context() as m:
        m.setattr(
            "sys.argv",
            ["whisper-aloud", "transcribe", "--engine", "fake", "--model", "fake",
             "--output", str(out_file), str(wav)],
        )
        exit_code = main()

    assert exit_code == 0
    assert out_file.read_text().strip() == "to a file"


def test_cli_transcribe_invalid_model_still_rejected_at_parse_time():
    """Regression guard: dynamic catalog-driven choices must still reject a
    nonsense --model value with argparse's usual SystemExit(2), independent of
    the fake-engine machinery exercised above."""
    from whisper_aloud.__main__ import build_parser

    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["transcribe", "--model", "not-a-real-model", "x.wav"])
    assert excinfo.value.code == 2
