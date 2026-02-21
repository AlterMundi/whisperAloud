"""Pure logic helpers for HistoryPanel behavior."""

from datetime import date, datetime
from typing import Iterable


def resolve_history_query_mode(
    query: str,
    favorites_only: bool,
) -> tuple[str, str]:
    """Resolve query mode and normalized text for HistoryPanel fetch operations."""
    normalized_query = (query or "").strip()
    if favorites_only:
        return "favorites", normalized_query
    if normalized_query:
        return "search", normalized_query
    return "recent", ""


def filter_entries_by_query(entries: Iterable, query: str) -> list:
    """Filter entries by text query (case-insensitive)."""
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return list(entries)
    return [
        entry
        for entry in entries
        if normalized_query in (getattr(entry, "text", "") or "").lower()
    ]


def group_entries_by_date(
    entries: Iterable,
    today: date | None = None,
) -> dict[str, list]:
    """Group entries into Today/Yesterday/date labels."""
    current_day = today or datetime.now().date()
    grouped: dict[str, list] = {}

    for entry in entries:
        timestamp = getattr(entry, "timestamp", None)
        if not timestamp:
            key = "Unknown Date"
        else:
            entry_date = timestamp.date()
            if entry_date == current_day:
                key = "Today"
            elif (current_day - entry_date).days == 1:
                key = "Yesterday"
            else:
                key = entry_date.strftime("%B %d, %Y")

        grouped.setdefault(key, []).append(entry)

    return grouped
