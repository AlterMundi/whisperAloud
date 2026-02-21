"""Logic tests for HistoryItem behavior without GTK runtime."""

from whisper_aloud.ui.history_logic import (
    build_history_metadata,
    format_transcription_preview,
    should_emit_favorite_toggle,
)


def test_format_transcription_preview_empty():
    assert format_transcription_preview("") == ""


def test_format_transcription_preview_collapses_whitespace():
    out = format_transcription_preview("a   b\n\nc")
    assert "  " not in out
    assert "\n\n" not in out


def test_format_transcription_preview_line_limits():
    text = " ".join(["word"] * 200)
    out = format_transcription_preview(text, max_lines=5, line_width=25)
    lines = out.splitlines()
    assert len(lines) == 5
    assert lines[-1].endswith("...")


def test_should_emit_favorite_toggle_only_on_change():
    assert should_emit_favorite_toggle(False, True) is True
    assert should_emit_favorite_toggle(True, False) is True
    assert should_emit_favorite_toggle(True, True) is False
    assert should_emit_favorite_toggle(False, False) is False


def test_build_history_metadata_formats_values():
    assert build_history_metadata("es", 0.913, 2.37) == "es • 91% • 2.4s"


def test_build_history_metadata_handles_missing_values():
    assert build_history_metadata(None, None, None) == "auto • 0% • 0.0s"


def test_build_history_metadata_clamps_confidence():
    assert build_history_metadata("en", 1.5, 1.0) == "en • 100% • 1.0s"
    assert build_history_metadata("en", -0.2, 1.0) == "en • 0% • 1.0s"
