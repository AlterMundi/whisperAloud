"""Logic tests for HistoryPanel behavior without GTK runtime."""

from datetime import datetime, timedelta

from whisper_aloud.persistence.models import HistoryEntry
from whisper_aloud.ui.history_panel_logic import (
    filter_entries_by_query,
    group_entries_by_date,
    resolve_history_query_mode,
)


def _entry(text: str, ts: datetime | None):
    return HistoryEntry(
        id=1,
        text=text,
        language="en",
        confidence=0.9,
        duration=1.0,
        processing_time=0.1,
        segments=[],
        timestamp=ts,
    )


def test_resolve_history_query_mode():
    assert resolve_history_query_mode("hello", False) == ("search", "hello")
    assert resolve_history_query_mode("  hello  ", True) == ("favorites", "hello")
    assert resolve_history_query_mode("   ", False) == ("recent", "")


def test_filter_entries_by_query():
    entries = [
        _entry("Hello world", datetime(2026, 2, 20, 10, 0, 0)),
        _entry("Other text", datetime(2026, 2, 20, 11, 0, 0)),
    ]
    filtered = filter_entries_by_query(entries, "hello")
    assert len(filtered) == 1
    assert filtered[0].text == "Hello world"


def test_group_entries_by_date_labels():
    today = datetime(2026, 2, 20, 12, 0, 0)
    entries = [
        _entry("today", today),
        _entry("yesterday", today - timedelta(days=1)),
        _entry("older", datetime(2026, 2, 10, 9, 0, 0)),
        _entry("unknown", None),
    ]

    grouped = group_entries_by_date(entries, today=today.date())
    assert "Today" in grouped
    assert "Yesterday" in grouped
    assert "February 10, 2026" in grouped
    assert "Unknown Date" in grouped
