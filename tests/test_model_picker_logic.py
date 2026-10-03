"""Pure logic for the catalog-driven model picker (WHISP-42). No GTK/Adw import."""
from dataclasses import replace

import pytest

from whisper_aloud.stt import catalog
from whisper_aloud.ui import model_picker_logic as mpl


def _entry(**overrides):
    """A minimal synthetic ModelEntry for cases the real catalog doesn't cover yet
    (streaming=True, non-standard license, device-restricted)."""
    base = catalog.ModelEntry(
        engine="faster-whisper", name="synthetic", label="Synthetic",
        size_mb=100, languages=None, streaming=False, license="MIT",
        devices=("cpu", "cuda"), tier="fast", recommended=False,
    )
    return replace(base, **overrides)


def test_selectable_entries_filters_by_language_and_includes_parakeet_for_es():
    entries = mpl.selectable_entries("es", "cpu")
    assert any(e.name == "parakeet-tdt-0.6b-v3" for e in entries)
    assert any(e.engine == "faster-whisper" for e in entries)


def test_selectable_entries_includes_parakeet_for_en_and_de():
    # en and de are both in Parakeet v3's declared language list.
    for lang in ("en", "de"):
        entries = mpl.selectable_entries(lang, "cpu")
        assert any(e.name == "parakeet-tdt-0.6b-v3" for e in entries), lang


def test_selectable_entries_excludes_parakeet_for_language_outside_its_list():
    entries = mpl.selectable_entries("ja", "cpu")  # Japanese: not in the 25-language list
    assert not any(e.name == "parakeet-tdt-0.6b-v3" for e in entries)
    assert any(e.engine == "faster-whisper" for e in entries)  # multilingual still shown


def test_selectable_entries_auto_language_returns_everything_reviewable_for_the_device():
    all_cpu = mpl.selectable_entries("auto", "cpu")
    reviewable = [e for e in catalog.entries() if not e.needs_review]
    assert len(all_cpu) == len(reviewable)  # every current reviewable entry supports cpu


def test_selectable_entries_includes_nemotron_now_that_its_review_completed():
    """WHISP-86: nemotron-streaming's needs_review flipped to False, so it is now a
    normal selectable entry (es/en, cpu) — no longer limited to the full-catalog
    view the way it was under WHISP-56."""
    entries = mpl.selectable_entries("auto", "cpu")
    assert any(e.name == "nemotron-3.5-streaming-560ms" for e in entries)


def test_selectable_entries_still_excludes_needs_review_with_synthetic_entry(monkeypatch):
    reviewed = _entry(name="reviewed", needs_review=False)
    pending = _entry(name="pending-review", needs_review=True)
    monkeypatch.setattr(catalog, "entries", lambda: (reviewed, pending))
    entries = mpl.selectable_entries("auto", "cpu")
    assert [e.name for e in entries] == ["reviewed"]


def test_selectable_entries_auto_device_includes_cpu_only_and_cuda_only_synthetic_entries(
    monkeypatch,
):
    """selectable_entries(lang, "auto") must not drop a device-restricted entry —
    "auto" device passes everything through, same convention as "auto" language."""
    cuda_only = _entry(name="cuda-only", devices=("cuda",))
    cpu_only = _entry(name="cpu-only", devices=("cpu",))
    monkeypatch.setattr(catalog, "entries", lambda: (cuda_only, cpu_only))
    names = {e.name for e in mpl.selectable_entries("en", "auto")}
    assert names == {"cuda-only", "cpu-only"}


def test_selectable_entries_filters_by_device(monkeypatch):
    cuda_only = _entry(name="cuda-only", devices=("cuda",))
    cpu_only = _entry(name="cpu-only", devices=("cpu",))
    monkeypatch.setattr(catalog, "entries", lambda: (cuda_only, cpu_only))
    assert [e.name for e in mpl.selectable_entries("auto", "cpu")] == ["cpu-only"]
    assert [e.name for e in mpl.selectable_entries("auto", "cuda")] == ["cuda-only"]


