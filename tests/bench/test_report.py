from bench.report import generate_matrix


def _row(lang, engine, model, device, hw_id, wer, rtf):
    return {
        "lang": lang, "engine": engine, "model": model, "device": device,
        "compute_type": "int8", "hw": {"id": hw_id},
        "metrics": {"wer": wer, "rtf": rtf, "peak_rss_mb": 500,
                    "peak_vram_mb": None, "model_size_mb": 600},
        "dataset": "fleurs/" + lang, "n_clips": 15,
    }


def test_generate_matrix_contains_rows_and_headers():
    rows = [
        _row("es", "parakeet", "tdt-0.6b-v3", "cpu", "i7-1360P", 0.034, 0.16),
        _row("es", "faster-whisper", "small", "cpu", "i7-1360P", 0.055, 0.16),
    ]
    md = generate_matrix(rows)
    assert "# STT Benchmark Matrix" in md
    assert "parakeet" in md and "faster-whisper" in md
    assert "3.4" in md      # WER rendered as percentage
    assert "i7-1360P" in md


def test_generate_matrix_empty():
    md = generate_matrix([])
    assert "# STT Benchmark Matrix" in md
    assert "No results" in md
