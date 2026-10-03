#!/usr/bin/env python3
"""Summarize a field-validation checklist's Result column.

Parses the Markdown table rows of a checklist such as
`docs/validation/0.3.0-field-checklist.md` (one row per `FV-NNN` item, with
the last cell holding a `✅` / `❌` / `⚠️ <note>` / empty result) and prints a
per-status count plus the list of failing/warning items with their notes.

No GTK/D-Bus/daemon dependency -- pure text parsing, safe to run anywhere,
including CI, without touching the real desktop.

Usage:
    python scripts/field_report.py [path/to/checklist.md]

Defaults to `docs/validation/0.3.0-field-checklist.md` relative to the repo
root (two directories up from this file).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKLIST = REPO_ROOT / "docs" / "validation" / "0.3.0-field-checklist.md"

ID_PATTERN = re.compile(r"^(FV-\d+)\b")

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARN = "warn"
STATUS_PENDING = "pending"

STATUS_LABELS = {
    STATUS_PASS: "✅ pass",
    STATUS_FAIL: "❌ fail",
    STATUS_WARN: "⚠️ warn",
    STATUS_PENDING: "⬜ pending (not yet run)",
}

# Order in which the summary is printed.
STATUS_ORDER = [STATUS_PASS, STATUS_FAIL, STATUS_WARN, STATUS_PENDING]


@dataclass(frozen=True)
class ChecklistItem:
    """One parsed checklist row."""

    item_id: str
    status: str
    note: str


def split_table_row(line: str) -> List[str]:
    """Split a Markdown table row into its cell strings.

    `| a | b | c |` -> `["a", "b", "c"]`. Returns `[]` for a line that isn't
    a table row (doesn't start and end with `|`).
    """
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    # Drop the leading/trailing empty cells produced by the boundary pipes.
    cells = stripped.split("|")[1:-1]
    return [cell.strip() for cell in cells]


def classify_result(raw: str) -> str:
    """Map a raw Result cell to one of the STATUS_* constants."""
    raw = raw.strip()
    if not raw:
        return STATUS_PENDING
    if raw.startswith("✅"):
        return STATUS_PASS
    if raw.startswith("❌"):
        return STATUS_FAIL
    if raw.startswith("⚠️") or raw.startswith("⚠"):
        return STATUS_WARN
    # Unrecognized content in the Result column is treated as a warning
    # rather than silently ignored or silently "passed".
    return STATUS_WARN


def extract_note(raw: str) -> str:
    """Strip the leading status glyph(s) off a Result cell, leaving the note."""
    raw = raw.strip()
    for glyph in ("✅", "❌", "⚠️", "⚠"):
        if raw.startswith(glyph):
            return raw[len(glyph) :].strip(" -:")
    return raw


def parse_checklist(text: str) -> List[ChecklistItem]:
    """Parse every `FV-NNN` table row in a checklist document.

    A row is recognized as a checklist item when its first cell *starts*
    with `FV-\\d+` (so the "how to report a defect" template, the coverage
    summary table, and table header/separator rows are all skipped without
    special-casing their exact text, while an ID cell carrying a trailing
    risk tag like `FV-002 **[FLATPAK-RISK]**` is still recognized).
    """
    items: List[ChecklistItem] = []
    for line in text.splitlines():
        cells = split_table_row(line)
        if len(cells) < 2:
            continue
        match = ID_PATTERN.match(cells[0])
        if not match:
            continue
        result_raw = cells[-1]
        items.append(
            ChecklistItem(
                item_id=match.group(1),
                status=classify_result(result_raw),
                note=extract_note(result_raw),
            )
        )
    return items


def render_report(items: List[ChecklistItem]) -> str:
    """Render the human-readable summary used by `main()`."""
    counts = {status: 0 for status in STATUS_ORDER}
    for item in items:
        counts[item.status] += 1

    lines = [f"Field validation report ({len(items)} items)", ""]
    for status in STATUS_ORDER:
        lines.append(f"  {STATUS_LABELS[status]}: {counts[status]}")

    flagged = [item for item in items if item.status in (STATUS_FAIL, STATUS_WARN)]
    if flagged:
        lines.append("")
        lines.append("Needs attention:")
        for item in flagged:
            label = STATUS_LABELS[item.status]
            note = f" -- {item.note}" if item.note else ""
            lines.append(f"  {item.item_id} [{label}]{note}")

    return "\n".join(lines)


def main(argv: List[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CHECKLIST
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"field_report.py: error: could not read {path}: {e}", file=sys.stderr)
        return 1

    items = parse_checklist(text)
    if not items:
        print(f"field_report.py: warning: no FV-NNN rows found in {path}", file=sys.stderr)

    print(render_report(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