def test_selectable_entries_tolerates_entry_whose_engine_is_not_in_engines_map():
    """A catalog entry for an engine not (yet) registered in catalog.ENGINES must still
    be selectable — e.g. a streaming engine landing on another branch before its ENGINES
    label does (see WHISP-56 coordination note)."""
    unregistered = _entry(name="nemotron-streaming", engine="nemotron-streaming", streaming=True,
                           license="OpenMDW-1.1")
    monkeypatch_entries = (unregistered,)
    import unittest.mock as mock
    with mock.patch.object(catalog, "entries", lambda: monkeypatch_entries):
        entries = mpl.selectable_entries("auto", "cpu")
        assert entries == [unregistered]
        # Pure logic must not explode just because catalog.ENGINES has no label for it.
        assert mpl.badges_for(unregistered) == ["Streaming", "OpenMDW-1.1"]


def test_default_index_prefers_current_selection_when_valid():
    entries = mpl.selectable_entries("es", "cpu")
    idx = mpl.default_index(entries, "faster-whisper", "small", "es")
    assert entries[idx].engine == "faster-whisper"
    assert entries[idx].name == "small"


def test_default_index_falls_back_to_recommended_when_current_is_invalid():
    entries = mpl.selectable_entries("es", "cpu")
    idx = mpl.default_index(entries, "faster-whisper", "not-a-real-model", "es")
    recommended = catalog.recommended(language="es")
    assert entries[idx].name == recommended.name


def test_default_index_english_resolves_to_parakeet_by_default():
    """Regression guard for the WHISP-42 spec §6 product decision: catalog.recommended
    (language='en') -> Parakeet. If a future catalog change silently flips this, this
    test fails and forces a conscious re-review, per spec §8."""
    entries = mpl.selectable_entries("en", "cpu")
    idx = mpl.default_index(entries, "faster-whisper", "not-a-real-model", "en")
    assert entries[idx].name == "parakeet-tdt-0.6b-v3"


def test_default_index_empty_entries_returns_zero():
    assert mpl.default_index([], "faster-whisper", "base", "en") == 0


def test_default_index_matches_current_selection_via_alias():
    """Mutation guard: default_index must match the configured (engine, name) via an
    alias, not just an exact name — e.g. a config saved with the legacy "turbo" name
    should resolve to its canonical large-v3-turbo row, not fall through to the
    recommendation (as it would if the `name in e.aliases` branch were ever dropped)."""
    entries = mpl.selectable_entries("en", "cpu")
    idx = mpl.default_index(entries, "faster-whisper", "turbo", "en")
    assert entries[idx].name == "large-v3-turbo"


def test_badges_for_streaming_entry():
    assert mpl.badges_for(_entry(streaming=True)) == ["Streaming"]


def test_badges_for_nonstandard_license_entry():
    assert mpl.badges_for(_entry(license="OpenMDW-1.1")) == ["OpenMDW-1.1"]


def test_badges_for_streaming_and_nonstandard_license():
    assert mpl.badges_for(_entry(streaming=True, license="OpenMDW-1.1")) == (
        ["Streaming", "OpenMDW-1.1"]
    )


def test_badges_for_plain_entry_is_empty():
    assert mpl.badges_for(_entry()) == []


def test_badges_for_needs_review_entry():
    assert mpl.badges_for(_entry(needs_review=True)) == ["pending license review"]


def test_badges_for_streaming_nonstandard_license_and_needs_review_entry():
    """Synthetic entry shaped like nemotron-streaming was before its WHISP-86
    license review completed (nemotron itself is needs_review=False now, but the
    badge logic for a hypothetical still-pending streaming/non-standard entry must
    keep working)."""
    entry = _entry(streaming=True, license="OpenMDW-1.1", needs_review=True)
    assert mpl.badges_for(entry) == ["Streaming", "OpenMDW-1.1", "pending license review"]


@pytest.mark.parametrize("lic", ["MIT", "Apache-2.0", "CC-BY-4.0", "BSD-3-Clause"])
def test_standard_licenses_are_not_flagged(lic):
    assert mpl.is_nonstandard_license(lic) is False


