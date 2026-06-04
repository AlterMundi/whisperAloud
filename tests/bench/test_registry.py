import pytest
from bench.engines import resolve, ENGINE_REGISTRY


def test_registry_has_core_engines():
    assert "faster-whisper" in ENGINE_REGISTRY
    assert "parakeet" in ENGINE_REGISTRY
    assert "whispercpp" in ENGINE_REGISTRY


def test_resolve_parses_engine_and_model():
    engine, model = resolve("faster-whisper:small")
    assert engine.name == "faster-whisper"
    assert model == "small"


def test_resolve_unknown_raises():
    with pytest.raises(KeyError):
        resolve("nope:x")
