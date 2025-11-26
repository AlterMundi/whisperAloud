"""Shared pytest fixtures for WhisperAloud tests."""

import pytest
import tempfile
from pathlib import Path
from whisper_aloud.persistence.database import TranscriptionDatabase
from whisper_aloud.persistence.models import HistoryEntry


@pytest.fixture
def temp_db_path():
    """Temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture
def db(temp_db_path):
    """Initialized test database."""
    database = TranscriptionDatabase(temp_db_path)
    yield database
    # Cleanup handled by temp_db_path fixture


@pytest.fixture
def sample_entry():
    """Sample HistoryEntry for testing."""
    return HistoryEntry(
        text="Test transcription of sample audio",
        language="en",
        confidence=0.95,
        duration=5.2,
        processing_time=1.3,
        segments=[
            {"text": "Test transcription", "start": 0.0, "end": 2.5},
            {"text": "of sample audio", "start": 2.5, "end": 5.2}
        ]
    )


@pytest.fixture
def sample_entries():
    """Multiple sample entries for testing."""
    return [
        HistoryEntry(
            text="First test entry",
            language="en",
            confidence=0.92,
            duration=3.0,
            processing_time=0.8,
            segments=[],
            tags=["test", "first"],
            favorite=True
        ),
        HistoryEntry(
            text="Second test entry in Spanish",
            language="es",
            confidence=0.88,
            duration=4.5,
            processing_time=1.2,
            segments=[],
            tags=["test", "spanish"]
        ),
        HistoryEntry(
            text="Third entry with different content",
            language="en",
            confidence=0.95,
            duration=2.1,
            processing_time=0.6,
            segments=[],
            notes="Important note"
        )
    ]
