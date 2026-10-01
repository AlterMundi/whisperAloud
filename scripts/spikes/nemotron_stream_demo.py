#!/usr/bin/env python3
"""WHISP-55 spike demo: stream a Spanish WAV through the sherpa-onnx export of
NVIDIA Nemotron 3.5 ASR Streaming 0.6B (560 ms chunk variant) and report
partial hypotheses, real-time factor (RTF) and peak RSS.

This is a throwaway spike script, not part of the shipped application. It
expects:
  - a venv with `sherpa-onnx` installed (see docs/superpowers/specs/
    2026-10-01-whisp55-nemotron-sherpa-findings.md for exact version)
  - the 560ms int8 ONNX export extracted from the official sherpa-onnx
    "asr-models" GitHub release asset, e.g. under .spike-models/ (gitignored)
  - a 16 kHz mono WAV file of Spanish speech

Usage:
    OMP_NUM_THREADS=2 python scripts/spikes/nemotron_stream_demo.py \\
        --model-dir .spike-models/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11 \\
        --wav .spike-models/platero_07_16k.wav \\
        --language es \\
        --chunk-ms 560
"""

from __future__ import annotations

import argparse
import resource
import time
import wave
from pathlib import Path

try:
    import sherpa_onnx
except ImportError as exc:  # pragma: no cover - spike-only dependency
    raise SystemExit(
        "sherpa-onnx is not installed in this interpreter. Create a throwaway "
        "venv and `pip install sherpa-onnx` before running this spike script."
    ) from exc


def load_wav_mono16k(path: Path) -> tuple[list[float], int]:
    """Read a 16-bit PCM mono 16kHz WAV into a list of floats in [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        if wf.getframerate() != 16000:
            raise ValueError(f"expected 16kHz WAV, got {wf.getframerate()} Hz")
        if wf.getnchannels() != 1:
            raise ValueError(f"expected mono WAV, got {wf.getnchannels()} channels")
        if wf.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM WAV, got {wf.getsampwidth() * 8}-bit")
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    import array

    samples = array.array("h")
    samples.frombytes(raw)
    return [s / 32768.0 for s in samples], 16000


def build_recognizer(model_dir: Path, language: str) -> "sherpa_onnx.OnlineRecognizer":
    # NOTE: `language` is not accepted by from_transducer() itself; it is set
    # per-stream below via stream.set_config(). Kept as a parameter here so the
    # call site reads naturally and in case a future sherpa-onnx version grows
    # a recognizer-level default.
    del language
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(model_dir / "tokens.txt"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        joiner=str(model_dir / "joiner.int8.onnx"),
        sample_rate=16000,
        feature_dim=128,
        model_type="nemotron",
        decoding_method="greedy_search",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--language", default="es")
    parser.add_argument("--chunk-ms", type=int, default=560)
    args = parser.parse_args()

    samples, sample_rate = load_wav_mono16k(args.wav)
    audio_duration_s = len(samples) / sample_rate

    recognizer = build_recognizer(args.model_dir, args.language)
    stream = recognizer.create_stream()
    # Per-stream language hint, per the model README ("en", "ja", "auto", ...).
    # set_option(key, value) is the real API (confirmed against the installed
    # sherpa-onnx build); an earlier draft of this script guessed a JSON
    # set_config() call that does not exist and silently no-opped.
    stream.set_option("language", args.language)

    chunk_size = int(sample_rate * args.chunk_ms / 1000)
    partials: list[str] = []

    t_start = time.monotonic()
    for i in range(0, len(samples), chunk_size):
        chunk = samples[i : i + chunk_size]
        stream.accept_waveform(sample_rate, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = recognizer.get_result(stream)
        if text and (not partials or text != partials[-1]):
            partials.append(text)
            print(f"[partial @ {i / sample_rate:6.2f}s] {text}")

    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    final_text = recognizer.get_result(stream)
    t_end = time.monotonic()

    wall_s = t_end - t_start
    rtf = wall_s / audio_duration_s if audio_duration_s else float("nan")
    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print("\n--- summary ---")
    print(f"audio duration:  {audio_duration_s:.2f} s")
    print(f"wall time:       {wall_s:.2f} s")
    print(f"RTF:             {rtf:.3f}")
    print(f"peak RSS:        {peak_rss_kb / 1024:.1f} MB")
    print(f"final text:      {final_text}")


if __name__ == "__main__":
    main()
