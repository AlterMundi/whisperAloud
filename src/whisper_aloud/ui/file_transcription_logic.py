"""Pure logic helpers for the GUI's "Transcribe file..." flow (WHISP-96).

Kept GTK-free and side-effect-free so it is unit-testable without a display
-- main_window.py (and its drag-and-drop/"Open with" callers) are thin GTK
wrappers around these functions, mirroring the existing history_logic.py/
main_window_logic.py split.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Common audio/video extensions WhisperAloud can hand to an STT engine's
# transcribe_file(); kept in sync with data/org.fede.whisperaloud.desktop's
# MimeType= list and the Gtk.FileFilter patterns built below. PyAV (the
# container/codec decoder used by most non-faster-whisper engines) and
# faster-whisper's own ffmpeg-based loader both accept a much wider set in
# practice, but this list is what we actively invite via the file picker,
# drag&drop, and "Open With" -- an unlisted extension can still be opened by
# typing its name in the Gtk.FileDialog (filters are suggestions, not a
# sandbox), so being conservative here costs nothing.
AUDIO_EXTENSIONS: Tuple[str, ...] = (
    ".mp3", ".m4a", ".mp4", ".wav", ".ogg", ".oga", ".flac", ".webm", ".aac",
)
VIDEO_EXTENSIONS: Tuple[str, ...] = (
    ".mp4", ".webm", ".mkv", ".mov",
)
SUPPORTED_EXTENSIONS: Tuple[str, ...] = tuple(
    sorted(set(AUDIO_EXTENSIONS) | set(VIDEO_EXTENSIONS))
)


def is_supported_media_file(path: str) -> bool:
    """Whether `path`'s extension looks like audio/video WhisperAloud should
    offer to transcribe (used to filter drag&drop and "Open With" file
    lists). Case-insensitive; a path with no/unknown extension is rejected
    -- this only gates an *automatic* flow (drop, multi-file open), never
    the explicit file-picker dialog, where any file can still be chosen."""
    if not path:
        return False
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def file_dialog_filter_patterns() -> List[str]:
    """Glob patterns for the Gtk.FileDialog's "Audio/Video" filter, e.g.
    ``["*.mp3", "*.wav", ...]``."""
    return [f"*{ext}" for ext in SUPPORTED_EXTENSIONS]


def pick_first_supported_path(paths: Iterable[Optional[str]]) -> Optional[str]:
    """From a list of candidate local paths (e.g. multiple dropped/opened
    files, or Gio.File.get_path() results that may be None for a non-local
    GFile), return the first that is both non-empty and a supported media
    file. Returns None if nothing qualifies.

    WHISP-96 deliberately handles one file per drop/open -- transcribing a
    batch is out of scope for this ticket -- so "first wins" rather than
    silently picking something unexpected is the simplest, most predictable
    rule, and matches `transcribe_file()`'s one-job-at-a-time daemon API.
    """
    for path in paths:
        if path and is_supported_media_file(path):
            return path
    return None


def status_label_for_file_job(filename: str) -> str:
    """Status-bar text while a TranscribeFile() job is in flight."""
    name = filename or "file"
    return f"Transcribing {name}…"


def export_filename_for_source(source_name: str, fmt: str) -> str:
    """Default filename for the "export this result" save dialog, derived
    from the source audio/video filename (e.g. "meeting.m4a" + "srt" ->
    "meeting.srt") -- mirrors history_panel.py's export naming convention
    for a mic-recorded entry, but keeps the original basename instead of a
    generic "transcription" prefix, since there is a real source file to
    name it after."""
    stem = Path(source_name).stem if source_name else "transcription"
    extension = {"txt": "txt", "srt": "srt", "vtt": "vtt", "json": "json"}.get(
        fmt, "txt"
    )
    return f"{stem}.{extension}"


def transcribe_options_for_language(language_code: str) -> dict:
    """Build the TranscribeFile() `options` dict from the GUI's language
    dropdown value. "auto" (the dropdown's default) means "let the engine
    decide", so it is omitted entirely rather than forwarded as a literal
    "auto" string the daemon/engine would have to special-case."""
    if not language_code or language_code == "auto":
        return {}
    return {"language": language_code}


def is_stale_job_response(received_job_id: str, current_job_id: Optional[str]) -> bool:
    """True when a FileTranscriptionReady/Error/Progress signal's job_id no
    longer matches the job the GUI is actually waiting on (e.g. the user
    started a second file transcription, or cleared the view, before the
    first one's result arrived) -- such a response must be dropped, not
    rendered over whatever is current."""
    return current_job_id is None or received_job_id != current_job_id