def test_badges_for_real_nemotron_entry_shows_license_not_pending_review():
    """WHISP-86: the real catalog entry's license review completed, so its badge
    list keeps the non-standard-license badge (still true -- OpenMDW-1.1 isn't in
    the standard allowlist) but drops "pending license review"."""
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    badges = mpl.badges_for(entry)
    assert "OpenMDW-1.1" in badges
    assert "pending license review" not in badges


@pytest.mark.parametrize("lic", ["OpenMDW-1.1", "GPL-3.0", ""])
def test_nonstandard_licenses_are_flagged(lic):
    assert mpl.is_nonstandard_license(lic) is True


def test_needs_download_note():
    assert mpl.needs_download_note(True) is False
    assert mpl.needs_download_note(False) is True


def test_save_selection_passthrough_for_canonical_name():
    assert mpl.save_selection("parakeet", "parakeet-tdt-0.6b-v3") == (
        "parakeet", "parakeet-tdt-0.6b-v3"
    )


def test_save_selection_resolves_alias():
    assert mpl.save_selection("faster-whisper", "turbo") == ("faster-whisper", "large-v3-turbo")


def test_save_selection_unknown_raises():
    with pytest.raises(ValueError):
        mpl.save_selection("nope", "x")


def test_download_state_parakeet_present(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from whisper_aloud.stt import model_download as md
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("x")
    (d / "encoder.onnx").write_bytes(b"\x00")
    entry = catalog.get("parakeet", "parakeet-tdt-0.6b-v3")
    state = mpl.download_state_for(entry)
    assert state.downloaded is True
    assert state.detection == "sherpa-onnx-cache"


def test_download_state_parakeet_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    entry = catalog.get("parakeet", "parakeet-tdt-0.6b-v3")
    state = mpl.download_state_for(entry)
    assert state.downloaded is False
    assert state.detection == "sherpa-onnx-cache"


def test_download_state_faster_whisper_present(monkeypatch, tmp_path):
    hf_hub = tmp_path / "hub"
    snapshot = hf_hub / "models--Systran--faster-whisper-base" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"\x00")
    monkeypatch.setattr(mpl, "_hf_hub_cache_root", lambda: hf_hub)
    entry = catalog.get("faster-whisper", "base")
    state = mpl.download_state_for(entry)
    assert state.downloaded is True
    assert state.detection == "hf-cache-heuristic"


def test_download_state_faster_whisper_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(mpl, "_hf_hub_cache_root", lambda: tmp_path / "hub")
    entry = catalog.get("faster-whisper", "base")
    state = mpl.download_state_for(entry)
    assert state.downloaded is False


def test_download_state_resolves_alias_to_canonical_hf_repo_name(monkeypatch, tmp_path):
    """'turbo' is an alias for large-v3-turbo — the HF cache dir name uses the
    canonical name, not the alias, so lookup must resolve it first."""
    hf_hub = tmp_path / "hub"
    snapshot = (
        hf_hub / "models--Systran--faster-whisper-large-v3-turbo" / "snapshots" / "abc"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"\x00")
    monkeypatch.setattr(mpl, "_hf_hub_cache_root", lambda: hf_hub)
    entry = catalog.get("faster-whisper", "turbo")  # resolved via alias
    assert entry.name == "large-v3-turbo"
    state = mpl.download_state_for(entry)
    assert state.downloaded is True


def test_download_state_unknown_engine_is_conservatively_not_downloaded():
    """A catalog entry this module doesn't know how to check -- no `download_url`
    (the sherpa-onnx tarball check) and not `faster-whisper` (the HF-cache
    heuristic) -- reports downloaded=False rather than guessing. Uses a synthetic
    engine name since the real nemotron-streaming entry has a `download_url` as of
    WHISP-86 and so no longer demonstrates this "unknown" path -- see
    test_download_state_nemotron_present/_absent below."""
    entry = _entry(engine="some-future-engine", name="some-future-model")
    state = mpl.download_state_for(entry)
    assert state.downloaded is False
    assert state.detection == "unknown"


