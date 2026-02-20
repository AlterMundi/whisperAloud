"""Tests for daemon-backed history adapter."""

from datetime import datetime
from unittest.mock import MagicMock

from whisper_aloud.service.history_client import DaemonHistoryManager


class _FakeVariant:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


def _sample_payload():
    return [
        {
            "id": _FakeVariant(3),
            "text": _FakeVariant("hello world"),
            "timestamp": _FakeVariant("2026-02-01T12:30:00"),
            "duration": _FakeVariant(1.2),
            "language": _FakeVariant("en"),
            "confidence": _FakeVariant(0.91),
            "processing_time": _FakeVariant(0.44),
            "favorite": _FakeVariant(True),
            "notes": _FakeVariant("note"),
            "tags": _FakeVariant(["one", "two"]),
        }
    ]


def test_get_recent_decodes_entries():
    client = MagicMock()
    client.get_history.return_value = _sample_payload()
    manager = DaemonHistoryManager(client)

    entries = manager.get_recent(limit=10)

    client.get_history.assert_called_once_with(limit=10)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == 3
    assert entry.text == "hello world"
    assert entry.favorite is True
    assert entry.tags == ["one", "two"]
    assert entry.timestamp == datetime.fromisoformat("2026-02-01T12:30:00")


def test_search_and_favorites_delegate_to_client():
    client = MagicMock()
    client.search_history.return_value = _sample_payload()
    client.get_favorite_history.return_value = _sample_payload()
    manager = DaemonHistoryManager(client)

    search_entries = manager.search(" hello ", limit=7)
    favorite_entries = manager.get_favorites(limit=4)

    client.search_history.assert_called_once_with("hello", limit=7)
    client.get_favorite_history.assert_called_once_with(limit=4)
    assert len(search_entries) == 1
    assert len(favorite_entries) == 1


def test_toggle_and_delete_delegate_to_client():
    client = MagicMock()
    client.toggle_history_favorite.return_value = True
    client.delete_history_entry.return_value = True
    manager = DaemonHistoryManager(client)

    assert manager.toggle_favorite(8) is True
    assert manager.delete(9) is True
    client.toggle_history_favorite.assert_called_once_with(8)
    client.delete_history_entry.assert_called_once_with(9)


def test_export_writes_files(tmp_path):
    client = MagicMock()
    manager = DaemonHistoryManager(client)
    entries = manager._decode_entries(_sample_payload())

    json_path = tmp_path / "history.json"
    md_path = tmp_path / "history.md"
    csv_path = tmp_path / "history.csv"
    txt_path = tmp_path / "history.txt"

    manager.export_json(entries, json_path)
    manager.export_markdown(entries, md_path)
    manager.export_csv(entries, csv_path)
    manager.export_text(entries, txt_path)

    assert "hello world" in json_path.read_text(encoding="utf-8")
    assert "WhisperAloud Transcription History" in md_path.read_text(encoding="utf-8")
    assert "hello world" in csv_path.read_text(encoding="utf-8")
    assert "hello world" in txt_path.read_text(encoding="utf-8")

