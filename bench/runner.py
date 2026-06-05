"""Benchmark runner CLI.

Example:
  python -m bench.runner --lang es,en \
    --engines parakeet:<model_dir>,faster-whisper:small,faster-whisper:medium \
    --device auto --reps 3 --hw-id inference01
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time

import psutil
import soundfile as sf

from bench.engines import resolve
from bench.hwprobe import probe
from bench.ledger import append_row, make_row
from bench.wer import wer

HERE = os.path.dirname(__file__)
MANIFEST_ROOT = os.path.join(HERE, "datasets", "manifests")
AUDIO_ROOT = os.path.join(HERE, "datasets", "audio")
LEDGER = os.path.join(HERE, "results", "ledger.jsonl")


def median_rtf(values: list[float]) -> float:
    return round(statistics.median(values), 4)


def pick_device(caps_devices: list[str], hw_kind: str) -> str:
    """auto policy: cuda if engine supports it AND box has a GPU; else cpu.
    Non-cuda accelerators (vulkan/openvino/coral) are opt-in via --device, not auto."""
    if hw_kind == "gpu" and "cuda" in caps_devices:
        return "cuda"
    return "cpu"


def load_manifest(path) -> list:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _dirsize_mb(path) -> float | None:
    if os.path.isfile(path):
        return round(os.path.getsize(path) / 1e6, 1)
    if not os.path.isdir(path):
        return None
    total = sum(
        os.path.getsize(os.path.join(r, fn))
        for r, _, fs in os.walk(path)
        for fn in fs
    )
    return round(total / 1e6, 1)


def _proc_vram_mb() -> float | None:
    """Sum CUDA memory (MiB) used by THIS process via nvidia-smi compute-apps.

    Returns None if nvidia-smi is unavailable or this PID has no compute apps.
    """
    import os as _os
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    pid = _os.getpid()
    total = 0.0
    found = False
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) == pid:
            total += float(parts[1]); found = True
    return round(total, 1) if found else None


def run_combo(engine, model, lang, device, reps, hw, git_sha):
    manifest = os.path.join(MANIFEST_ROOT, f"{lang}.jsonl")
    audio_dir = os.path.join(AUDIO_ROOT, lang)
    clips = load_manifest(manifest)
    if not clips:
        raise FileNotFoundError(f"manifest empty or missing: {manifest}")
    t_load = time.time()
    engine.load(model, device, "int8")
    load_s = round(time.time() - t_load, 2)

    rep_times = []
    wers, total_audio = [], 0.0
    rss0 = psutil.Process().memory_info().rss / 1e6
    for rep in range(reps):
        t_rep = 0.0
        for c in clips:
            arr, sr = sf.read(os.path.join(audio_dir, c["file"]), dtype="float32")
            t = time.time()
            out = engine.transcribe_numpy(arr, sr, language=lang)
            t_rep += time.time() - t
            if rep == 0:
                wers.append(wer(c["ref"], out.text))
                total_audio += c["duration_s"]
        rep_times.append(t_rep)
    rss_peak = psutil.Process().memory_info().rss / 1e6
    peak_vram = _proc_vram_mb() if device == "cuda" else None

    # Integrity guard (spec §12): a cuda run that allocated no GPU memory actually
    # fell back to CPU (e.g. a CPU-only sherpa-onnx wheel). Refuse to record it as cuda.
    if device == "cuda" and not peak_vram:
        engine.unload()
        raise RuntimeError(
            f"{engine.name} requested cuda but used no GPU memory — CPU fallback "
            f"(install a GPU build of the engine). Refusing to mislabel as cuda."
        )

    metrics = {
        "wer": round(sum(wers) / len(wers), 4),
        "rtf": median_rtf([rt / total_audio for rt in rep_times]),
        "load_s": load_s,
        "peak_rss_mb": round(rss_peak, 1),
        "rss_delta_mb": round(rss_peak - rss0, 1),
        "peak_vram_mb": peak_vram,
        "model_size_mb": _dirsize_mb(model),
    }
    engine.unload()
    return make_row(
        lang=lang,
        dataset=f"fleurs/{lang}",
        n_clips=len(clips),
        engine=engine.name,
        model=os.path.basename(model.rstrip("/")) or model,
        compute_type="int8",
        device=device,
        hw=hw,
        metrics=metrics,
        git_sha=git_sha,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="es,en")
    ap.add_argument(
        "--engines",
        required=True,
        help="comma list of engine:model, e.g. parakeet:/path,faster-whisper:small",
    )
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "vulkan"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--hw-id", default=None)
    args = ap.parse_args()

    if args.reps < 1:
        ap.error("--reps must be >= 1")

    hw = probe(hw_id=args.hw_id)
    git_sha = _git_sha()
    print(f"hw: {hw}")
    for spec in args.engines.split(","):
        engine, model = resolve(spec.strip())
        for lang in args.lang.split(","):
            lang = lang.strip()
            device = (
                pick_device(engine.capabilities.devices, hw["kind"])
                if args.device == "auto"
                else args.device
            )
            try:
                row = run_combo(engine, model, lang, device, args.reps, hw, git_sha)
            except (FileNotFoundError, RuntimeError) as e:
                print(f"SKIP {spec} {lang}: {e}")
                continue
            append_row(LEDGER, row)
            print(
                f"[{row['engine']}:{row['model']} {lang}/{device}] "
                f"WER={row['metrics']['wer']:.1%} RTF={row['metrics']['rtf']:.3f}"
            )
    print(f"appended to {LEDGER}. Run `python -m bench.report` to regenerate MATRIX.md")


if __name__ == "__main__":
    main()