def test_download_state_nemotron_present(monkeypatch, tmp_path):
    """WHISP-86 bug fix: download_state_for used to key its sherpa-onnx-cache check
    on `engine == "parakeet"`, so nemotron-streaming (which also has a catalog
    `download_url`) always fell through to detection="unknown"/downloaded=False in
    the Settings UI, even once its weights were actually on disk. Routing is now
    derived from `entry.download_url` instead."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from whisper_aloud.stt import model_download as md

    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    d = md.model_dir(entry.name)
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("x")
    (d / "encoder.onnx").write_bytes(b"\x00")
    state = mpl.download_state_for(entry)
    assert state.downloaded is True
    assert state.detection == "sherpa-onnx-cache"


def test_download_state_nemotron_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    state = mpl.download_state_for(entry)
    assert state.downloaded is False
    assert state.detection == "sherpa-onnx-cache"


# --- row_label: full Model/catalog row text, badges appended as plain text ---
# (audit MEDIUM #4 — no per-row pill widgets yet; see settings_dialog.py's
# WHISP-42-followup TODO.)


def test_row_label_plain_entry_has_no_trailing_badges():
    assert mpl.row_label(_entry(), downloaded=True) == "Synthetic · 100 MB · fast"


def test_row_label_appends_not_downloaded():
    assert mpl.row_label(_entry(), downloaded=False) == (
        "Synthetic · 100 MB · fast · not downloaded"
    )


def test_row_label_appends_streaming_and_license_badges_in_order():
    entry = _entry(streaming=True, license="OpenMDW-1.1")
    assert mpl.row_label(entry, downloaded=True) == (
        "Synthetic · 100 MB · fast · Streaming · OpenMDW-1.1"
    )


def test_row_label_appends_badges_and_not_downloaded_together():
    entry = _entry(streaming=True, needs_review=True)
    assert mpl.row_label(entry, downloaded=False) == (
        "Synthetic · 100 MB · fast · Streaming · pending license review · not downloaded"
    )


def test_row_label_unknown_size_uses_dash():
    assert mpl.row_label(_entry(size_mb=None), downloaded=True) == "Synthetic · — · fast"


# --- Device row <-> config.model.device mapping (audit MEDIUM #5) ---
# The Device row has three options in this fixed order: Auto, CPU, CUDA. Previously
# "auto" was silently coerced to "cpu" on Save because the row only had two options.


def test_device_row_index_round_trips_auto_cpu_cuda():
    assert mpl.device_row_index("auto") == 0
    assert mpl.device_row_index("cpu") == 1
    assert mpl.device_row_index("cuda") == 2


def test_device_row_index_defaults_to_auto_for_unrecognized_value():
    assert mpl.device_row_index("quantum-computer") == 0


def test_device_for_index_round_trips():
    assert mpl.device_for_index(0) == "auto"
    assert mpl.device_for_index(1) == "cpu"
    assert mpl.device_for_index(2) == "cuda"


def test_device_for_index_defaults_to_auto_for_out_of_range_index():
    assert mpl.device_for_index(99) == "auto"
    assert mpl.device_for_index(-1) == "auto"


# --- entries_for_engine: language-aware Advanced->Engine switch (audit HIGH #2) ---
# Reproduces: language="ja" + Advanced->Parakeet must not silently keep showing the
# previous engine's (faster-whisper) entries just because Parakeet has none for "ja".


def test_entries_for_engine_uses_language_filtered_list_when_nonempty():
    entries, used_fallback = mpl.entries_for_engine("es", "cpu", "faster-whisper")
    assert used_fallback is False
    assert entries  # non-empty
    assert all(e.engine == "faster-whisper" for e in entries)


def test_entries_for_engine_falls_back_to_full_engine_list_when_language_excludes_it():
    entries, used_fallback = mpl.entries_for_engine("ja", "cpu", "parakeet")
    assert used_fallback is True
    assert entries == catalog.models_for("parakeet")
    assert entries  # the fallback must not be empty-handed either
    assert all(e.engine == "parakeet" for e in entries)


def test_entries_for_engine_fallback_for_unknown_engine_is_empty_but_flagged():
    entries, used_fallback = mpl.entries_for_engine("auto", "cpu", "no-such-engine")
    assert entries == []
    assert used_fallback is True
