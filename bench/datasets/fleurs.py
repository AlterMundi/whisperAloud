"""Fetch + normalize FLEURS clips to 16kHz mono WAV and write a manifest.

Avoids torchcodec (heavy) by disabling datasets auto-decode and decoding bytes
with soundfile. Audio goes to bench/datasets/audio/<lang>/, references to
bench/datasets/manifests/<lang>.jsonl (manifest is committed; audio is gitignored).

Usage: python -m bench.datasets.fleurs --lang es,en --n 40
"""
from __future__ import annotations

import argparse
import io
import json
import os

import numpy as np
import soundfile as sf

HERE = os.path.dirname(__file__)
AUDIO_ROOT = os.path.join(HERE, "audio")
MANIFEST_ROOT = os.path.join(HERE, "manifests")

# FLEURS config name per language code
FLEURS_CONFIG = {"es": "es_419", "en": "en_us", "fr": "fr_fr",
                 "de": "de_de", "pt": "pt_br", "it": "it_it"}


def fetch_lang(lang: str, n: int, min_s: float = 4.0, max_s: float = 30.0) -> int:
    from datasets import Audio, load_dataset
    config = FLEURS_CONFIG[lang]
    out_dir = os.path.join(AUDIO_ROOT, lang)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(MANIFEST_ROOT, exist_ok=True)
    print(f"[{lang}] loading FLEURS {config} (test, streaming, no-decode)...", flush=True)
    ds = load_dataset("google/fleurs", config, split="test", streaming=True)
    ds = ds.cast_column("audio", Audio(decode=False))

    rows = []
    for ex in ds:
        if len(rows) >= n:
            break
        audio = ex["audio"]
        raw = audio.get("bytes")
        if raw is None and audio.get("path") and os.path.exists(audio["path"]):
            with open(audio["path"], "rb") as fh:
                raw = fh.read()
        if raw is None:
            continue
        arr, sr = sf.read(io.BytesIO(raw), dtype="float32")
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        if sr != 16000:
            import scipy.signal as ss
            arr = ss.resample(arr, int(len(arr) * 16000 / sr)).astype(np.float32)
            sr = 16000
        dur = len(arr) / sr
        if dur < min_s or dur > max_s:
            continue
        fn = f"{lang}_{len(rows):02d}.wav"
        sf.write(os.path.join(out_dir, fn), arr, sr)
        ref = ex.get("transcription") or ex.get("raw_transcription") or ""
        rows.append({"file": fn, "duration_s": round(dur, 2), "ref": ref})

    manifest = os.path.join(MANIFEST_ROOT, f"{lang}.jsonl")
    with open(manifest, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[{lang}] wrote {len(rows)} clips -> {out_dir} ; manifest -> {manifest}")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="es,en")
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()
    for lang in args.lang.split(","):
        fetch_lang(lang.strip(), args.n)


if __name__ == "__main__":
    main()
