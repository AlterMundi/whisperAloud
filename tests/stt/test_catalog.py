"""Invariant tests for the STT model catalog (WHISP-50)."""
import pytest

from whisper_aloud.stt import catalog

# The dict literal `config.ENGINE_MODELS` carried before WHISP-50 — frozen here so this
# test fails if the catalog ever drops a name that used to be valid.
LEGACY_ENGINE_MODELS = {
    "faster-whisper": [
        "tiny", "base", "small", "medium",
        "large-v1", "large-v2", "large-v3", "large",
        "large-v3-turbo", "turbo",
    ],
    "parakeet": ["parakeet-tdt-0.6b-v3"],
}


def test_engine_models_is_superset_of_legacy_dict():
    current = catalog.engine_models()
    for engine, names in LEGACY_ENGINE_MODELS.items():
        assert engine in current
        assert set(names) <= set(current[engine])


def test_engine_models_matches_legacy_dict_exactly():
    """No new engine/model silently appeared without being an intentional catalog change,
    and nothing legacy was dropped."""
    current = catalog.engine_models()
    assert set(current.keys()) == set(LEGACY_ENGINE_MODELS.keys())
    for engine, names in LEGACY_ENGINE_MODELS.items():
        assert set(current[engine]) == set(names)


def test_every_engine_in_ENGINES_has_at_least_one_entry():
    for engine in catalog.ENGINES:
        assert catalog.models_for(engine), f"engine {engine!r} has no catalog entries"


def test_every_engine_has_exactly_one_recommended_default():
    for engine in catalog.ENGINES:
        recommended = [e for e in catalog.models_for(engine) if e.recommended]
        assert len(recommended) == 1, (
            f"engine {engine!r} has {len(recommended)} recommended entries, expected 1"
        )


def test_unique_engine_name_pairs_including_aliases():
    seen = set()
    for entry in catalog.entries():
        keys = [entry.name, *entry.aliases]
        for key in keys:
            pair = (entry.engine, key)
            assert pair not in seen, f"duplicate (engine, name) {pair!r}"
            seen.add(pair)


def test_every_sherpa_entry_has_download_url():
    """Any non-faster-whisper (sherpa-onnx-backed) entry must declare a download URL."""
    for entry in catalog.entries():
        if entry.engine != "faster-whisper":
            assert entry.download_url, f"{entry.engine}/{entry.name} missing download_url"


def test_faster_whisper_entries_have_no_download_url():
    """faster-whisper models are HF-managed (downloaded lazily by the library itself)."""
    for entry in catalog.models_for("faster-whisper"):
        assert entry.download_url is None


def test_engines_returns_declared_keys_in_order():
    assert catalog.engines() == list(catalog.ENGINES.keys())


def test_models_for_unknown_engine_is_empty():
    assert catalog.models_for("does-not-exist") == []


def test_get_resolves_canonical_name():
    entry = catalog.get("faster-whisper", "large-v3")
    assert entry is not None
    assert entry.name == "large-v3"


def test_get_resolves_alias():
    entry = catalog.get("faster-whisper", "large")
    assert entry is not None
    assert entry.name == "large-v3"

    entry = catalog.get("faster-whisper", "turbo")
    assert entry is not None
    assert entry.name == "large-v3-turbo"


def test_get_unknown_name_returns_none():
    assert catalog.get("faster-whisper", "does-not-exist") is None
    assert catalog.get("does-not-exist", "tiny") is None


def test_recommended_default_per_engine():
    assert catalog.recommended(engine="faster-whisper").name == "base"
    assert catalog.recommended(engine="parakeet").name == "parakeet-tdt-0.6b-v3"


def test_recommended_for_spanish_picks_parakeet():
    assert catalog.recommended(language="es").engine == "parakeet"
    assert catalog.recommended(language="es").name == "parakeet-tdt-0.6b-v3"


def test_recommended_for_english_picks_parakeet():
    assert catalog.recommended(language="en").engine == "parakeet"


def test_recommended_for_auto_or_none_does_not_force_parakeet():
    # "auto"/None carry no language preference, so the generic recommendation wins.
    assert catalog.recommended(language="auto").engine == "faster-whisper"
    assert catalog.recommended().engine == "faster-whisper"


def test_recommended_unknown_engine_raises():
    with pytest.raises(LookupError):
        catalog.recommended(engine="does-not-exist")


def test_filter_by_language_auto_returns_all():
    ents = catalog.entries()
    assert catalog.filter_by_language(ents, "auto") == tuple(ents)


def test_filter_by_language_narrows_to_supporting_entries():
    ents = catalog.entries()
    filtered = catalog.filter_by_language(ents, "es")
    # Every faster-whisper (multilingual) entry passes, plus parakeet (es in its list).
    assert all(e.languages is None or "es" in e.languages for e in filtered)
    assert any(e.engine == "parakeet" for e in filtered)


def test_filter_by_language_excludes_entries_missing_language():
    ents = catalog.entries()
    # No entry in the catalog declares Japanese.
    filtered = catalog.filter_by_language(ents, "ja")
    assert all(e.languages is None for e in filtered)
    assert all(e.engine != "parakeet" for e in filtered)


def test_entries_declaration_order_matches_display_order():
    names = [e.name for e in catalog.entries()]
    assert names.index("tiny") < names.index("base") < names.index("small")
    assert names.index("large-v3") < names.index("large-v3-turbo")
    assert names.index("large-v3-turbo") < names.index("parakeet-tdt-0.6b-v3")


def test_model_entry_is_frozen():
    entry = catalog.get("faster-whisper", "base")
    with pytest.raises(Exception):
        entry.name = "renamed"  # type: ignore[misc]
