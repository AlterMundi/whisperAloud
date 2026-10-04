"""Unit tests for ui/file_transcription_logic.py (WHISP-96) -- pure, no GTK."""
from whisper_aloud.ui.file_transcription_logic import (
    export_filename_for_source,
    file_dialog_filter_patterns,
    is_stale_job_response,
    is_supported_media_file,
    pick_first_supported_path,
    status_label_for_file_job,
    transcribe_options_for_language,
)


class TestIsSupportedMediaFile:
    def test_accepts_common_audio_extensions(self):
        for name in ["clip.mp3", "clip.wav", "clip.m4a", "clip.flac", "clip.ogg"]:
            assert is_supported_media_file(name) is True

    def test_accepts_common_video_extensions(self):
        for name in ["movie.mp4", "movie.webm", "movie.mkv", "movie.mov"]:
            assert is_supported_media_file(name) is True

    def test_case_insensitive(self):
        assert is_supported_media_file("CLIP.WAV") is True

    def test_rejects_unrelated_extension(self):
        assert is_supported_media_file("notes.txt") is False
        assert is_supported_media_file("archive.zip") is False

    def test_rejects_empty_or_no_extension(self):
        assert is_supported_media_file("") is False
        assert is_supported_media_file("README") is False


class TestFileDialogFilterPatterns:
    def test_returns_glob_patterns(self):
        patterns = file_dialog_filter_patterns()
        assert "*.wav" in patterns
        assert "*.mp4" in patterns
        assert all(p.startswith("*.") for p in patterns)


class TestPickFirstSupportedPath:
    def test_picks_first_supported_among_mixed(self):
        candidates = [None, "/tmp/notes.txt", "/tmp/clip.wav", "/tmp/other.mp3"]
        assert pick_first_supported_path(candidates) == "/tmp/clip.wav"

    def test_returns_none_when_nothing_qualifies(self):
        assert pick_first_supported_path([None, "", "/tmp/notes.txt"]) is None

    def test_empty_iterable(self):
        assert pick_first_supported_path([]) is None


class TestStatusLabel:
    def test_includes_filename(self):
        assert "meeting.wav" in status_label_for_file_job("meeting.wav")

    def test_falls_back_for_empty_name(self):
        assert status_label_for_file_job("") == "Transcribing file…"


class TestExportFilenameForSource:
    def test_reuses_source_stem(self):
        assert export_filename_for_source("meeting.m4a", "srt") == "meeting.srt"

    def test_defaults_to_txt_for_unknown_format(self):
        assert export_filename_for_source("clip.wav", "weird") == "clip.txt"

    def test_falls_back_for_empty_source_name(self):
        assert export_filename_for_source("", "vtt") == "transcription.vtt"


class TestTranscribeOptionsForLanguage:
    def test_auto_omits_language_key(self):
        assert transcribe_options_for_language("auto") == {}

    def test_empty_omits_language_key(self):
        assert transcribe_options_for_language("") == {}

    def test_explicit_language_is_forwarded(self):
        assert transcribe_options_for_language("es") == {"language": "es"}


class TestIsStaleJobResponse:
    def test_matching_job_id_is_not_stale(self):
        assert is_stale_job_response("abc", "abc") is False

    def test_mismatched_job_id_is_stale(self):
        assert is_stale_job_response("abc", "def") is True

    def test_no_current_job_is_always_stale(self):
        assert is_stale_job_response("abc", None) is True
