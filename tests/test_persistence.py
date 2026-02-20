"""Tests for persistence layer (database and history manager)."""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from whisper_aloud.persistence.audio_archive import AudioArchive
from whisper_aloud.persistence.models import HistoryEntry


class TestDatabaseSchema:
    """Test database schema creation and migrations."""

    def test_schema_creation(self, db):
        """Test that database schema is created correctly."""
        with db._connection() as conn:
            cursor = conn.cursor()

            # Check main table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            assert 'transcriptions' in tables
            assert 'transcriptions_fts' in tables
            assert 'metadata' in tables

    def test_indexes_created(self, db):
        """Test that all indexes are created."""
        with db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]

            assert 'idx_timestamp' in indexes
            assert 'idx_favorite' in indexes
            assert 'idx_language' in indexes
            assert 'idx_session' in indexes
            assert 'idx_audio_hash' in indexes

    def test_fts_triggers_created(self, db):
        """Test that FTS synchronization triggers are created."""
        with db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            triggers = [row[0] for row in cursor.fetchall()]

            assert 'transcriptions_ai' in triggers  # After insert
            assert 'transcriptions_ad' in triggers  # After delete
            assert 'transcriptions_au' in triggers  # After update

    def test_user_version_set(self, db):
        """Test that schema version is set correctly."""
        with db._connection() as conn:
            cursor = conn.cursor()
            version = cursor.execute("PRAGMA user_version").fetchone()[0]
            assert version == 1


