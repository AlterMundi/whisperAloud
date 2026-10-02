"""High-level history management with optional audio archiving."""

import csv
import hashlib
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np

from ..config import PersistenceConfig
from .database import TranscriptionDatabase
from .models import HistoryEntry

logger = logging.getLogger(__name__)


class HistoryManager:
    """
    High-level history management with optional audio archiving.

    Thread-safe: All public methods can be called from background threads.
    """

    def __init__(
        self,
        config: PersistenceConfig,
        db: Optional[TranscriptionDatabase] = None
    ):
        """
        Initialize HistoryManager.

        Args:
            config: Persistence configuration
            db: Optional database instance (for testing/dependency injection)
        """
        self.config = config
        self.db = db if db else TranscriptionDatabase(config.db_path)

        # AudioArchive will be imported lazily if needed (Step 3)
        self.audio_archive = None
        if config.save_audio:
            try:
                from .audio_archive import AudioArchive
                self.audio_archive = AudioArchive(config.audio_archive_path)
                logger.info(f"Audio archiving enabled: {config.audio_archive_path}")
            except ImportError:
                logger.warning("AudioArchive not yet implemented, audio saving disabled")

        logger.info(f"HistoryManager initialized with database: {config.db_path}")

    def add_transcription(
        self,
        result,  # TranscriptionResult (avoiding circular import)
        audio: Optional[np.ndarray] = None,
        sample_rate: int = 16000,
        session_id: Optional[str] = None
    ) -> int:
        """
        Add transcription to history.

        IMPORTANT: This method performs heavy I/O (SHA256, FLAC encoding, disk writes, SQLite INSERT).
        Must be called from a BACKGROUND THREAD, never from the UI thread.

        Args:
            result: TranscriptionResult instance
            audio: Optional audio data to archive
            sample_rate: Audio sample rate
            session_id: Optional session grouping ID

        Returns:
            Entry ID
        """
        audio_path = None
        audio_hash = None

        # Archive audio if enabled and provided
        audio_saved = False
        if audio is not None and self.audio_archive:
            audio_hash = self._hash_audio(audio)

            # Save audio file (safe due to idempotent save)
            audio_path = self.audio_archive.save(
                audio,
                sample_rate,
                audio_hash
            )
            audio_saved = True  # Note: may be reused existing file
            logger.debug(f"Saved/reused audio file: {audio_path}")

        # Create entry from result
        entry = HistoryEntry.from_transcription_result(
            result,
            audio_path=audio_path,
            audio_hash=audio_hash,
            session_id=session_id
        )

        # Save to database with rollback on failure
        try:
            if self.config.deduplicate_audio and audio_hash:
                # Use atomic insert_or_get_by_hash to prevent race conditions
                entry_id, is_new = self.db.insert_or_get_by_hash(entry)
                if not is_new:
                    logger.info(f"Reused existing transcription {entry_id} for audio hash {audio_hash}")
                else:
                    logger.info(f"Added new transcription {entry_id}: {entry.text[:50]}...")
            else:
                entry_id = self.db.insert(entry)
                logger.info(f"Added transcription {entry_id}: {entry.text[:50]}...")
        except Exception:
            # Rollback: delete audio file if we saved a new one and it's not referenced
            if audio_saved and audio_path and self.audio_archive:
                ref_count = self.db.count_audio_references(str(audio_path))
                if ref_count == 0:
                    self.audio_archive.delete(audio_path)
                    logger.warning(f"Rolled back audio file after database error: {audio_path}")
            raise  # Re-raise the exception

        # NOTE: Auto-cleanup is NOT run here to avoid performance impact.
        # Instead, it should be run:
        # 1. On app startup (if enabled)
        # 2. Via a background maintenance task
        # 3. Manually by the user

        return entry_id

    def search(self, query: str, limit: int = 50) -> List[HistoryEntry]:
        """
        Full-text search.

        Args:
            query: Search query string
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        return self.db.search(query, limit)

    def query(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        favorites_only: bool = False,
        query: Optional[str] = None,
        limit: int = 50,
    ) -> List[HistoryEntry]:
        """
        Combined filter query (WHISP-79): since/until date range,
        favorites-only, and full-text search, any subset combinable.

        Args:
            since: Only entries with timestamp >= this (inclusive)
            until: Only entries with timestamp <= this (inclusive)
            favorites_only: Only entries marked as favorite
            query: Optional FTS search text
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances, newest first
        """
        return self.db.query_entries(
            since=since,
            until=until,
            favorites_only=favorites_only,
            query=query,
            limit=limit,
        )

    def get_recent(self, limit: Optional[int] = 50) -> List[HistoryEntry]:
        """
        Get recent transcriptions.

        Args:
            limit: Maximum number of results (default: 50, None for no limit)

        Returns:
            List of recent HistoryEntry instances
        """
        return self.db.get_all(limit=limit)

    def get_favorites(self, limit: int = 50) -> List[HistoryEntry]:
        """
        Get favorite transcriptions.

        Args:
            limit: Maximum number of results (default: 50)

        Returns:
            List of favorite HistoryEntry instances
        """
        return self.db.get_favorites(limit=limit)

    def get_by_session(self, session_id: str, limit: int = 50) -> List[HistoryEntry]:
        """
        Get all transcriptions from a specific session.

        Args:
            session_id: Session ID to filter by
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        return self.db.get_by_session(session_id, limit=limit)

    def get_by_id(self, entry_id: int) -> Optional[HistoryEntry]:
        """
        Get entry by ID.

        Args:
            entry_id: ID of entry to retrieve

        Returns:
            HistoryEntry if found, None otherwise
        """
        return self.db.get_by_id(entry_id)

    def toggle_favorite(self, entry_id: int) -> bool:
        """
        Toggle favorite status.

        Args:
            entry_id: ID of entry to toggle

        Returns:
            True if toggle succeeded, False if entry not found
        """
        entry = self.db.get_by_id(entry_id)
        if entry:
            success = self.db.update(entry_id, favorite=not entry.favorite)
            if success:
                logger.debug(f"Toggled favorite for entry {entry_id}")
            return success
        return False

    def update_notes(self, entry_id: int, notes: str) -> bool:
        """
        Update notes for an entry.

        Args:
            entry_id: ID of entry to update
            notes: New notes text

        Returns:
            True if update succeeded, False if entry not found
        """
        success = self.db.update(entry_id, notes=notes)
        if success:
            logger.debug(f"Updated notes for entry {entry_id}")
        return success

    def update_text(self, entry_id: int, text: str) -> bool:
        """
        Update the transcribed text of an entry.

        Args:
            entry_id: ID of entry to update
            text: New transcribed text

        Returns:
            True if update succeeded, False if entry not found
        """
        success = self.db.update(entry_id, text=text)
        if success:
            logger.debug(f"Updated text for entry {entry_id}")
        return success

    def add_tag(self, entry_id: int, tag: str) -> bool:
        """
        Add tag to entry.

        Args:
            entry_id: ID of entry
            tag: Tag to add

        Returns:
            True if tag was added, False if entry not found or tag already exists
        """
        entry = self.db.get_by_id(entry_id)
        if entry and tag not in entry.tags:
            tags = entry.tags + [tag]
            success = self.db.update(entry_id, tags=json.dumps(tags))
            if success:
                logger.debug(f"Added tag '{tag}' to entry {entry_id}")
            return success
        return False

    def remove_tag(self, entry_id: int, tag: str) -> bool:
        """
        Remove tag from entry.

        Args:
            entry_id: ID of entry
            tag: Tag to remove

        Returns:
            True if tag was removed, False if entry not found or tag doesn't exist
        """
        entry = self.db.get_by_id(entry_id)
        if entry and tag in entry.tags:
            tags = [t for t in entry.tags if t != tag]
            success = self.db.update(entry_id, tags=json.dumps(tags))
            if success:
                logger.debug(f"Removed tag '{tag}' from entry {entry_id}")
            return success
        return False

    def get_by_tag(self, tag: str, limit: int = 50) -> List[HistoryEntry]:
        """
        Get entries with specific tag.

        Args:
            tag: Tag to search for
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        return self.db.get_by_tag(tag, limit=limit)

    def delete(self, entry_id: int) -> bool:
        """
        Delete entry and optionally its audio file.

        Args:
            entry_id: ID of entry to delete

        Returns:
            True if deletion succeeded, False if entry not found
        """
        # Get entry to check for audio file
        entry = self.db.get_by_id(entry_id)

        # Delete from database
        success = self.db.delete(entry_id)

        # Delete audio file if present and not used by other entries
        if success and entry and entry.audio_file_path and self.audio_archive:
            # Check if any other entry uses this audio file
            ref_count = self.db.count_audio_references(str(entry.audio_file_path))
            if ref_count == 0:
                self.audio_archive.delete(entry.audio_file_path)
                logger.debug(f"Deleted audio file: {entry.audio_file_path}")

        if success:
            logger.info(f"Deleted entry {entry_id}")

        return success

    def cleanup_old(self, days: int, cleanup_audio: bool = False) -> int:
        """
        Delete old entries and optionally orphaned audio files.

        WARNING: cleanup_audio can be slow with thousands of files.
        Only enable for explicit user action or background maintenance.

        Args:
            days: Delete entries older than this many days
            cleanup_audio: If True, also scan and remove orphaned audio files

        Returns:
            Number of entries deleted
        """
        deleted_count = self.db.cleanup_old(days)
        logger.info(f"Cleaned up {deleted_count} entries older than {days} days")

        if cleanup_audio and self.audio_archive:
            orphan_count = self.audio_archive.cleanup_orphans(self.db)
            logger.info(f"Cleaned up {orphan_count} orphaned audio files")

        return deleted_count

    def get_stats(self) -> dict:
        """
        Get database statistics.

        Returns:
            Dictionary with statistics
        """
        return self.db.get_stats()

    # Export methods
    #
    # Each format has a `_build_*` method that returns the formatted string
    # (shared by export_string(), used by the ExportHistory D-Bus method, and
    # the public export_* methods below that write the same content to a
    # file) -- kept as one implementation per format so the string returned
    # over D-Bus and the file written to disk can never silently diverge.

    EXPORT_FORMATS = ("json", "md", "csv", "txt")

    def export_string(self, entries: List[HistoryEntry], format: str) -> str:
        """
        Render entries to one of EXPORT_FORMATS as a string, without writing
        to disk (WHISP-79: used by the daemon's ExportHistory D-Bus method,
        which must not write to an arbitrary path itself -- the caller
        writes the file).

        Args:
            entries: List of HistoryEntry instances to export
            format: One of "json", "md", "csv", "txt"

        Returns:
            The fully rendered export content

        Raises:
            ValueError: if `format` isn't one of EXPORT_FORMATS
        """
        builders = {
            "json": self._build_json,
            "md": self._build_markdown,
            "csv": self._build_csv,
            "txt": self._build_text,
        }
        builder = builders.get(format)
        if builder is None:
            raise ValueError(
                f"Unknown export format: {format!r} (expected one of {self.EXPORT_FORMATS})"
            )
        return builder(entries)

    def _build_json(self, entries: List[HistoryEntry]) -> str:
        """Render entries as a JSON array string (ensure_ascii=False so
        accented characters like ñ/é come through literally, not \\uXXXX)."""
        data = [entry.to_dict() for entry in entries]
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _build_markdown(self, entries: List[HistoryEntry]) -> str:
        """Render entries as a Markdown document, grouped by date."""
        lines = ["# WhisperAloud Transcription History", ""]

        # Group by date
        by_date = {}
        for entry in entries:
            date_str = entry.timestamp.strftime("%Y-%m-%d") if entry.timestamp else "Unknown"
            if date_str not in by_date:
                by_date[date_str] = []
            by_date[date_str].append(entry)

        # Write grouped entries
        for date_str in sorted(by_date.keys(), reverse=True):
            lines.append(f"## {date_str}")
            lines.append("")

            for entry in by_date[date_str]:
                time_str = entry.timestamp.strftime("%H:%M:%S") if entry.timestamp else "??:??:??"
                lines.append(f"### {time_str} - {entry.language.upper()}")
                lines.append("")

                # Metadata
                metadata = [
                    f"**Confidence:** {int(entry.confidence * 100)}%",
                    f"**Duration:** {entry.duration:.1f}s",
                    f"**Processing:** {entry.processing_time:.2f}s"
                ]
                if entry.favorite:
                    metadata.append("⭐ **Favorite**")
                if entry.tags:
                    metadata.append(f"**Tags:** {', '.join(entry.tags)}")

                lines.append(" | ".join(metadata))
                lines.append("")

                # Text
                lines.append("> " + entry.text.replace("\n", "\n> "))
                lines.append("")

                # Notes
                if entry.notes:
                    lines.append("**Notes:**")
                    lines.append(entry.notes)
                    lines.append("")

                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    def _build_csv(self, entries: List[HistoryEntry]) -> str:
        """Render entries as CSV (csv.writer handles quoting of embedded
        commas/quotes/newlines automatically)."""
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Header
        writer.writerow([
            'ID',
            'Timestamp',
            'Text',
            'Language',
            'Confidence',
            'Duration',
            'Processing Time',
            'Tags',
            'Notes',
            'Favorite',
            'Session ID'
        ])

        # Data
        for entry in entries:
            writer.writerow([
                entry.id,
                entry.timestamp.isoformat() if entry.timestamp else '',
                entry.text,
                entry.language,
                entry.confidence,
                entry.duration,
                entry.processing_time,
                ','.join(entry.tags) if entry.tags else '',
                entry.notes,
                entry.favorite,
                entry.session_id or ''
            ])

        return buf.getvalue()

    def _build_text(self, entries: List[HistoryEntry]) -> str:
        """Render entries as plain text (transcriptions only)."""
        lines = []

        for entry in entries:
            timestamp_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S") if entry.timestamp else "Unknown"
            lines.append(f"[{timestamp_str}] {entry.language.upper()}")
            lines.append(entry.text)
            lines.append("")

        return "\n".join(lines)

    def export_json(self, entries: List[HistoryEntry], path: Path) -> None:
        """
        Export entries to JSON.

        Args:
            entries: List of HistoryEntry instances to export
            path: Destination file path
        """
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self._build_json(entries))

        logger.info(f"Exported {len(entries)} entries to JSON: {path}")

    def export_markdown(self, entries: List[HistoryEntry], path: Path) -> None:
        """
        Export entries to Markdown.

        Args:
            entries: List of HistoryEntry instances to export
            path: Destination file path
        """
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self._build_markdown(entries))

        logger.info(f"Exported {len(entries)} entries to Markdown: {path}")

    def export_csv(self, entries: List[HistoryEntry], path: Path) -> None:
        """
        Export entries to CSV.

        Args:
            entries: List of HistoryEntry instances to export
            path: Destination file path
        """
        with open(path, 'w', newline='', encoding='utf-8') as f:
            f.write(self._build_csv(entries))

        logger.info(f"Exported {len(entries)} entries to CSV: {path}")

    def export_text(self, entries: List[HistoryEntry], path: Path) -> None:
        """
        Export entries to plain text (transcriptions only).

        Args:
            entries: List of HistoryEntry instances to export
            path: Destination file path
        """
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self._build_text(entries))

        logger.info(f"Exported {len(entries)} entries to text: {path}")

    @staticmethod
    def _hash_audio(audio: np.ndarray) -> str:
        """
        Generate SHA256 hash of audio data.

        Args:
            audio: Audio samples array

        Returns:
            Hexadecimal hash string
        """
        return hashlib.sha256(audio.tobytes()).hexdigest()
