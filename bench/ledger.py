"""Append-only JSONL results ledger. One row per (engine,model,lang,device,hw) run."""
from __future__ import annotations

import datetime
import json
import os

BENCH_VERSION = 1


def make_row(*, lang, dataset, n_clips, engine, model, compute_type, device,
             hw, metrics, git_sha, notes="", ts=None):
    """Build one ledger row. `ts` defaults to current UTC (pass explicitly in tests)."""
    if ts is None:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ts": ts, "git_sha": git_sha, "bench_version": BENCH_VERSION,
        "lang": lang, "dataset": dataset, "n_clips": n_clips,
        "engine": engine, "model": model, "compute_type": compute_type, "device": device,
        "hw": hw, "metrics": metrics, "notes": notes,
    }


def append_row(path, row) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path) -> list:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
