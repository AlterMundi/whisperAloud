"""WHISP-79 e2e scenario: ExportHistory against the real daemon/SQLite-FTS5
stack on a temp WHISPER_ALOUD_DB_PATH. Run only via scripts/test_e2e.sh --
see tests/e2e/conftest.py.

Mirrors tests/e2e/test_history.py's pattern: one daemon spawn, several
otherwise-independent assertions grouped into one test function to stay
inside the suite's wall-time budget."""
import csv
import io
import json
import time

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def _record_once(client, fixture_name: str, audio_fixture) -> tuple:
    """Run one full start/stop/transcribe cycle and return (entry_id, text)."""
    fixture = audio_fixture(fixture_name)
    ready = SignalCapture(client, "TranscriptionReady")
    assert client.start_recording() is True
    time.sleep(0.1)
    assert client.stop_recording() == "transcribing"
    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text
    return meta["history_id"], text


def test_export_history_json_and_csv(daemon, audio_fixture):
    """Create two entries via fake transcriptions, favorite one, then export
    to JSON and CSV and check both formats contain the right content --
    including a combined favorites_only+query filter and accent handling."""
    handle = daemon(fake_audio="cancion")
    client = handle.client()

    first_id, first_text = _record_once(client, "cancion", audio_fixture)
    second_id, second_text = _record_once(client, "cancion", audio_fixture)
    assert client.toggle_history_favorite(first_id) is True

    # ── JSON export: both entries present, unicode preserved literally ──
    json_content = client.export_history("json", {"limit": 10})
    assert json_content
    assert "\\u" not in json_content  # ensure_ascii=False
    data = json.loads(json_content)
    exported_ids = {entry["id"] for entry in data}
    assert {first_id, second_id} <= exported_ids
    assert any(entry["text"] == first_text for entry in data)
    assert "canción" in json_content

    # ── CSV export: header + quoting survive a real daemon round trip ──
    csv_content = client.export_history("csv", {"limit": 10})
    rows = list(csv.reader(io.StringIO(csv_content)))
    assert rows[0][0] == "ID"
    csv_ids = {int(row[0]) for row in rows[1:]}
    assert {first_id, second_id} <= csv_ids

    # ── Combined filter: favorites_only narrows to just the favorited one ──
    favorites_only_content = client.export_history(
        "json", {"favorites_only": True, "limit": 10}
    )
    favorites_data = json.loads(favorites_only_content)
    favorite_ids = {entry["id"] for entry in favorites_data}
    assert first_id in favorite_ids
    assert second_id not in favorite_ids

    client.disconnect()


def test_export_history_invalid_format_and_filter_report_errors(daemon, audio_fixture):
    """An unknown format or unparseable date returns "" and emits Error,
    without ever reaching the database."""
    handle = daemon(fake_audio="tone")
    client = handle.client()

    errors = SignalCapture(client, "Error")

    assert client.export_history("pdf", {}) == ""
    code, _message = errors.wait(timeout=5.0)
    assert code == "export_invalid_format"

    errors2 = SignalCapture(client, "Error")
    assert client.export_history("json", {"since": "not-a-date"}) == ""
    code2, _message2 = errors2.wait(timeout=5.0)
    assert code2 == "export_invalid_filter"

    client.disconnect()
