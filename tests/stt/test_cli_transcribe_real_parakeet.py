"""Optional real-model smoke test for WHISP-76: `whisper-aloud transcribe` with the
real Parakeet engine, through the CLI, against a bench fixture wav.

Skipped unless sherpa-onnx is installed and the Parakeet model is already cached
locally (never downloads -- same pattern as
tests/stt/test_nemotron_streaming_engine.py::TestRealModelSmoke). The root
conftest.py redirects $XDG_CACHE_HOME to a throwaway temp dir for the whole
suite (desktop isolation), so the real cache is located via `~/.cache` (real
$HOME, which the root conftest does *not* touch) rather than $XDG_CACHE_HOME,
and is restored only inside this one test (monkeypatch, read-only -- this test
never writes into it).
"""
import importlib.util
import os
from pathlib import Path

import pytest

_HAS_SHERPA = importlib.util.find_spec("sherpa_onnx") is not None
# Real $HOME is untouched by the root conftest (only XDG_*/DBUS/runtime dirs
# are redirected), so this -- not $XDG_CACHE_HOME -- finds the real cache.
_REAL_CACHE_HOME = Path(os.environ.get("WHISPER_ALOUD_TEST_REAL_HOME", os.path.expanduser("~"))) / ".cache"
_MODEL_DIR = _REAL_CACHE_HOME / "whisper_aloud" / "models" / "parakeet-tdt-0.6b-v3"
_BENCH_WAV = _MODEL_DIR / "test_wavs" / "en.wav"


def _model_cached() -> bool:
    return (_MODEL_DIR / "tokens.txt").exists() and any(_MODEL_DIR.glob("encoder*.onnx"))


pytestmark = pytest.mark.real_model


@pytest.mark.skipif(not _HAS_SHERPA, reason="sherpa-onnx not installed")
@pytest.mark.skipif(not _model_cached(), reason="parakeet model not cached locally")
@pytest.mark.skipif(not _BENCH_WAV.exists(), reason="bench test_wavs fixture not present")
def test_transcribe_cli_with_real_parakeet_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(_REAL_CACHE_HOME))

    from whisper_aloud.__main__ import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "whisper-aloud", "transcribe",
            "--engine", "parakeet", "--model", "parakeet-tdt-0.6b-v3",
            "--format", "srt", str(_BENCH_WAV),
        ],
    )
    exit_code = main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "-->" in out
    assert len(out.strip()) > 0
