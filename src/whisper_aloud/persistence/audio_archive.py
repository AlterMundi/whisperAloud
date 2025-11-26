"""Audio file archiving with FLAC compression and deduplication."""

import logging
import shutil
from pathlib import Path
from typing import Optional, Set

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


class AudioArchive:
    """
    Audio file archive with FLAC compression and SHA256-based deduplication.

    Features:
    - FLAC lossless compression for efficient storage
    - SHA256-based filenames for deduplication support
    - Organized directory structure (YYYY/MM/)
    - Orphan detection and cleanup
    """

    def __init__(self, archive_path: Path):
        """
        Initialize audio archive.

        Args:
            archive_path: Root directory for audio archive
        """
        self.archive_path = Path(archive_path)
        self.archive_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"AudioArchive initialized at {self.archive_path}")

    def save(
        self,
        audio: np.ndarray,
        sample_rate: int,
        audio_hash: str
    ) -> Path:
        """
        Save audio to FLAC file with hash-based filename.

        IMPORTANT: This method performs heavy I/O (FLAC encoding, disk writes).
        Must be called from a BACKGROUND THREAD, never from the UI thread.

        Args:
            audio: Audio samples (1D numpy array, float32, normalized to [-1, 1])
            sample_rate: Sample rate in Hz
            audio_hash: SHA256 hash of audio data (for filename)

        Returns:
            Path to saved FLAC file

        Raises:
            IOError: If file write fails
        """
        # Create date-based subdirectory (YYYY/MM/)
        from datetime import datetime
        now = datetime.now()
        subdir = self.archive_path / f"{now.year:04d}" / f"{now.month:02d}"
        subdir.mkdir(parents=True, exist_ok=True)

        # Construct filename: hash[:16].flac
        # Using first 16 chars of hash is sufficient for uniqueness
        filename = f"{audio_hash[:16]}.flac"
        file_path = subdir / filename

        # Skip if file already exists (deduplication)
        if file_path.exists():
            logger.debug(f"Audio file already exists: {file_path}")
            return file_path

        # Ensure audio is in correct format
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Ensure audio is normalized to [-1, 1]
        max_val = np.abs(audio).max()
        if max_val > 1.0:
            logger.warning(f"Audio not normalized (max={max_val:.3f}), clipping to [-1, 1]")
            audio = np.clip(audio, -1.0, 1.0)

        # Write FLAC file
        # FLAC compression levels: 0 (fastest) to 8 (best compression)
        # Level 5 is good balance of speed and size
        try:
            sf.write(
                str(file_path),
                audio,
                sample_rate,
                format='FLAC',
                subtype='PCM_16'  # 16-bit PCM (sufficient for Whisper)
            )
            logger.debug(f"Saved audio file: {file_path} ({len(audio)} samples @ {sample_rate}Hz)")
            return file_path
        except Exception as e:
            logger.error(f"Failed to save audio file {file_path}: {e}")
            raise IOError(f"Failed to save audio file: {e}") from e

    def delete(self, file_path: Path) -> bool:
        """
        Delete audio file.

        Args:
            file_path: Path to audio file to delete

        Returns:
            True if file was deleted, False if file didn't exist
        """
        file_path = Path(file_path)

        if not file_path.exists():
            logger.debug(f"Audio file doesn't exist: {file_path}")
            return False

        try:
            file_path.unlink()
            logger.debug(f"Deleted audio file: {file_path}")

            # Clean up empty directories
            self._cleanup_empty_dirs(file_path.parent)

            return True
        except Exception as e:
            logger.error(f"Failed to delete audio file {file_path}: {e}")
            return False

    def cleanup_orphans(self, db) -> int:
        """
        Find and delete audio files not referenced in database.

        WARNING: This can be SLOW with thousands of files.
        Only call from background maintenance task, not during normal operation.

        Args:
            db: TranscriptionDatabase instance

        Returns:
            Number of orphaned files deleted
        """
        logger.info("Starting orphan audio file cleanup...")

        # Get all audio paths referenced in database
        referenced_paths = db.get_all_audio_paths()
        logger.debug(f"Found {len(referenced_paths)} audio files referenced in database")

        # Scan archive directory for all FLAC files
        archive_files = set(self.archive_path.rglob("*.flac"))
        logger.debug(f"Found {len(archive_files)} FLAC files in archive")

        # Find orphans (files in archive but not in database)
        orphaned_files = archive_files - referenced_paths

        # Delete orphaned files
        deleted_count = 0
        for file_path in orphaned_files:
            try:
                file_path.unlink()
                deleted_count += 1
                logger.debug(f"Deleted orphaned audio file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete orphaned file {file_path}: {e}")

        # Clean up empty directories
        self._cleanup_empty_dirs(self.archive_path)

        logger.info(f"Orphan cleanup complete: deleted {deleted_count} files")
        return deleted_count

    def get_size(self) -> int:
        """
        Get total size of audio archive in bytes.

        Returns:
            Total size in bytes
        """
        total_size = 0
        for file_path in self.archive_path.rglob("*.flac"):
            try:
                total_size += file_path.stat().st_size
            except Exception as e:
                logger.warning(f"Failed to get size of {file_path}: {e}")

        return total_size

    def get_file_count(self) -> int:
        """
        Get number of audio files in archive.

        Returns:
            Number of FLAC files
        """
        return len(list(self.archive_path.rglob("*.flac")))

    def _cleanup_empty_dirs(self, start_dir: Path) -> None:
        """
        Recursively remove empty directories up to archive root.

        Args:
            start_dir: Directory to start cleanup from
        """
        current = Path(start_dir)

        # Walk up the tree until we hit the archive root
        while current != self.archive_path and current.is_relative_to(self.archive_path):
            try:
                # Only remove if directory is empty
                if current.is_dir() and not any(current.iterdir()):
                    current.rmdir()
                    logger.debug(f"Removed empty directory: {current}")
                    current = current.parent
                else:
                    # Stop if directory is not empty
                    break
            except Exception as e:
                logger.warning(f"Failed to remove directory {current}: {e}")
                break
