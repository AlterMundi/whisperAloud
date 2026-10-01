"""WHISP-47 e2e scenarios: history CRUD and FTS search against the real
daemon/SQLite-FTS5 stack on a temp WHISPER_ALOUD_DB_PATH. Run only via
scripts/test_e2e.sh -- see tests/e2e/conftest.py.

Several otherwise-independent assertions are grouped into one test function
per daemon spawn (rather than one assertion per test) to stay inside the
suite's ~60s wall-time budget: a fresh daemon subprocess costs ~1.5-2s just
in interpreter/gi import time, which dominates over actual assertion cost
for cheap D-Bus calls like ToggleHistoryFavorite/UpdateHistoryEntry/
DeleteHistoryEntry against an unknown id."""
import time

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def _record_once(client, fixture_name: str, audio_fixture) -> tuple:
    """Run one full start/stop/transcribe cycle on an already-running client
    and return (entry_id, text). The client's daemon must have been spawned
    with fake_audio=fixture_name."""
    fixture = audio_fixture(fixture_name)
    ready = SignalCapture(client, "TranscriptionReady")
    assert client.start_recording() is True
    time.sleep(0.1)
    assert client.stop_recording() == "transcribing"
    text, meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text
    return meta["history_id"], text


def test_history_crud_round_trip(daemon, audio_fixture):
    """GetHistory, SearchHistory's empty-query fallback, favorites toggle
    (including an unknown id), UpdateHistoryEntry (including an unknown id),
    and DeleteHistoryEntry (including an unknown id) -- all against one
    recorded entry on one daemon."""
    handle = daemon(fake_audio="tone")
    client = handle.client()
    entry_id, text = _record_once(client, "tone", audio_fixture)

    # GetHistory
    history = client.get_history(limit=10)
    assert any(e["id"] == entry_id and e["text"] == text for e in history)

    # SearchHistory("") falls back to GetHistory
    results = client.search_history("   ", limit=10)
    assert any(r["id"] == entry_id for r in results)

    # Favorites: unknown id, then a real round trip
    assert client.toggle_history_favorite(999999) is False
    favorites_before = client.get_favorite_history(limit=10)
    assert not any(e["id"] == entry_id for e in favorites_before)
    assert client.toggle_history_favorite(entry_id) is True
    favorites_after = client.get_favorite_history(limit=10)
    assert any(e["id"] == entry_id and e["favorite"] is True for e in favorites_after)
    assert client.toggle_history_favorite(entry_id) is True  # toggle back off
    favorites_final = client.get_favorite_history(limit=10)
    assert not any(e["id"] == entry_id for e in favorites_final)

    # UpdateHistoryEntry: unknown id, then a real overwrite
    assert client.update_history_entry(999999, "no such entry") is False
    assert client.update_history_entry(entry_id, "corrected text") is True
    history = client.get_history(limit=10)
    entry = next(e for e in history if e["id"] == entry_id)
    assert entry["text"] == "corrected text"

    # DeleteHistoryEntry: unknown id, then the real one
    assert client.delete_history_entry(999999) is False
    assert client.delete_history_entry(entry_id) is True
    history = client.get_history(limit=10)
    assert not any(e["id"] == entry_id for e in history)

    client.disconnect()


def test_search_history_matches_distinctive_word(daemon, audio_fixture):
    """SearchHistory("umbrella", ...) must return only the fixture that
    actually contains that word, not the unrelated "tone" transcription."""
    handle = daemon(fake_audio="tone")
    client = handle.client()
    tone_id, tone_text = _record_once(client, "tone", audio_fixture)
    client.disconnect()
    # Only one process can ever own org.fede.whisperaloud on this bus -- stop
    # the first daemon before starting a second one (sharing its DB file),
    # rather than leaving both "running" (the first would silently keep
    # answering every call, since a second daemon() spawn's readiness check
    # only confirms *a* daemon owns the name, not that it is the new one).
    handle.stop()

    # Re-spawn with a different fake-audio fixture: the daemon reads
    # WHISPER_ALOUD_FAKE_AUDIO_WAV once at process start (see fake_engine.py),
    # so a second transcript requires a second daemon sharing the same DB.
    handle2 = daemon(
        config_overrides={"DB_PATH": str(handle.xdg_home / "data" / "history.sqlite3")},
        fake_audio="umbrella",
    )
    client2 = handle2.client()
    umbrella_id, umbrella_text = _record_once(client2, "umbrella", audio_fixture)

    results = client2.search_history("umbrella", limit=10)
    texts = [r["text"] for r in results]
    assert umbrella_text in texts
    assert tone_text not in texts

    client2.disconnect()


def test_search_history_accent_folding_documented_behavior(daemon, audio_fixture):
    """Document FTS5's actual accent-folding behaviour for this schema:
    transcriptions_fts uses the default `fts5(...)` tokenizer (no explicit
    `tokenize=` clause -> plain `unicode61` with its own default
    diacritics-handling), not a custom one. Search both the accented form
    ("canción") and the plain-ASCII form ("cancion") and record what
    actually matches, rather than assuming a specific behavior."""
    handle = daemon(fake_audio="cancion")
    client = handle.client()
    entry_id, text = _record_once(client, "cancion", audio_fixture)
    assert text == "una canción bonita"

    accented_hits = client.search_history("canción", limit=10)
    plain_hits = client.search_history("cancion", limit=10)

    # The exact accented query must always find the entry (it's a literal
    # substring/token match).
    assert any(r["id"] == entry_id for r in accented_hits)

    # Document whichever way the plain-ASCII query behaves today: this
    # schema's fts5(...) call has no explicit `tokenize=` argument, so it
    # uses SQLite's compiled-in unicode61 default. If this assertion ever
    # flips, that's a sqlite3 version change in the diacritics default, not
    # a WhisperAloud code change -- re-pin this test to match.
    plain_matches = any(r["id"] == entry_id for r in plain_hits)
    assert plain_matches, (
        "Expected the unaccented query 'cancion' to also match 'canción' "
        "(unicode61's compiled-in remove_diacritics default). If this "
        "starts failing, unicode61's default changed -- update this test "
        "to document the new behavior rather than silently xfail-ing it."
    )

    client.disconnect()
