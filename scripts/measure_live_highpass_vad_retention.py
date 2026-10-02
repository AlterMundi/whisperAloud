#!/usr/bin/env python3
"""WHISP-83: does the live AudioPipeline's new leading HighPass stage help
NoiseGate/VAD retention the same way WHISP-77's file-transcription highpass
did?

This is the measurement that decided AudioProcessingConfig.highpass_hz's
default: it does NOT -- see the note at the bottom of main()'s output. The
stage stays in (same implementation, same tests, available opt-in via
highpass_hz), but defaults to 0 (off) for live dictation rather than
mirroring the file path's 80Hz default.

Not a test -- a one-off measurement script, mirroring
tests/test_vad_quiet_speech.py's approach but through the *live* pipeline
(HighPass -> NoiseGate) instead of the file path's
stt.normalize.apply_highpass_filter. No network, no models beyond the Silero
VAD ONNX model bundled inside faster-whisper itself. Run manually:

    source ~/.venvs/whisper_aloud/bin/activate
    python scripts/measure_live_highpass_vad_retention.py

Uses tests/fixtures/vad_quiet_speech/quiet_speech_component.wav (real
speech + synthetic room coloration + 30-60Hz rumble -- see
tests/fixtures/generate_vad_quiet_speech.py), the same fixture WHISP-77 used
to catch the file-transcription-path regression.

Reports, before vs. after the live HighPass stage:
  1. NoiseGate's own open/closed decision (fraction of samples gated open) --
     this is the thing HighPass runs ahead of in the live pipeline, so it's
     the most direct "did this change anything" signal.
  2. Silero VAD retention (coverage) on the NoiseGate's *output* at several
     thresholds, same metric tests/test_vad_quiet_speech.py uses for the file
     path.
"""
import os
import wave

import numpy as np

from whisper_aloud.audio.audio_processor import HighPass, NoiseGate

FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..", "tests", "fixtures", "vad_quiet_speech", "quiet_speech_component.wav",
)


def _read_wav(path: str):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = w.readframes(n)
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0, sr


def _gate_open_fraction(gated: np.ndarray, raw: np.ndarray) -> float:
    """Approximate "gate open" as output samples that aren't ~silenced
    relative to input -- NoiseGate doesn't expose per-sample state publicly,
    so this proxies it from its effect (gain near zero -> gated closed)."""
    eps = 1e-6
    passed = np.abs(gated) > eps
    return float(np.mean(passed))


def _coverage(vad_mod, signal: np.ndarray, sr: int, threshold: float) -> float:
    ts = vad_mod.get_speech_timestamps(
        signal, vad_mod.VadOptions(threshold=threshold, min_speech_duration_ms=100)
    )
    return sum(seg["end"] - seg["start"] for seg in ts) / sr


def main() -> None:
    from faster_whisper import vad as vad_mod

    raw, sr = _read_wav(FIXTURE)
    total_dur_s = len(raw) / sr

    # --- Before: NoiseGate directly on raw audio ---
    gate_before = NoiseGate(threshold_db=-40.0)
    gated_before = gate_before.process(raw.copy(), sr)

    # --- After: HighPass -> NoiseGate ---
    hp = HighPass(cutoff_hz=80.0)
    filtered = hp.process(raw.copy(), sr)
    gate_after = NoiseGate(threshold_db=-40.0)
    gated_after = gate_after.process(filtered.copy(), sr)

    print(f"Fixture: {FIXTURE}")
    print(f"Duration: {total_dur_s:.2f}s @ {sr}Hz\n")

    print("NoiseGate open-sample fraction (proxy for gate decisions):")
    print(f"  before highpass: {_gate_open_fraction(gated_before, raw):.3f}")
    print(f"  after  highpass: {_gate_open_fraction(gated_after, raw):.3f}")
    print(
        "  (NoiseGate is an RMS/energy gate, not a spectral one -- on this\n"
        "   fixture its open/closed decision barely moves either way, because\n"
        "   broadband RMS stays above threshold with or without the rumble.\n"
        "   This is the live path's real architectural difference from the\n"
        "   file path: faster-whisper's file-path VAD filter is a spectral\n"
        "   Silero model, which rumble *does* confuse; NoiseGate does not use\n"
        "   Silero at all.)\n"
    )

    print("Silero VAD retention (coverage) on NoiseGate's output (highpass -> gate):")
    print(f"{'threshold':>10} | {'before':>8} | {'after':>8}")
    for threshold in (0.5, 0.35, 0.2, 0.1):
        before_cov = _coverage(vad_mod, gated_before, sr, threshold)
        after_cov = _coverage(vad_mod, gated_after, sr, threshold)
        print(f"{threshold:>10} | {before_cov:>7.2f}s | {after_cov:>7.2f}s")

    print(
        "\nFor comparison, Silero VAD retention on HighPass's output directly\n"
        "(no NoiseGate at all) vs. raw -- isolates the *causal* filter's own\n"
        "effect, the same comparison tests/test_vad_quiet_speech.py makes for\n"
        "the file path's *non-causal* (sosfiltfilt) apply_highpass_filter():"
    )
    print(f"{'threshold':>10} | {'raw':>8} | {'causal hp':>10}")
    for threshold in (0.5, 0.35, 0.22, 0.2, 0.18, 0.15, 0.1):
        raw_cov = _coverage(vad_mod, raw, sr, threshold)
        hp_cov = _coverage(vad_mod, filtered, sr, threshold)
        print(f"{threshold:>10} | {raw_cov:>7.2f}s | {hp_cov:>9.2f}s")
    print(
        "\nNote (important, see final report): unlike the file path's\n"
        "zero-phase sosfiltfilt, this *causal* sosfilt-based filter measured\n"
        "as a small net NEGATIVE for Silero retention at this fixture's\n"
        "0.18-0.22 threshold band specifically (full retention at 0.1 and\n"
        "below, zero at both at 0.25+) -- causal filtering's phase shift,\n"
        "not a cold-start transient (checked with both zero and\n"
        "steady-state-scaled initial zi, same result either way), is enough\n"
        "to tip Silero's decision on this short, borderline-quiet fixture.\n"
        "This does not contradict HighPass's own correctness (frequency\n"
        "response / chunk-invariance tests above are about the filter being\n"
        "a correct causal high-pass, not about Silero VAD specifically) --\n"
        "it means the live pipeline's architecture (NoiseGate, no Silero VAD\n"
        "stage at all) does not reproduce WHISP-77's file-path VAD-retention\n"
        "win one-for-one, and should not be assumed to without its own\n"
        "measurement, which is what this script is for."
    )


if __name__ == "__main__":
    main()
