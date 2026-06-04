"""Render the results ledger into MATRIX.md. Pure function of the ledger rows."""
from __future__ import annotations

_HEADER = "# STT Benchmark Matrix\n\n"
_CAVEAT = (
    "> Generated from `bench/results/ledger.jsonl` by `python -m bench.report`. "
    "FLEURS is read-speech: absolute WER is optimistic vs real mic dictation, but "
    "relative ordering is reliable. GPU numbers are machine-specific (see `hw.id`).\n\n"
)
_COLS = ["lang", "engine", "model", "compute", "device", "hw.id",
         "WER %", "RTF", "RAM MB", "VRAM MB", "size MB"]


def _fmt(v):
    return "—" if v is None else v


def generate_matrix(rows: list) -> str:
    if not rows:
        return _HEADER + _CAVEAT + "_No results yet. Run `python -m bench.runner`._\n"
    lines = [_HEADER, _CAVEAT, "| " + " | ".join(_COLS) + " |",
             "|" + "|".join(["---"] * len(_COLS)) + "|"]
    # sort for stable output: lang, then WER ascending
    for r in sorted(rows, key=lambda r: (r["lang"], r["metrics"].get("wer") or 1.0)):
        m = r["metrics"]
        wer_pct = f"{m['wer'] * 100:.1f}" if m.get("wer") is not None else "—"
        cells = [
            r["lang"], r["engine"], r["model"], r.get("compute_type", "—"),
            r["device"], r["hw"].get("id", "—"),
            wer_pct,
            f"{m.get('rtf'):.3f}" if m.get("rtf") is not None else "—",
            _fmt(m.get("peak_rss_mb")), _fmt(m.get("peak_vram_mb")),
            _fmt(m.get("model_size_mb")),
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def main():
    import os
    from bench.ledger import read_rows
    here = os.path.dirname(__file__)
    rows = read_rows(os.path.join(here, "results", "ledger.jsonl"))
    md = generate_matrix(rows)
    with open(os.path.join(here, "MATRIX.md"), "w") as f:
        f.write(md)
    print(f"Wrote MATRIX.md with {len(rows)} rows")


if __name__ == "__main__":
    main()
