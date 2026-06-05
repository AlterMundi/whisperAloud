from bench.ledger import append_row, read_rows, make_row


def test_make_row_has_schema_keys():
    row = make_row(
        lang="es", dataset="fleurs/es_419", n_clips=15,
        engine="parakeet", model="tdt-0.6b-v3", compute_type="int8", device="cpu",
        hw={"id": "box"}, metrics={"wer": 0.034, "rtf": 0.16},
        git_sha="abc123",
    )
    for key in ("ts", "git_sha", "bench_version", "lang", "dataset", "n_clips",
                "engine", "model", "compute_type", "device", "hw", "metrics", "notes"):
        assert key in row
    assert row["bench_version"] == 1


def test_append_and_read_roundtrip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    r1 = make_row(lang="es", dataset="d", n_clips=1, engine="e", model="m",
                  compute_type="int8", device="cpu", hw={}, metrics={}, git_sha="x")
    r2 = make_row(lang="en", dataset="d", n_clips=1, engine="e", model="m",
                  compute_type="int8", device="cuda", hw={}, metrics={}, git_sha="y")
    append_row(path, r1)
    append_row(path, r2)
    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[0]["lang"] == "es"
    assert rows[1]["device"] == "cuda"


def test_read_missing_file_returns_empty(tmp_path):
    assert read_rows(tmp_path / "nope.jsonl") == []
