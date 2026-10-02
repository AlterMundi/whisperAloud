"""Tests for transcribe output formatters (WHISP-76)."""
import json

from whisper_aloud.stt.formatters import Segment, format_json, format_srt, format_txt, format_vtt


def _segments():
    return [
        Segment(start=0.0, end=1.5, text="Hello there"),
        Segment(start=1.5, end=3.25, text="general kenobi"),
    ]


def test_format_txt_plain_joins_text_without_timestamps():
    assert format_txt(_segments(), timestamps=False) == "Hello there general kenobi\n"


def test_format_txt_plain_with_no_segments_is_empty():
    assert format_txt([], timestamps=False) == ""


def test_format_txt_timestamped_includes_hh_mm_ss():
    out = format_txt(_segments(), timestamps=True)
    assert "[00:00:00 - 00:00:01] Hello there" in out
    assert "[00:00:01 - 00:00:03] general kenobi" in out


def test_format_txt_skips_blank_segments():
    segs = [Segment(0.0, 1.0, "  "), Segment(1.0, 2.0, "real text")]
    assert format_txt(segs, timestamps=False) == "real text\n"


def test_format_srt_numbers_and_comma_timestamps():
    out = format_srt(_segments())
    assert out.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello there\n")
    assert "2\n00:00:01,500 --> 00:00:03,250\ngeneral kenobi\n" in out


def test_format_srt_empty_segments_is_empty_string():
    assert format_srt([]) == ""


def test_format_vtt_has_header_and_period_timestamps():
    out = format_vtt(_segments())
    assert out.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.500" in out
    assert "general kenobi" in out


def test_format_vtt_empty_segments_still_has_header():
    assert format_vtt([]) == "WEBVTT\n"


def test_format_json_round_trips_text_and_segments():
    out = format_json(_segments())
    payload = json.loads(out)
    assert payload["text"] == "Hello there general kenobi"
    assert payload["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "Hello there"},
        {"start": 1.5, "end": 3.25, "text": "general kenobi"},
    ]


def test_format_json_includes_extra_meta():
    out = format_json(_segments(), meta={"language": "en", "duration": 3.25})
    payload = json.loads(out)
    assert payload["language"] == "en"
    assert payload["duration"] == 3.25
