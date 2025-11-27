"""SQLite database for transcription history with FTS5 full-text search."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Optional, Set
from datetime import datetime, timedelta
from contextlib import contextmanager

from .models import HistoryEntry

logger = logging.getLogger(__name__)


class TranscriptionDatabase:
    """SQLite database for transcription history with FTS5 full-text search."""

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database connection and schema.

        Args:
            db_path: Optional path to database file. Defaults to XDG data directory.
        """
        if db_path is None:
            db_path = Path.home() / ".local/share/whisper_aloud/history.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()
        logger.info(f"Database initialized at {self.db_path}")

    @contextmanager
    def _connection(self):
        """
        Context manager for database connections.

        Yields:
            sqlite3.Connection with Row factory
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize database schema with tables, indexes, and triggers."""
        with self._connection() as conn:
            cursor = conn.cursor()

            # Check schema version for migrations
            current_version = cursor.execute("PRAGMA user_version").fetchone()[0]

            if current_version == 0:
                # Fresh database - create all tables
                logger.info("Creating fresh database schema (version 1)")
                self._create_tables(cursor)
                cursor.execute("PRAGMA user_version = 1")
            elif current_version < 1:
                # Future: Migration logic for older schemas
                logger.info(f"Migrating database from version {current_version} to 1")
                self._migrate_to_v1(cursor)
            else:
                logger.debug(f"Database schema version {current_version} is up to date")

    def _create_tables(self, cursor):
        """
        Create all tables, indexes, FTS5 virtual tables, and triggers.

        Args:
            cursor: sqlite3.Cursor
        """
        # Main transcriptions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transcriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                text TEXT NOT NULL,
                language VARCHAR(10),
                confidence REAL,
                duration REAL,
                processing_time REAL,
                segments TEXT,
                audio_file_path TEXT,
                audio_hash TEXT,
                tags TEXT,
                notes TEXT,
                favorite BOOLEAN DEFAULT 0,
                session_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON transcriptions(timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_favorite ON transcriptions(favorite)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_language ON transcriptions(language)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_session ON transcriptions(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audio_hash ON transcriptions(audio_hash)")

        # FTS5 virtual table for full-text search
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts
            USING fts5(
                text,
                tags,
                notes,
                content=transcriptions,
                content_rowid=id
            )
        """)

        # Triggers to keep FTS index synchronized
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS transcriptions_ai AFTER INSERT ON transcriptions BEGIN
                INSERT INTO transcriptions_fts(rowid, text, tags, notes)
                VALUES (new.id, new.text, new.tags, new.notes);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS transcriptions_ad AFTER DELETE ON transcriptions BEGIN
                DELETE FROM transcriptions_fts WHERE rowid = old.id;
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS transcriptions_au AFTER UPDATE ON transcriptions BEGIN
                INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text, tags, notes)
                VALUES('delete', old.id, old.text, old.tags, old.notes);
                INSERT INTO transcriptions_fts(rowid, text, tags, notes)
                VALUES (new.id, new.text, new.tags, new.notes);
            END
        """)

        # Metadata table for statistics and settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        logger.debug("Database schema created successfully")

    def _migrate_to_v1(self, cursor):
        """
        Migrate database to version 1.

        Args:
            cursor: sqlite3.Cursor
        """
        # Future: Add migration logic here
        cursor.execute("PRAGMA user_version = 1")
        logger.info("Database migrated to version 1")

    def _row_to_entry(self, row: sqlite3.Row) -> HistoryEntry:
        """
        Convert database row to HistoryEntry.

        Args:
            row: sqlite3.Row from query

        Returns:
            HistoryEntry instance
        """
        return HistoryEntry(
            id=row['id'],
            timestamp=datetime.fromisoformat(row['timestamp']) if row['timestamp'] else None,
            text=row['text'],
            language=row['language'],
            confidence=row['confidence'],
            duration=row['duration'],
            processing_time=row['processing_time'],
            segments=json.loads(row['segments']) if row['segments'] else [],
            audio_file_path=Path(row['audio_file_path']) if row['audio_file_path'] else None,
            audio_hash=row['audio_hash'],
            tags=json.loads(row['tags']) if row['tags'] else [],
            notes=row['notes'] or "",
            favorite=bool(row['favorite']),
            session_id=row['session_id'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def insert(self, entry: HistoryEntry) -> int:
        """
        Insert history entry and return row ID.

        Args:
            entry: HistoryEntry to insert

        Returns:
            Integer row ID of inserted entry
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO transcriptions (
                    timestamp, text, language, confidence, duration, processing_time,
                    segments, audio_file_path, audio_hash, tags, notes, favorite,
                    session_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.timestamp.isoformat() if entry.timestamp else datetime.now().isoformat(),
                entry.text,
                entry.language,
                entry.confidence,
                entry.duration,
                entry.processing_time,
                json.dumps(entry.segments),
                str(entry.audio_file_path) if entry.audio_file_path else None,
                entry.audio_hash,
                json.dumps(entry.tags),
                entry.notes,
                int(entry.favorite),
                entry.session_id,
                entry.created_at.isoformat() if entry.created_at else datetime.now().isoformat(),
                entry.updated_at.isoformat() if entry.updated_at else datetime.now().isoformat()
            ))
            entry_id = cursor.lastrowid
            logger.debug(f"Inserted entry {entry_id}: {entry.text[:50]}...")
            return entry_id

    def update(self, entry_id: int, **updates) -> bool:
        """
        Update entry fields.

        Args:
            entry_id: ID of entry to update
            **updates: Field names and values to update

        Returns:
            True if update succeeded, False if entry not found
        """
        if not updates:
            return False

        # Always update updated_at timestamp
        updates['updated_at'] = datetime.now().isoformat()

        # Convert booleans to integers for SQLite and build values list
        converted_updates = {}
        for key, value in updates.items():
            if isinstance(value, bool):
                converted_updates[key] = int(value)
            else:
                converted_updates[key] = value

        # Build UPDATE query dynamically
        set_clause = ', '.join(f"{key} = ?" for key in converted_updates.keys())
        values = list(converted_updates.values()) + [entry_id]

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE transcriptions SET {set_clause} WHERE id = ?",
                values
            )
            success = cursor.rowcount > 0
            if success:
                logger.debug(f"Updated entry {entry_id}: {list(updates.keys())}")
            return success

    def delete(self, entry_id: int) -> bool:
        """
        Delete entry by ID.

        Args:
            entry_id: ID of entry to delete

        Returns:
            True if deletion succeeded, False if entry not found
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM transcriptions WHERE id = ?", (entry_id,))
            success = cursor.rowcount > 0
            if success:
                logger.debug(f"Deleted entry {entry_id}")
            return success

    def get_by_id(self, entry_id: int) -> Optional[HistoryEntry]:
        """
        Get entry by ID.

        Args:
            entry_id: ID of entry to retrieve

        Returns:
            HistoryEntry if found, None otherwise
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transcriptions WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return self._row_to_entry(row) if row else None

    def get_by_audio_hash(self, audio_hash: str) -> Optional[HistoryEntry]:
        """
        Get entry by audio hash (for deduplication).

        Args:
            audio_hash: SHA256 hash of audio data

        Returns:
            HistoryEntry if found, None otherwise
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM transcriptions WHERE audio_hash = ? LIMIT 1",
                (audio_hash,)
            )
            row = cursor.fetchone()
            return self._row_to_entry(row) if row else None

    def insert_or_get_by_hash(self, entry: HistoryEntry) -> tuple[int, bool]:
        """
        Insert entry or return existing if audio_hash matches.

        Uses exclusive transaction to prevent race conditions during deduplication.

        Args:
            entry: HistoryEntry to insert (must have audio_hash)

        Returns:
            Tuple of (entry_id, is_new) where is_new is True if inserted, False if existing found
        """
        if not entry.audio_hash:
            # No hash, just insert normally
            entry_id = self.insert(entry)
            return entry_id, True

        with self._connection() as conn:
            cursor = conn.cursor()

            # Start exclusive transaction to prevent race conditions
            cursor.execute("BEGIN EXCLUSIVE")

            try:
                # Check if entry with this hash already exists
                cursor.execute(
                    "SELECT id FROM transcriptions WHERE audio_hash = ? LIMIT 1",
                    (entry.audio_hash,)
                )
                existing_row = cursor.fetchone()

                if existing_row:
                    # Return existing entry ID
                    entry_id = existing_row[0]
                    logger.debug(f"Found existing entry {entry_id} for audio hash {entry.audio_hash}")
                    return entry_id, False

                # No existing entry, insert new one
                cursor.execute("""
                    INSERT INTO transcriptions (
                        timestamp, text, language, confidence, duration, processing_time,
                        segments, audio_file_path, audio_hash, tags, notes, favorite,
                        session_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.timestamp.isoformat() if entry.timestamp else datetime.now().isoformat(),
                    entry.text,
                    entry.language,
                    entry.confidence,
                    entry.duration,
                    entry.processing_time,
                    json.dumps(entry.segments),
                    str(entry.audio_file_path) if entry.audio_file_path else None,
                    entry.audio_hash,
                    json.dumps(entry.tags),
                    entry.notes,
                    int(entry.favorite),
                    entry.session_id,
                    entry.created_at.isoformat() if entry.created_at else datetime.now().isoformat(),
                    entry.updated_at.isoformat() if entry.updated_at else datetime.now().isoformat()
                ))
                entry_id = cursor.lastrowid
                logger.debug(f"Inserted new entry {entry_id} for audio hash {entry.audio_hash}")
                return entry_id, True

            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def get_all(self, limit: Optional[int] = 50, offset: int = 0) -> List[HistoryEntry]:
        """
        Get all entries ordered by timestamp DESC.

        Args:
            limit: Maximum number of entries to return (default: 50, None for no limit)
            offset: Number of entries to skip (default: 0)

        Returns:
            List of HistoryEntry instances
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            if limit is None:
                cursor.execute(
                    "SELECT * FROM transcriptions ORDER BY timestamp DESC OFFSET ?",
                    (offset,)
                )
            else:
                cursor.execute(
                    "SELECT * FROM transcriptions ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                )
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def search(self, query: str, limit: int = 50) -> List[HistoryEntry]:
        """
        Full-text search using FTS5.

        Args:
            query: Search query string
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.* FROM transcriptions t
                JOIN transcriptions_fts fts ON t.id = fts.rowid
                WHERE transcriptions_fts MATCH ?
                ORDER BY t.timestamp DESC
                LIMIT ?
            """, (query, limit))
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_by_tag(self, tag: str, limit: int = 50) -> List[HistoryEntry]:
        """
        Get entries with specific tag.

        Args:
            tag: Tag to search for
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            # Use JSON array search
            cursor.execute("""
                SELECT * FROM transcriptions
                WHERE tags LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (f'%"{tag}"%', limit))
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_favorites(self, limit: int = 50) -> List[HistoryEntry]:
        """
        Get all favorite entries.

        Args:
            limit: Maximum number of results (default: 50)

        Returns:
            List of favorite HistoryEntry instances
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM transcriptions
                WHERE favorite = 1
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_by_date_range(
        self,
        start: datetime,
        end: datetime,
        limit: int = 50
    ) -> List[HistoryEntry]:
        """
        Get entries within date range.

        Args:
            start: Start datetime (inclusive)
            end: End datetime (inclusive)
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM transcriptions
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (start.isoformat(), end.isoformat(), limit))
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_by_session(self, session_id: str, limit: int = 50) -> List[HistoryEntry]:
        """
        Get all entries from a specific session.

        Args:
            session_id: Session ID to filter by
            limit: Maximum number of results (default: 50)

        Returns:
            List of matching HistoryEntry instances
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM transcriptions
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, limit))
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def cleanup_old(self, days: int) -> int:
        """
        Delete entries older than N days.

        Args:
            days: Delete entries older than this many days

        Returns:
            Number of entries deleted
        """
        cutoff = datetime.now() - timedelta(days=days)
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM transcriptions WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            deleted_count = cursor.rowcount
            logger.info(f"Cleaned up {deleted_count} entries older than {days} days")
            return deleted_count

    def get_all_audio_paths(self) -> Set[Path]:
        """
        Get all audio file paths referenced in database.

        Returns:
            Set of Path objects for all audio files
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT audio_file_path FROM transcriptions WHERE audio_file_path IS NOT NULL")
            return {Path(row[0]) for row in cursor.fetchall()}

    def count_audio_references(self, audio_path: str) -> int:
        """
        Count how many entries reference a specific audio file path.

        Args:
            audio_path: Audio file path to check

        Returns:
            Number of entries referencing this path
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM transcriptions WHERE audio_file_path = ?",
                (audio_path,)
            )
            return cursor.fetchone()[0]

    def get_stats(self) -> dict:
        """
        Get database statistics.

        Returns:
            Dictionary with statistics (total count, by language, avg confidence, etc.)
        """
        with self._connection() as conn:
            cursor = conn.cursor()

            # Total count
            cursor.execute("SELECT COUNT(*) FROM transcriptions")
            total_count = cursor.fetchone()[0]

            # Count by language
            cursor.execute("""
                SELECT language, COUNT(*) as count
                FROM transcriptions
                GROUP BY language
                ORDER BY count DESC
            """)
            by_language = {row[0]: row[1] for row in cursor.fetchall()}

            # Average confidence
            cursor.execute("SELECT AVG(confidence) FROM transcriptions")
            avg_confidence = cursor.fetchone()[0] or 0.0

            # Total duration
            cursor.execute("SELECT SUM(duration) FROM transcriptions")
            total_duration = cursor.fetchone()[0] or 0.0

            # Favorites count
            cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE favorite = 1")
            favorites_count = cursor.fetchone()[0]

            # With audio count
            cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE audio_file_path IS NOT NULL")
            with_audio_count = cursor.fetchone()[0]

            return {
                "total_count": total_count,
                "by_language": by_language,
                "avg_confidence": avg_confidence,
                "total_duration": total_duration,
                "favorites_count": favorites_count,
                "with_audio_count": with_audio_count
            }
