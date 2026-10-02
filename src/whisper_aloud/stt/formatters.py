"""Output formatters for `whisper-aloud transcribe --format` (WHISP-76).

Pure functions over a plain `Segment` list -- no engine/CLI coupling, so they are
unit-testable on their own. Blank-text segments (common for chunk boundaries that
produced no speech) are silently skipped by every formatter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class Segment:
    """One timestamped span of text. `start`/`end` are absolute seconds."""

    start: float
    end: float
    text: str


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _timestamp(seconds: float, decimal_sep: str) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{decimal_sep}{ms:03d}"


def _srt_timestamp(seconds: float) -> str:
    return _timestamp(seconds, ",")


def _vtt_timestamp(seconds: float) -> str:
    return _timestamp(seconds, ".")


def _non_blank(segments: list[Segment]) -> list[Segment]:
    return [s for s in segments if s.text.strip()]


def format_txt(segments: list[Segment], timestamps: bool = False) -> str:
    """Plain text (backward compatible with the pre-WHISP-76 `transcribe` output
    when `timestamps=False`), or one `[hh:mm:ss - hh:mm:ss] text` line per
    segment when `timestamps=True`."""
    segs = _non_blank(segments)
    if not segs:
        return ""
    if not timestamps:
        return " ".join(s.text.strip() for s in segs) + "\n"
    lines = [f"[{_hms(s.start)} - {_hms(s.end)}] {s.text.strip()}" for s in segs]
    return "\n".join(lines) + "\n"


def format_srt(segments: list[Segment]) -> str:
    """SubRip (.srt) output."""
    segs = _non_blank(segments)
    if not segs:
        return ""
    blocks = [
        f"{i}\n{_srt_timestamp(s.start)} --> {_srt_timestamp(s.end)}\n{s.text.strip()}\n"
        for i, s in enumerate(segs, start=1)
    ]
    return "\n".join(blocks)


def format_vtt(segments: list[Segment]) -> str:
    """WebVTT output. Always has the `WEBVTT` header, even with no segments."""
    segs = _non_blank(segments)
    if not segs:
        return "WEBVTT\n"
    lines = ["WEBVTT", ""]
    for s in segs:
        lines.append(f"{_vtt_timestamp(s.start)} --> {_vtt_timestamp(s.end)}")
        lines.append(s.text.strip())
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def format_json(segments: list[Segment], meta: dict | None = None) -> str:
    """JSON output: `{"text": ..., "segments": [...]}` plus any extra `meta` keys."""
    segs = _non_blank(segments)
    payload = {
        "text": " ".join(s.text.strip() for s in segs),
        "segments": [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs],
    }
    if meta:
        payload.update(meta)
    return json.dumps(payload, indent=2) + "\n"
