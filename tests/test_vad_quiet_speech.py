"""WHISP-77 regression tests: VAD drops quiet speech (e.g. a remote meeting
participant captured over speakers, much quieter than the local microphone).

Two independent checks, from cheapest to most realistic:

1. `faster_whisper.vad.get_speech_timestamps` directly, on a real speech clip
   attenuated enough that Silero's own default threshold (0.5) drops part of
   it -- demonstrating `TranscriptionConfig.vad_threshold`/`vad_min_speech_ms`
   (configurable, see config.py) recover it. No model download needed: the
   Silero ONNX model ships inside the `faster-whisper` package itself.

2. A full `Transcriber.transcribe_file()` run with the real `tiny` model
   against `tests/fixtures/vad_quiet_speech/two_speaker.wav` (see
   `tests/fixtures/generate_vad_quiet_speech.py` for how it was built): a
   loud "local" sentence followed by the same speaker's next sentence
   attenuated + lightly noised to stand in for a quiet remote participant.
   Marked `real_model` and skipped unless the `tiny` faster-whisper/CTranslate2
   model is already cached locally -- never downloads it.

Both use `cpu_threads=1` where a real model is loaded: CTranslate2's default
multi-threaded reduction is not bit-reproducible run to run, which would make
these regression tests flaky.
"""
from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import numpy as np
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vad_quiet_speech"


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = w.readframes(n)
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0, sr


def _load_metadata() -> dict:
    return json.loads((FIXTURE_DIR / "metadata.json").read_text())


def _faster_whisper_tiny_cached() -> bool:
    """True if Systran/faster-whisper-tiny is already in the local HF cache.

    Mirrors the project's `real_model` convention (see tests/e2e/conftest.py's
    collection hook): check for the cached snapshot, never trigger a download.
    """
    hf_home = os.environ.get("HF_HOME")
    cache_root = Path(hf_home) / "hub" if hf_home else Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_root / "models--Systran--faster-whisper-tiny"
    return model_dir.exists() and any(model_dir.rglob("model.bin"))


# ─── 1. Direct Silero VAD component: default threshold drops quiet speech ──


def test_default_silero_threshold_drops_quiet_speech_component():
    """Isolate Silero VAD's own contribution to the bug, independent of the
    whisper decoder: `quiet_speech_component.wav` (real speech, attenuated
    -26dB with a -35dB noise floor -- see
    tests/fixtures/generate_vad_quiet_speech.py) loses a meaningful fraction
    of its speech-timestamp coverage at Silero's threshold=0.5 default, and
    `TranscriptionConfig`'s lower, configurable threshold/min_speech_duration
    recover most of it.
    """
    vad = pytest.importorskip("faster_whisper.vad")

    quiet, sr = _read_wav(FIXTURE_DIR / "quiet_speech_component.wav")
    total_dur_s = len(quiet) / sr

    def coverage(timestamps) -> float:
        return sum(seg["end"] - seg["start"] for seg in timestamps) / sr

    default_ts = vad.get_speech_timestamps(quiet, vad.VadOptions(threshold=0.5))
    configured_ts = vad.get_speech_timestamps(
        quiet, vad.VadOptions(threshold=0.35, min_speech_duration_ms=100)
    )

    default_coverage = coverage(default_ts)
    configured_coverage = coverage(configured_ts)

    assert default_coverage < 0.8 * total_dur_s, (
        "expected Silero's default threshold to drop a meaningful fraction of "
        f"this quiet speech, got {default_coverage:.2f}s / {total_dur_s:.2f}s"
    )
    assert configured_coverage > default_coverage, (
        "configured (lower) VAD threshold should recover more speech than "
        f"Silero's default: {configured_coverage:.2f}s vs {default_coverage:.2f}s"
    )
    assert configured_coverage > 0.9 * total_dur_s, (
        f"expected the configured threshold to recover nearly all of the speech, "
        f"got {configured_coverage:.2f}s / {total_dur_s:.2f}s"
    )


# ─── 2. Full pipeline: Transcriber.transcribe_file() with the real model ───


@pytest.mark.real_model
@pytest.mark.skipif(not _faster_whisper_tiny_cached(), reason="faster-whisper tiny model not cached locally")
class TestTranscribeFileRecoversQuietSpeaker:
    """End-to-end real_model coverage of the actual fix: `transcribe_file()`
    on a loud-then-quiet two-"speaker" recording."""

    def _build_config(self, **overrides):
        from whisper_aloud.config import WhisperAloudConfig

        config = WhisperAloudConfig.load()
        config.model.name = "tiny"
        config.model.device = "cpu"
        config.transcription.language = "en"
        for key, value in overrides.items():
            setattr(config.transcription, key, value)
        return config

    def _transcribe_with_cpu_threads_1(self, config, audio_path: str):
        """Like Transcriber.transcribe_file(), but forces cpu_threads=1 for a
        deterministic decode (see module docstring)."""
        from faster_whisper import WhisperModel

        from whisper_aloud.transcriber import Transcriber

        transcriber = Transcriber(config)
        transcriber._model = WhisperModel(
            config.model.name, device="cpu", compute_type=config.model.compute_type, cpu_threads=1
        )
        return transcriber.transcribe_file(audio_path)

    def test_before_fix_default_vad_drops_speaker_b(self):
        """Characterizes the pre-fix behaviour: VAD at Silero's own defaults,
        no loudness normalization -- speaker B is lost."""
        config = self._build_config(
            normalize_loudness=False, vad_threshold=0.5, vad_min_speech_ms=250
        )
        result = self._transcribe_with_cpu_threads_1(config, str(FIXTURE_DIR / "two_speaker.wav"))
        meta = _load_metadata()
        assert meta["b_text"].rstrip(".").lower() not in result.text.lower()

    def test_after_fix_recovers_speaker_b(self):
        """With the chosen defaults (normalize_loudness=True, vad_threshold=0.35,
        vad_min_speech_ms=100), speaker B's sentence is recovered."""
        config = self._build_config()  # project defaults
        assert config.transcription.normalize_loudness is True
        assert config.transcription.vad_threshold == pytest.approx(0.35)
        assert config.transcription.vad_min_speech_ms == 100

        result = self._transcribe_with_cpu_threads_1(config, str(FIXTURE_DIR / "two_speaker.wav"))

        text_lower = result.text.lower()
        assert "country" in text_lower
        # Both sentences' key content words should now be present.
        assert "ask not" in text_lower or "not what" in text_lower  # speaker A
        assert "ask what you can do" in text_lower or "what you can do" in text_lower  # speaker B

    def test_silence_stays_silence_after_fix(self):
        """The fix must not hallucinate speech out of a pure noise floor."""
        config = self._build_config()  # project defaults: normalization + lower VAD threshold
        result = self._transcribe_with_cpu_threads_1(config, str(FIXTURE_DIR / "silence_only.wav"))
        assert result.text.strip() == ""
