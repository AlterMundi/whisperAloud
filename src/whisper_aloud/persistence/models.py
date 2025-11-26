"""Data models for persistence layer."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from pathlib import Path


@dataclass
class HistoryEntry:
    """Single transcription history entry."""

    # Primary data (from TranscriptionResult)
    text: str
    language: str
    confidence: float
    duration: float
    processing_time: float
    segments: List[dict]

    # Metadata
    id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    audio_file_path: Optional[Path] = None
    audio_hash: Optional[str] = None

    # User organization
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    favorite: bool = False
    session_id: Optional[str] = None

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_transcription_result(
        cls,
        result,  # TranscriptionResult (avoiding circular import)
        audio_path: Optional[Path] = None,
        audio_hash: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> 'HistoryEntry':
        """
        Create HistoryEntry from TranscriptionResult.

        Args:
            result: TranscriptionResult instance
            audio_path: Optional path to archived audio file
            audio_hash: Optional SHA256 hash of audio data
            session_id: Optional session grouping ID

        Returns:
            New HistoryEntry instance
        """
        return cls(
            text=result.text,
            language=result.language,
            confidence=result.confidence,
            duration=result.duration,
            processing_time=result.processing_time,
            segments=result.segments,
            audio_file_path=audio_path,
            audio_hash=audio_hash,
            session_id=session_id
        )

    def to_dict(self) -> dict:
        """
        Serialize to dictionary for JSON export.

        Returns:
            Dictionary representation with all fields
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "text": self.text,
            "language": self.language,
            "confidence": self.confidence,
            "duration": self.duration,
            "processing_time": self.processing_time,
            "segments": self.segments,
            "audio_file_path": str(self.audio_file_path) if self.audio_file_path else None,
            "audio_hash": self.audio_hash,
            "tags": self.tags,
            "notes": self.notes,
            "favorite": self.favorite,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