class TestDatabaseCRUD:
    """Test Create, Read, Update, Delete operations."""

    def test_insert_entry(self, db, sample_entry):
        """Test inserting a history entry."""
        entry_id = db.insert(sample_entry)

        assert entry_id > 0
        assert isinstance(entry_id, int)

    def test_insert_retrieve_by_id(self, db, sample_entry):
        """Test insert and retrieve operations."""
        entry_id = db.insert(sample_entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved is not None
        assert retrieved.id == entry_id
        assert retrieved.text == sample_entry.text
        assert retrieved.language == sample_entry.language
        assert retrieved.confidence == sample_entry.confidence
        assert retrieved.duration == sample_entry.duration
        assert retrieved.processing_time == sample_entry.processing_time

    def test_insert_with_all_fields(self, db):
        """Test inserting entry with all optional fields."""
        entry = HistoryEntry(
            text="Complete entry",
            language="fr",
            confidence=0.91,
            duration=10.5,
            processing_time=2.3,
            segments=[{"text": "Complete", "start": 0.0, "end": 10.5}],
            audio_file_path=Path("/tmp/audio.flac"),
            audio_hash="abc123def456",
            tags=["work", "important"],
            notes="This is a note",
            favorite=True,
            session_id="session-123"
        )

        entry_id = db.insert(entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved.audio_file_path == entry.audio_file_path
        assert retrieved.audio_hash == entry.audio_hash
        assert retrieved.tags == entry.tags
        assert retrieved.notes == entry.notes
        assert retrieved.favorite == entry.favorite
        assert retrieved.session_id == entry.session_id

    def test_update_entry(self, db, sample_entry):
        """Test updating entry fields."""
        entry_id = db.insert(sample_entry)

        # Update some fields
        success = db.update(entry_id, favorite=True, notes="Updated note")
        assert success is True

        # Verify updates
        retrieved = db.get_by_id(entry_id)
        assert retrieved.favorite is True
        assert retrieved.notes == "Updated note"
        assert retrieved.text == sample_entry.text  # Unchanged

    def test_update_nonexistent_entry(self, db):
        """Test updating entry that doesn't exist."""
        success = db.update(99999, favorite=True)
        assert success is False

    def test_delete_entry(self, db, sample_entry):
        """Test deleting an entry."""
        entry_id = db.insert(sample_entry)

        # Delete and verify
        success = db.delete(entry_id)
        assert success is True

        # Entry should not exist anymore
        retrieved = db.get_by_id(entry_id)
        assert retrieved is None

    def test_delete_nonexistent_entry(self, db):
        """Test deleting entry that doesn't exist."""
        success = db.delete(99999)
        assert success is False


class TestDatabaseQueries:
    """Test query operations."""

    def test_get_all_empty(self, db):
        """Test get_all with empty database."""
        entries = db.get_all()
        assert entries == []

    def test_get_all_limit(self, db, sample_entries):
        """Test get_all with limit."""
        # Insert multiple entries
        for entry in sample_entries:
            db.insert(entry)

        # Get with limit
        entries = db.get_all(limit=2)
        assert len(entries) == 2

    def test_get_all_ordered_by_timestamp(self, db, sample_entries):
        """Test that get_all returns entries ordered by timestamp DESC."""
        # Insert in order
        ids = [db.insert(entry) for entry in sample_entries]

        entries = db.get_all()

        # Should be in reverse order (newest first)
        assert len(entries) == len(sample_entries)
        assert entries[0].id == ids[-1]  # Last inserted is first returned

    def test_get_by_audio_hash(self, db):
        """Test retrieving entry by audio hash."""
        entry = HistoryEntry(
            text="Test",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[],
            audio_hash="unique_hash_123"
        )

        db.insert(entry)
        retrieved = db.get_by_audio_hash("unique_hash_123")

        assert retrieved is not None
        assert retrieved.audio_hash == "unique_hash_123"

    def test_get_favorites(self, db, sample_entries):
        """Test retrieving favorite entries."""
        for entry in sample_entries:
            db.insert(entry)

        favorites = db.get_favorites()

        assert len(favorites) == 1  # Only first entry is favorite
        assert favorites[0].favorite is True
        assert favorites[0].text == "First test entry"

    def test_get_by_tag(self, db, sample_entries):
        """Test retrieving entries by tag."""
        for entry in sample_entries:
            db.insert(entry)

        # Search for "spanish" tag
        tagged = db.get_by_tag("spanish")

        assert len(tagged) == 1
        assert "spanish" in tagged[0].tags

    def test_get_by_session(self, db):
        """Test retrieving entries by session ID."""
        session_id = "test-session-123"

        for i in range(3):
            entry = HistoryEntry(
                text=f"Entry {i}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[],
                session_id=session_id if i < 2 else "other-session"
            )
            db.insert(entry)

        session_entries = db.get_by_session(session_id)
        assert len(session_entries) == 2

    def test_get_by_date_range(self, db):
        """Test retrieving entries by date range."""
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        tomorrow = now + timedelta(days=1)

        entry = HistoryEntry(
            text="Today's entry",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[],
            timestamp=now
        )
        db.insert(entry)

        # Query for today
        entries = db.get_by_date_range(yesterday, tomorrow)
        assert len(entries) == 1
        assert entries[0].text == "Today's entry"

    def test_cleanup_old(self, db):
        """Test cleanup of old entries."""
        now = datetime.now()
        old_timestamp = now - timedelta(days=100)

        # Insert old entry
        old_entry = HistoryEntry(
            text="Old entry",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[],
            timestamp=old_timestamp
        )
        db.insert(old_entry)

        # Insert new entry
        new_entry = HistoryEntry(
            text="New entry",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[],
            timestamp=now
        )
        db.insert(new_entry)

        # Cleanup entries older than 90 days
        deleted_count = db.cleanup_old(days=90)

        assert deleted_count == 1

        # Verify only new entry remains
        all_entries = db.get_all()
        assert len(all_entries) == 1
        assert all_entries[0].text == "New entry"


class TestFullTextSearch:
    """Test FTS5 full-text search functionality."""

    def test_fts_basic_search(self, db, sample_entry):
        """Test basic FTS5 search."""
        db.insert(sample_entry)

        results = db.search("Test")
        assert len(results) == 1
        assert results[0].text == sample_entry.text

    def test_fts_multiple_results(self, db, sample_entries):
        """Test FTS5 search with multiple results."""
        for entry in sample_entries:
            db.insert(entry)

        # Search for "test" (appears in first two entries)
        results = db.search("test")
        assert len(results) == 2

    def test_fts_case_insensitive(self, db, sample_entry):
        """Test that FTS5 search is case-insensitive."""
        db.insert(sample_entry)

        results_lower = db.search("test")
        results_upper = db.search("TEST")

        assert len(results_lower) == len(results_upper)
        assert results_lower[0].id == results_upper[0].id

    def test_fts_phrase_search(self, db, sample_entries):
        """Test FTS5 phrase search."""
        for entry in sample_entries:
            db.insert(entry)

        # Search for exact phrase
        results = db.search('"test entry"')
        assert len(results) == 2  # First and second entries

    def test_fts_search_in_tags(self, db, sample_entries):
        """Test FTS5 search finds results in tags."""
        for entry in sample_entries:
            db.insert(entry)

        # Search for tag
        results = db.search("spanish")
        assert len(results) >= 1

    def test_fts_search_in_notes(self, db, sample_entries):
        """Test FTS5 search finds results in notes."""
        for entry in sample_entries:
            db.insert(entry)

        # Search for note content
        results = db.search("Important")
        assert len(results) == 1
        assert "Important note" in results[0].notes

    def test_fts_no_results(self, db, sample_entry):
        """Test FTS5 search with no matching results."""
        db.insert(sample_entry)

        results = db.search("nonexistent")
        assert len(results) == 0

    def test_fts_limit(self, db):
        """Test FTS5 search respects limit parameter."""
        # Insert many entries
        for i in range(10):
            entry = HistoryEntry(
                text=f"Test entry number {i}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[]
            )
            db.insert(entry)

        # Search with limit
        results = db.search("Test", limit=5)
        assert len(results) == 5


class TestDatabaseStatistics:
    """Test database statistics functionality."""

    def test_get_stats_empty_db(self, db):
        """Test statistics on empty database."""
        stats = db.get_stats()

        assert stats['total_count'] == 0
        assert stats['by_language'] == {}
        assert stats['avg_confidence'] == 0.0
        assert stats['total_duration'] == 0.0
        assert stats['favorites_count'] == 0
        assert stats['with_audio_count'] == 0

    def test_get_stats_with_data(self, db, sample_entries):
        """Test statistics with data."""
        for entry in sample_entries:
            db.insert(entry)

        stats = db.get_stats()

        assert stats['total_count'] == 3
        assert stats['by_language'] == {'en': 2, 'es': 1}
        assert stats['avg_confidence'] > 0
        assert stats['total_duration'] > 0
        assert stats['favorites_count'] == 1

    def test_get_all_audio_paths(self, db):
        """Test retrieving all audio file paths."""
        # Insert entries with audio paths
        for i in range(3):
            entry = HistoryEntry(
                text=f"Entry {i}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[],
                audio_file_path=Path(f"/tmp/audio_{i}.flac") if i < 2 else None
            )
            db.insert(entry)

        audio_paths = db.get_all_audio_paths()

        assert len(audio_paths) == 2
        assert Path("/tmp/audio_0.flac") in audio_paths
        assert Path("/tmp/audio_1.flac") in audio_paths


class TestDatabaseEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_text(self, db):
        """Test entry with empty text."""
        entry = HistoryEntry(
            text="",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[]
        )

        entry_id = db.insert(entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved.text == ""

    def test_very_long_text(self, db):
        """Test entry with very long text."""
        long_text = "A" * 10000

        entry = HistoryEntry(
            text=long_text,
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[]
        )

        entry_id = db.insert(entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved.text == long_text
        assert len(retrieved.text) == 10000

    def test_special_characters_in_text(self, db):
        """Test entry with special characters."""
        special_text = "Test with émojis 🎉 and spëcial çhars!"

        entry = HistoryEntry(
            text=special_text,
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[]
        )

        entry_id = db.insert(entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved.text == special_text

    def test_empty_segments_list(self, db):
        """Test entry with empty segments list."""
        entry = HistoryEntry(
            text="Test",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[]
        )

        entry_id = db.insert(entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved.segments == []

    def test_none_optional_fields(self, db):
        """Test entry with None in optional fields."""
        entry = HistoryEntry(
            text="Test",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[],
            audio_file_path=None,
            audio_hash=None,
            session_id=None
        )

        entry_id = db.insert(entry)
        retrieved = db.get_by_id(entry_id)

        assert retrieved.audio_file_path is None
        assert retrieved.audio_hash is None
        assert retrieved.session_id is None


class TestHistoryManager:
    """Test HistoryManager business logic."""

    @pytest.fixture
    def manager(self, temp_db_path):
        """HistoryManager instance without audio archiving."""
        from whisper_aloud.config import PersistenceConfig
        from whisper_aloud.persistence import HistoryManager

        config = PersistenceConfig(
            db_path=temp_db_path,
            save_audio=False
        )
        return HistoryManager(config)

    @pytest.fixture
    def sample_transcription_result(self):
        """Sample TranscriptionResult for testing."""
        from whisper_aloud.transcriber import TranscriptionResult
        return TranscriptionResult(
            text="This is a test transcription",
            language="en",
            confidence=0.92,
            duration=5.0,
            processing_time=1.5,
            segments=[
                {"text": "This is a test", "start": 0.0, "end": 2.5},
                {"text": "transcription", "start": 2.5, "end": 5.0}
            ]
        )

    def test_add_transcription_without_audio(self, manager, sample_transcription_result):
        """Test adding transcription without audio."""
        entry_id = manager.add_transcription(sample_transcription_result)

        assert entry_id > 0

        # Verify entry was saved
        entry = manager.get_by_id(entry_id)
        assert entry is not None
        assert entry.text == sample_transcription_result.text
        assert entry.language == sample_transcription_result.language
        assert entry.confidence == sample_transcription_result.confidence

    def test_add_transcription_with_session_id(self, manager, sample_transcription_result):
        """Test adding transcription with session ID."""
        session_id = "test-session-123"
        entry_id = manager.add_transcription(
            sample_transcription_result,
            session_id=session_id
        )

        entry = manager.get_by_id(entry_id)
        assert entry.session_id == session_id

    def test_search(self, manager, sample_transcription_result):
        """Test full-text search."""
        manager.add_transcription(sample_transcription_result)

        results = manager.search("test")
        assert len(results) == 1
        assert results[0].text == sample_transcription_result.text

    def test_get_recent(self, manager, sample_transcription_result):
        """Test getting recent transcriptions."""
        from whisper_aloud.transcriber import TranscriptionResult

        # Add multiple entries
        for i in range(3):
            result = TranscriptionResult(
                text=f"Entry {i}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[]
            )
            manager.add_transcription(result)

        recent = manager.get_recent(limit=2)
        assert len(recent) == 2

    def test_toggle_favorite(self, manager, sample_transcription_result):
        """Test toggling favorite status."""
        entry_id = manager.add_transcription(sample_transcription_result)

        # Initially not favorite
        entry = manager.get_by_id(entry_id)
        assert entry.favorite is False

        # Toggle to favorite
        success = manager.toggle_favorite(entry_id)
        assert success is True

        entry = manager.get_by_id(entry_id)
        assert entry.favorite is True

        # Toggle back
        success = manager.toggle_favorite(entry_id)
        assert success is True

        entry = manager.get_by_id(entry_id)
        assert entry.favorite is False

    def test_update_notes(self, manager, sample_transcription_result):
        """Test updating entry notes."""
        entry_id = manager.add_transcription(sample_transcription_result)

        notes = "Important transcription"
        success = manager.update_notes(entry_id, notes)
        assert success is True

        entry = manager.get_by_id(entry_id)
        assert entry.notes == notes

    def test_add_tag(self, manager, sample_transcription_result):
        """Test adding tags to entry."""
        entry_id = manager.add_transcription(sample_transcription_result)

        # Add first tag
        success = manager.add_tag(entry_id, "work")
        assert success is True

        entry = manager.get_by_id(entry_id)
        assert "work" in entry.tags

        # Add second tag
        success = manager.add_tag(entry_id, "important")
        assert success is True

        entry = manager.get_by_id(entry_id)
        assert "work" in entry.tags
        assert "important" in entry.tags

        # Try to add duplicate tag
        success = manager.add_tag(entry_id, "work")
        assert success is False

    def test_remove_tag(self, manager, sample_transcription_result):
        """Test removing tags from entry."""
        entry_id = manager.add_transcription(sample_transcription_result)

        # Add tags
        manager.add_tag(entry_id, "work")
        manager.add_tag(entry_id, "temp")

        # Remove one tag
        success = manager.remove_tag(entry_id, "temp")
        assert success is True

        entry = manager.get_by_id(entry_id)
        assert "work" in entry.tags
        assert "temp" not in entry.tags

        # Try to remove non-existent tag
        success = manager.remove_tag(entry_id, "nonexistent")
        assert success is False

    def test_get_by_tag(self, manager):
        """Test retrieving entries by tag."""
        from whisper_aloud.transcriber import TranscriptionResult

        # Add entries with different tags
        for tag in ["work", "personal", "work"]:
            result = TranscriptionResult(
                text=f"Entry with tag {tag}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[]
            )
            entry_id = manager.add_transcription(result)
            manager.add_tag(entry_id, tag)

        # Get work entries
        work_entries = manager.get_by_tag("work")
        assert len(work_entries) == 2

        # Get personal entries
        personal_entries = manager.get_by_tag("personal")
        assert len(personal_entries) == 1

    def test_get_favorites(self, manager):
        """Test retrieving favorite entries."""
        from whisper_aloud.transcriber import TranscriptionResult

        # Add entries and mark some as favorite
        for i in range(5):
            result = TranscriptionResult(
                text=f"Entry {i}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[]
            )
            entry_id = manager.add_transcription(result)

            if i % 2 == 0:  # Mark even entries as favorite
                manager.toggle_favorite(entry_id)

        favorites = manager.get_favorites()
        assert len(favorites) == 3  # 0, 2, 4

    def test_get_by_session(self, manager):
        """Test retrieving entries by session ID."""
        from whisper_aloud.transcriber import TranscriptionResult

        session_id = "test-session"

        # Add entries with session ID
        for i in range(3):
            result = TranscriptionResult(
                text=f"Session entry {i}",
                language="en",
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[]
            )
            manager.add_transcription(result, session_id=session_id if i < 2 else "other")

        session_entries = manager.get_by_session(session_id)
        assert len(session_entries) == 2

    def test_delete_entry(self, manager, sample_transcription_result):
        """Test deleting entry."""
        entry_id = manager.add_transcription(sample_transcription_result)

        # Verify entry exists
        entry = manager.get_by_id(entry_id)
        assert entry is not None

        # Delete entry
        success = manager.delete(entry_id)
        assert success is True

        # Verify entry is gone
        entry = manager.get_by_id(entry_id)
        assert entry is None

    def test_cleanup_old(self, manager):
        """Test cleaning up old entries."""
        from datetime import datetime, timedelta

        from whisper_aloud.transcriber import TranscriptionResult

        # Add old entry
        old_result = TranscriptionResult(
            text="Old entry",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[]
        )
        old_entry_id = manager.add_transcription(old_result)

        # Manually update timestamp to 100 days ago
        old_timestamp = datetime.now() - timedelta(days=100)
        manager.db.update(old_entry_id, timestamp=old_timestamp.isoformat())

        # Add recent entry
        new_result = TranscriptionResult(
            text="New entry",
            language="en",
            confidence=0.9,
            duration=1.0,
            processing_time=0.5,
            segments=[]
        )
        manager.add_transcription(new_result)

        # Cleanup entries older than 90 days
        deleted_count = manager.cleanup_old(days=90)
        assert deleted_count == 1

        # Verify only new entry remains
        all_entries = manager.get_recent(limit=100)
        assert len(all_entries) == 1
        assert all_entries[0].text == "New entry"

    def test_get_stats(self, manager):
        """Test getting database statistics."""
        from whisper_aloud.transcriber import TranscriptionResult

        # Add entries
        for lang in ["en", "es", "en"]:
            result = TranscriptionResult(
                text=f"Entry in {lang}",
                language=lang,
                confidence=0.9,
                duration=1.0,
                processing_time=0.5,
                segments=[]
            )
            manager.add_transcription(result)

        stats = manager.get_stats()

        assert stats['total_count'] == 3
        assert stats['by_language']['en'] == 2
        assert stats['by_language']['es'] == 1


class TestHistoryManagerExport:
    """Test HistoryManager export functionality."""

    @pytest.fixture
    def manager_with_data(self, temp_db_path):
        """HistoryManager with sample data."""
        from whisper_aloud.config import PersistenceConfig
        from whisper_aloud.persistence import HistoryManager
        from whisper_aloud.transcriber import TranscriptionResult

        config = PersistenceConfig(
            db_path=temp_db_path,
            save_audio=False
        )
        manager = HistoryManager(config)

        # Add sample entries
        for i in range(3):
            result = TranscriptionResult(
                text=f"Transcription number {i}",
                language="en",
                confidence=0.90 + i * 0.02,
                duration=5.0 + i,
                processing_time=1.0 + i * 0.5,
                segments=[]
            )
            entry_id = manager.add_transcription(result)

            if i == 0:
                manager.add_tag(entry_id, "work")
                manager.toggle_favorite(entry_id)

        return manager

    def test_export_json(self, manager_with_data, tmp_path):
        """Test exporting to JSON."""
        output_file = tmp_path / "export.json"
        entries = manager_with_data.get_recent()

        manager_with_data.export_json(entries, output_file)

        assert output_file.exists()

        # Verify JSON content
        import json
        with open(output_file, 'r') as f:
            data = json.load(f)

        assert len(data) == 3
        assert data[0]['text'] == "Transcription number 2"  # Most recent first

    def test_export_markdown(self, manager_with_data, tmp_path):
        """Test exporting to Markdown."""
        output_file = tmp_path / "export.md"
        entries = manager_with_data.get_recent()

        manager_with_data.export_markdown(entries, output_file)

        assert output_file.exists()

        # Verify Markdown content
        content = output_file.read_text()
        assert "# WhisperAloud Transcription History" in content
        assert "Transcription number" in content
        assert "Confidence:" in content

    def test_export_csv(self, manager_with_data, tmp_path):
        """Test exporting to CSV."""
        output_file = tmp_path / "export.csv"
        entries = manager_with_data.get_recent()

        manager_with_data.export_csv(entries, output_file)

        assert output_file.exists()

        # Verify CSV content
        import csv
        with open(output_file, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) == 4  # Header + 3 data rows
        assert rows[0][0] == 'ID'  # Header
        assert "Transcription number" in rows[1][2]  # First data row, text column

    def test_export_text(self, manager_with_data, tmp_path):
        """Test exporting to plain text."""
        output_file = tmp_path / "export.txt"
        entries = manager_with_data.get_recent()

        manager_with_data.export_text(entries, output_file)

        assert output_file.exists()

        # Verify text content
        content = output_file.read_text()
        assert "Transcription number 0" in content
        assert "Transcription number 1" in content
        assert "Transcription number 2" in content
        assert "[" in content  # Timestamp markers


class TestAudioArchive:
    """Test audio archive functionality."""

    @pytest.fixture
    def archive(self, tmp_path):
        """Create AudioArchive instance."""
        return AudioArchive(tmp_path / "audio_archive")

    @pytest.fixture
    def sample_audio(self):
        """Generate sample audio data."""
        # 1 second of 16kHz sine wave at 440 Hz (A4 note)
        sample_rate = 16000
        duration = 1.0
        frequency = 440.0
        t = np.linspace(0, duration, int(sample_rate * duration))
        audio = np.sin(2 * np.pi * frequency * t).astype(np.float32)
        return audio, sample_rate

    @pytest.fixture
    def sample_hash(self):
        """Sample audio hash."""
        return "a" * 64  # SHA256 hash is 64 hex chars

    def test_archive_creation(self, tmp_path):
        """Test that archive directory is created."""
        archive_path = tmp_path / "audio_archive"
        AudioArchive(archive_path)

        assert archive_path.exists()
        assert archive_path.is_dir()

    def test_save_audio(self, archive, sample_audio, sample_hash):
        """Test saving audio file."""
        audio, sample_rate = sample_audio
        file_path = archive.save(audio, sample_rate, sample_hash)

        # Verify file exists
        assert file_path.exists()
        assert file_path.suffix == ".flac"
        assert file_path.name == f"{sample_hash[:16]}.flac"

        # Verify file is in date-based subdirectory
        from datetime import datetime
        now = datetime.now()
        expected_dir = archive.archive_path / f"{now.year:04d}" / f"{now.month:02d}"
        assert file_path.parent == expected_dir

    def test_save_audio_deduplication(self, archive, sample_audio, sample_hash):
        """Test that saving same audio twice reuses the file."""
        audio, sample_rate = sample_audio

        # Save first time
        file_path_1 = archive.save(audio, sample_rate, sample_hash)

        # Save second time with same hash
        file_path_2 = archive.save(audio, sample_rate, sample_hash)

        # Should return same path
        assert file_path_1 == file_path_2

        # Only one file should exist
        assert archive.get_file_count() == 1

    def test_save_audio_normalization(self, archive, sample_hash):
        """Test that audio normalization and clipping works."""
        # Create audio with values > 1.0
        audio = np.array([0.5, 1.5, -1.5, 0.0], dtype=np.float32)
        sample_rate = 16000

        # Should not raise, should clip to [-1, 1]
        file_path = archive.save(audio, sample_rate, sample_hash)
        assert file_path.exists()

    def test_save_audio_type_conversion(self, archive, sample_hash):
        """Test that audio type conversion works."""
        # Create audio with int16 type
        audio = np.array([0, 16384, -16384, 0], dtype=np.int16)
        sample_rate = 16000

        # Should convert to float32
        file_path = archive.save(audio, sample_rate, sample_hash)
        assert file_path.exists()

    def test_delete_audio(self, archive, sample_audio, sample_hash):
        """Test deleting audio file."""
        audio, sample_rate = sample_audio

        # Save audio
        file_path = archive.save(audio, sample_rate, sample_hash)
        assert file_path.exists()

        # Delete audio
        success = archive.delete(file_path)
        assert success
        assert not file_path.exists()

    def test_delete_nonexistent_audio(self, archive, tmp_path):
        """Test deleting non-existent audio file."""
        fake_path = tmp_path / "nonexistent.flac"
        success = archive.delete(fake_path)
        assert not success

    def test_cleanup_empty_dirs(self, archive, sample_audio):
        """Test that empty directories are cleaned up after deletion."""
        audio, sample_rate = sample_audio
        hash1 = "a" * 64

        # Save and delete audio
        file_path = archive.save(audio, sample_rate, hash1)
        parent_dir = file_path.parent

        archive.delete(file_path)

        # Parent directory should be removed if empty
        # (it might not be if other tests created files in same month)
        if not list(parent_dir.glob("*")):
            assert not parent_dir.exists()

    def test_cleanup_orphans(self, archive, sample_audio, db):
        """Test cleanup of orphaned audio files."""
        audio, sample_rate = sample_audio

        # Create two audio files
        hash1 = "a" * 64
        hash2 = "b" * 64
        file_path_1 = archive.save(audio, sample_rate, hash1)
        file_path_2 = archive.save(audio, sample_rate, hash2)

        # Add only one to database
        entry = HistoryEntry(
            text="Test transcription",
            language="en",
            confidence=0.95,
            duration=1.0,
            processing_time=0.5,
            segments=[],
            audio_file_path=file_path_1,
            audio_hash=hash1
        )
        db.insert(entry)

        # Run cleanup
        deleted_count = archive.cleanup_orphans(db)

        # Should delete the orphaned file (file_path_2)
        assert deleted_count == 1
        assert file_path_1.exists()  # Still referenced
        assert not file_path_2.exists()  # Orphaned, deleted

    def test_get_size(self, archive, sample_audio):
        """Test getting archive size."""
        audio, sample_rate = sample_audio

        # Initially empty
        assert archive.get_size() == 0

        # Save audio
        hash1 = "a" * 64
        file_path = archive.save(audio, sample_rate, hash1)

        # Size should be > 0
        size = archive.get_size()
        assert size > 0
        assert size == file_path.stat().st_size

    def test_get_file_count(self, archive, sample_audio):
        """Test getting file count."""
        audio, sample_rate = sample_audio

        # Initially empty
        assert archive.get_file_count() == 0

        # Save multiple files
        hash1 = "a" * 64
        hash2 = "b" * 64
        archive.save(audio, sample_rate, hash1)
        archive.save(audio, sample_rate, hash2)

        assert archive.get_file_count() == 2

    def test_multiple_files_organization(self, archive, sample_audio):
        """Test that multiple files are organized correctly."""
        audio, sample_rate = sample_audio

        # Save multiple files with different hashes
        hashes = [f"{chr(97 + i)}" * 64 for i in range(5)]  # a-e repeated
        file_paths = []

        for audio_hash in hashes:
            file_path = archive.save(audio, sample_rate, audio_hash)
            file_paths.append(file_path)

        # All files should exist
        for file_path in file_paths:
            assert file_path.exists()

        # All should be in same month directory (since created at same time)
        assert len(set(fp.parent for fp in file_paths)) == 1

        # Total count should match
        assert archive.get_file_count() == 5
