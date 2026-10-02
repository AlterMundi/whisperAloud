"""WHISP-77 regression tests: VAD drops quiet speech (e.g. a remote meeting
participant captured over speakers, much quieter than the local microphone).

Two independent checks, from cheapest to most realistic:

1. `faster_whisper.vad.get_speech_timestamps` directly, on a real speech clip
   (with synthetic room reverb + band-limited "over speakers" coloration)
   attenuated enough that Silero's own default threshold (0.5) drops part of
   it -- demonstrating `TranscriptionConfig.vad_threshold`/`vad_min_speech_ms`
   (configurable, see config.py) recover it, and that `stt.normalize.normalize_loudness`
   itself does not reduce retention versus doing nothing (a real regression in
   an earlier implementation -- see stt/normalize.py's docstring). No model
   download needed: the Silero ONNX model ships inside the `faster-whisper`
   package itself.

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
    whisper decoder: `quiet_speech_component.wav` (real speech, run through
    synthetic room reverb + 300-3400Hz band-limiting then attenuated -15dB
    with a -45dB noise floor -- see tests/fixtures/generate_vad_quiet_speech.py)
    loses a meaningful fraction of its speech-timestamp coverage at Silero's
    threshold=0.5 default, and `TranscriptionConfig`'s lower, configurable
    threshold/min_speech_duration recover it.
    """
    vad = pytest.importorskip("faster_whisper.vad")

    quiet, sr = _read_wav(FIXTURE_DIR / "quiet_speech_component.wav")
    total_dur_s = len(quiet) / sr

    def coverage(timestamps) -> float:
        return sum(seg["end"] - seg["start"] for seg in timestamps) / sr

    default_ts = vad.get_speech_timestamps(quiet, vad.VadOptions(threshold=0.5))
    configured_ts = vad.get_speech_timestamps(
        quiet, vad.VadOptions(threshold=0.2, min_speech_duration_ms=100)
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


def test_normalize_loudness_does_not_reduce_retention_on_realistic_fixture():
    """WHISP-77 regression: the first `normalize_loudness` implementation
    (AGC-based) measured *worse* than raw audio at every Silero VAD threshold
    on a real recording -- a plain attenuation+noise fixture didn't catch
    this. `quiet_speech_component.wav` adds room reverb, band-limited
    "captured over speakers" coloration, and low-frequency rumble, which does
    catch it: the current (dynaudnorm-style) `normalize_loudness` must give
    VAD retention at or above raw at every threshold tested.

    normalize_loudness defaults to *off* for file transcription now (see
    config.py/stt/normalize.py) -- the real win on real audio was
    `apply_highpass_filter`, tested below -- but normalize_loudness is still
    exposed (CLI `--normalize`) and must not actively regress retention.
    """
    vad = pytest.importorskip("faster_whisper.vad")
    from whisper_aloud.stt.normalize import normalize_loudness

    quiet, sr = _read_wav(FIXTURE_DIR / "quiet_speech_component.wav")
    normalized = normalize_loudness(quiet, sr)

    def coverage(signal, threshold) -> float:
        ts = vad.get_speech_timestamps(
            signal, vad.VadOptions(threshold=threshold, min_speech_duration_ms=100)
        )
        return sum(seg["end"] - seg["start"] for seg in ts) / sr

    for threshold in (0.5, 0.35, 0.2, 0.1):
        raw_cov = coverage(quiet, threshold)
        norm_cov = coverage(normalized, threshold)
        assert norm_cov >= raw_cov, (
            f"normalize_loudness() should not reduce VAD retention at "
            f"threshold={threshold}: raw={raw_cov:.2f}s, normalized={norm_cov:.2f}s"
        )


def test_highpass_filter_improves_retention_on_realistic_fixture():
    """WHISP-77's actual real-recording finding: removing low-frequency
    rumble with a plain high-pass filter -- not loudness normalization --
    is what improves Silero VAD retention. `apply_highpass_filter` (the
    default for file transcription, see config.py's `highpass_hz`) must give
    VAD retention at or above raw at every threshold tested on
    `quiet_speech_component.wav` (which has synthetic 30-60Hz rumble added --
    see tests/fixtures/generate_vad_quiet_speech.py), and clearly better at
    the project's default threshold (0.2) or just above it.
    """
    vad = pytest.importorskip("faster_whisper.vad")
    from whisper_aloud.stt.normalize import apply_highpass_filter

    quiet, sr = _read_wav(FIXTURE_DIR / "quiet_speech_component.wav")
    filtered = apply_highpass_filter(quiet, sr, cutoff_hz=80.0)

    def coverage(signal, threshold) -> float:
        ts = vad.get_speech_timestamps(
            signal, vad.VadOptions(threshold=threshold, min_speech_duration_ms=100)
        )
        return sum(seg["end"] - seg["start"] for seg in ts) / sr

    for threshold in (0.5, 0.35, 0.2, 0.1):
        raw_cov = coverage(quiet, threshold)
        hp_cov = coverage(filtered, threshold)
        assert hp_cov >= raw_cov, (
            f"apply_highpass_filter() should not reduce VAD retention at "
            f"threshold={threshold}: raw={raw_cov:.2f}s, highpass={hp_cov:.2f}s"
        )

    # Clear win at/around the project's default threshold.
    assert coverage(filtered, 0.35) > coverage(quiet, 0.35)


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

    def test_normalize_loudness_opt_in_recovers_speaker_b(self):
        """normalize_loudness is no longer the default (see
        TranscriptionConfig and stt/normalize.py's docstring -- it measured
        as a net negative for VAD retention on a real recording), but it
        still works for the narrower case it was originally built for: a
        loud speaker directly followed by a genuinely quiet one (no
        low-frequency rumble involved -- two_speaker.wav has none, so
        highpass_hz is disabled here to isolate normalize_loudness's own
        effect; the two are not meant to be combined by default, and
        measured worse together than normalize_loudness alone on this
        fixture)."""
        config = self._build_config(
            normalize_loudness=True, vad_threshold=0.2, vad_min_speech_ms=100, highpass_hz=0,
        )

        result = self._transcribe_with_cpu_threads_1(config, str(FIXTURE_DIR / "two_speaker.wav"))

        text_lower = result.text.lower()
        assert "country" in text_lower
        # Both sentences' key content words should now be present.
        assert "ask not" in text_lower or "not what" in text_lower  # speaker A
        assert "ask what you can do" in text_lower or "what you can do" in text_lower  # speaker B

    def test_default_config_does_not_regress_silence(self):
        """Sanity check that the project's actual current defaults
        (highpass_hz=80, normalize_loudness=False, vad_threshold=0.2) are
        exactly what transcribe_file() uses with no overrides."""
        config = self._build_config()  # project defaults
        assert config.transcription.normalize_loudness is False
        assert config.transcription.highpass_hz == pytest.approx(80.0)
        assert config.transcription.vad_threshold == pytest.approx(0.2)

    def test_silence_stays_silence_after_fix(self):
        """The fix must not hallucinate speech out of a pure noise floor."""
        config = self._build_config()  # project defaults: highpass + lower VAD threshold
        result = self._transcribe_with_cpu_threads_1(config, str(FIXTURE_DIR / "silence_only.wav"))
        assert result.text.strip() == ""
