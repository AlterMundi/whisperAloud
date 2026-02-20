"""Daemon-backed history adapter for UI consumers."""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..persistence.models import HistoryEntry

if TYPE_CHECKING:
    from .client import WhisperAloudClient

logger = logging.getLogger(__name__)


def _unpack_variant(value: Any) -> Any:
    """Return raw value from GLib.Variant-like objects."""
    if hasattr(value, "unpack"):
        try:
            return value.unpack()
        except Exception:
            return value
    return value


def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Convert value to float with fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class DaemonHistoryManager:
    """History manager facade that sources data from D-Bus daemon methods."""

    def __init__(self, client: "WhisperAloudClient"):
        self.client = client

    def get_recent(self, limit: int | None = 50) -> list[HistoryEntry]:
        """Return recent history entries via daemon."""
        safe_limit = 50 if limit is None else max(1, int(limit))
        return self._decode_entries(self.client.get_history(limit=safe_limit))

    def search(self, query: str, limit: int = 50) -> list[HistoryEntry]:
        """Search history entries via daemon."""
        safe_limit = max(1, int(limit))
        return self._decode_entries(
            self.client.search_history((query or "").strip(), limit=safe_limit)
        )

    def get_favorites(self, limit: int = 50) -> list[HistoryEntry]:
        """Return favorite entries via daemon."""
        safe_limit = max(1, int(limit))
        return self._decode_entries(self.client.get_favorite_history(limit=safe_limit))

    def toggle_favorite(self, entry_id: int) -> bool:
        """Toggle favorite status for one entry."""
        return self.client.toggle_history_favorite(int(entry_id))

    def delete(self, entry_id: int) -> bool:
        """Delete one history entry."""
        return self.client.delete_history_entry(int(entry_id))

    def export_json(self, entries: list[HistoryEntry], path: Path) -> None:
        """Export entries to JSON."""
        data = [entry.to_dict() for entry in entries]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def export_markdown(self, entries: list[HistoryEntry], path: Path) -> None:
        """Export entries to Markdown."""
        lines = ["# WhisperAloud Transcription History", ""]

        by_date: dict[str, list[HistoryEntry]] = {}
        for entry in entries:
            date_str = entry.timestamp.strftime("%Y-%m-%d") if entry.timestamp else "Unknown"
            by_date.setdefault(date_str, []).append(entry)

        for date_str in sorted(by_date.keys(), reverse=True):
            lines.append(f"## {date_str}")
            lines.append("")
            for entry in by_date[date_str]:
                time_str = entry.timestamp.strftime("%H:%M:%S") if entry.timestamp else "??:??:??"
                lines.append(f"### {time_str} - {(entry.language or '').upper()}")
                lines.append("")

                metadata = [
                    f"**Confidence:** {int(_coerce_float(entry.confidence) * 100)}%",
                    f"**Duration:** {_coerce_float(entry.duration):.1f}s",
                    f"**Processing:** {_coerce_float(entry.processing_time):.2f}s",
                ]
                if entry.favorite:
                    metadata.append("**Favorite**")
                if entry.tags:
                    metadata.append(f"**Tags:** {', '.join(entry.tags)}")
                lines.append(" | ".join(metadata))
                lines.append("")

                text = entry.text or ""
                lines.append("> " + text.replace("\n", "\n> "))
                lines.append("")
                if entry.notes:
                    lines.append("**Notes:**")
                    lines.append(entry.notes)
                    lines.append("")
                lines.append("---")
                lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def export_csv(self, entries: list[HistoryEntry], path: Path) -> None:
        """Export entries to CSV."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "ID",
                    "Timestamp",
                    "Text",
                    "Language",
                    "Confidence",
                    "Duration",
                    "Processing Time",
                    "Tags",
                    "Notes",
                    "Favorite",
                    "Session ID",
                ]
            )
            for entry in entries:
                writer.writerow(
                    [
                        entry.id,
                        entry.timestamp.isoformat() if entry.timestamp else "",
                        entry.text,
                        entry.language,
                        entry.confidence,
                        entry.duration,
                        entry.processing_time,
                        ",".join(entry.tags) if entry.tags else "",
                        entry.notes,
                        entry.favorite,
                        entry.session_id or "",
                    ]
                )

    def export_text(self, entries: list[HistoryEntry], path: Path) -> None:
        """Export entries to plain text."""
        lines: list[str] = []
        for entry in entries:
            timestamp_str = (
                entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if entry.timestamp else "Unknown"
            )
            lines.append(f"[{timestamp_str}] {(entry.language or '').upper()}")
            lines.append(entry.text or "")
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _decode_entries(self, payload: Any) -> list[HistoryEntry]:
        """Decode daemon history payload into HistoryEntry objects."""
        if not isinstance(payload, list):
            logger.warning("Unexpected history payload type: %s", type(payload))
            return []
        decoded: list[HistoryEntry] = []
        for raw_entry in payload:
            if not isinstance(raw_entry, Mapping):
                continue
            decoded.append(self._decode_entry(raw_entry))
        return decoded

    def _decode_entry(self, raw: Mapping[str, Any]) -> HistoryEntry:
        """Convert one daemon history dict into HistoryEntry."""
        unpacked = {k: _unpack_variant(v) for k, v in raw.items()}

        timestamp = datetime.now()
        raw_timestamp = unpacked.get("timestamp")
        if raw_timestamp:
            try:
                timestamp = datetime.fromisoformat(str(raw_timestamp))
            except (TypeError, ValueError):
                logger.debug("Invalid timestamp from daemon: %r", raw_timestamp)

        tags_value = unpacked.get("tags", [])
        if isinstance(tags_value, list):
            tags = [str(tag) for tag in tags_value]
        else:
            tags = []

        entry = HistoryEntry(
            text=str(unpacked.get("text", "") or ""),
            language=str(unpacked.get("language", "") or ""),
            confidence=_coerce_float(unpacked.get("confidence")),
            duration=_coerce_float(unpacked.get("duration")),
            processing_time=_coerce_float(unpacked.get("processing_time")),
            segments=[],
            id=int(unpacked.get("id", 0) or 0),
            timestamp=timestamp,
            favorite=bool(unpacked.get("favorite", False)),
            notes=str(unpacked.get("notes", "") or ""),
            tags=tags,
        )
        return entry
