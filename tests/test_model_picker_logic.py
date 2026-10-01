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


def test_selectable_entries_auto_language_returns_everything_for_the_device():
    all_cpu = mpl.selectable_entries("auto", "cpu")
    assert len(all_cpu) == len(catalog.entries())  # every current entry supports cpu


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


@pytest.mark.parametrize("lic", ["MIT", "Apache-2.0", "CC-BY-4.0", "BSD-3-Clause"])
def test_standard_licenses_are_not_flagged(lic):
    assert mpl.is_nonstandard_license(lic) is False


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
    """A catalog entry for an engine this module doesn't know how to check (e.g. a
    future streaming engine) reports downloaded=False rather than guessing."""
    entry = _entry(engine="nemotron-streaming", name="nemotron-streaming-0.6b")
    state = mpl.download_state_for(entry)
    assert state.downloaded is False
    assert state.detection == "unknown"
