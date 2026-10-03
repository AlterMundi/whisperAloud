"""Tests for scripts/field_report.py, the field-validation checklist parser.

Pure text parsing, no GTK/D-Bus/daemon involved -- loaded directly from its
script path since `scripts/` isn't an installed package.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "field_report.py"


def _load_field_report():
    spec = importlib.util.spec_from_file_location("field_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses.dataclass() looks the defining module up in sys.modules
    # (to resolve string type annotations); register it first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


field_report = _load_field_report()


SAMPLE_CHECKLIST = """\
# Sample checklist

Some intro text with a | pipe character that is not a table row.

| ID | Expected behaviour | Steps | Expected result | Ticket(s) | Result |
|---|---|---|---|---|---|
| FV-001 | Does a thing | Do step 1 | It works | WHISP-1 | ✅ |
| FV-002 | Does another thing | Do step 2 | It also works | WHISP-2 | ❌ broke on Wayland |
| FV-003 | Risky thing | Do step 3 | Should degrade | WHISP-3 | ⚠️ paste delayed ~2s |
| FV-004 | Not run yet | Do step 4 | TBD | WHISP-4 | |
| FV-005 | Unrecognized marker | Do step 5 | TBD | WHISP-5 | maybe? |
| FV-006 **[FLATPAK-RISK]** | Risk-tagged id cell | Do step 6 | TBD | WHISP-6 | ✅ |

## Coverage summary

| Area | Items |
|---|---|
| Sample area | 5 |
"""


def test_parse_checklist_finds_all_fv_rows():
    items = field_report.parse_checklist(SAMPLE_CHECKLIST)
    ids = [item.item_id for item in items]
    assert ids == ["FV-001", "FV-002", "FV-003", "FV-004", "FV-005", "FV-006"]


def test_parse_checklist_ignores_non_item_rows():
    items = field_report.parse_checklist(SAMPLE_CHECKLIST)
    # The coverage-summary table and header/separator rows must not leak in.
    assert all(item.item_id.startswith("FV-") for item in items)
    assert len(items) == 6


def test_parse_checklist_strips_risk_tag_from_id_cell():
    """An ID cell like 'FV-006 **[FLATPAK-RISK]**' still yields a clean id."""
    items = {item.item_id: item for item in field_report.parse_checklist(SAMPLE_CHECKLIST)}
    assert "FV-006" in items
    assert items["FV-006"].status == field_report.STATUS_PASS


@pytest.mark.parametrize(
    "raw,expected_status,expected_note",
    [
        ("✅", field_report.STATUS_PASS, ""),
        ("❌ broke on Wayland", field_report.STATUS_FAIL, "broke on Wayland"),
        ("⚠️ paste delayed ~2s", field_report.STATUS_WARN, "paste delayed ~2s"),
        ("", field_report.STATUS_PENDING, ""),
        ("maybe?", field_report.STATUS_WARN, "maybe?"),
    ],
)
def test_classify_and_extract_note(raw, expected_status, expected_note):
    assert field_report.classify_result(raw) == expected_status
    assert field_report.extract_note(raw) == expected_note


def test_parse_checklist_statuses_match_sample():
    items = {item.item_id: item for item in field_report.parse_checklist(SAMPLE_CHECKLIST)}
    assert items["FV-001"].status == field_report.STATUS_PASS
    assert items["FV-002"].status == field_report.STATUS_FAIL
    assert items["FV-002"].note == "broke on Wayland"
    assert items["FV-003"].status == field_report.STATUS_WARN
    assert items["FV-004"].status == field_report.STATUS_PENDING
    assert items["FV-005"].status == field_report.STATUS_WARN


def test_render_report_counts_and_lists_flagged_items():
    items = field_report.parse_checklist(SAMPLE_CHECKLIST)
    report = field_report.render_report(items)

    assert "6 items" in report
    assert "pass: 2" in report
    assert "fail: 1" in report
    assert "warn: 2" in report
    assert "pending" in report

    # Only fail/warn items are listed individually, each with its note.
    assert "FV-002" in report
    assert "broke on Wayland" in report
    assert "FV-003" in report
    assert "paste delayed ~2s" in report
    assert "FV-005" in report
    assert "FV-001" not in report  # a clean pass isn't called out individually
    assert "FV-004" not in report  # pending isn't called out individually


def test_main_reads_default_checklist_and_exits_zero(capsys):
    rc = field_report.main(["field_report.py"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Field validation report" in out
    assert "FV-001" not in out  # none of the real items are flagged yet (all pending)


def test_main_reports_missing_file(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.md"
    rc = field_report.main(["field_report.py", str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not read" in err


def test_real_checklist_has_no_unrecognized_or_duplicate_ids():
    """Sanity check against the actual 0.3.0 checklist this script ships with."""
    text = field_report.DEFAULT_CHECKLIST.read_text(encoding="utf-8")
    items = field_report.parse_checklist(text)

    assert len(items) >= 40  # brief asked for ~40-70 items
    ids = [item.item_id for item in items]
    assert len(ids) == len(set(ids)), "duplicate FV ids found"

    # A freshly authored checklist should have every item still pending.
    assert all(item.status == field_report.STATUS_PENDING for item in items)
