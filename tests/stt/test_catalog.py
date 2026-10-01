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
    "nemotron-streaming": ["nemotron-3.5-streaming-560ms"],
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


# ── WHISP-56: nemotron-streaming catalog entry ──────────────────────────────


def test_nemotron_streaming_entry_exists_and_is_streaming():
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    assert entry is not None
    assert entry.streaming is True
    assert entry.license == "OpenMDW-1.1"
    assert entry.devices == ("cpu",)
    assert entry.download_url is not None


def test_nemotron_streaming_is_not_recommended():
    """Pending legal review (WHISP-56): must never be auto-selected as a default."""
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    assert entry.recommended is False


def test_nemotron_streaming_supports_es_and_en():
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    assert "es" in entry.languages
    assert "en" in entry.languages


def test_nemotron_streaming_engine_is_wired_into_engine_models():
    """config.validate() derives its valid engines from this, so it must be present
    even though the entry is kept out of the `ENGINES` UI picker (pending legal review)."""
    assert "nemotron-streaming" in catalog.engine_models()
    assert "nemotron-3.5-streaming-560ms" in catalog.engine_models()["nemotron-streaming"]


def test_nemotron_streaming_not_in_engines_picker_pending_license_review():
    assert "nemotron-streaming" not in catalog.ENGINES


# ── WHISP-56 CHANGES_REQUIRED item 4: ModelEntry.needs_review ───────────────


def test_needs_review_defaults_to_false():
    for entry in catalog.entries():
        if entry.engine != "nemotron-streaming":
            assert entry.needs_review is False, entry


def test_nemotron_streaming_needs_review_is_true():
    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    assert entry.needs_review is True


def test_recommended_never_returns_a_needs_review_entry(monkeypatch):
    """Defensive invariant: even if an entry were both recommended and
    needs_review, recommended() must skip it (WHISP-56 audit item 4)."""
    from whisper_aloud.stt.catalog import ModelEntry

    fake_entry = ModelEntry(
        engine="fake-pending-engine",
        name="fake-model",
        label="Fake pending-review model",
        size_mb=1,
        languages=None,
        streaming=False,
        license="Fake",
        devices=("cpu",),
        tier="fast",
        recommended=True,
        needs_review=True,
    )
    monkeypatch.setattr(catalog, "_ENTRIES", catalog._ENTRIES + (fake_entry,))
    with pytest.raises(LookupError):
        catalog.recommended(engine="fake-pending-engine")


class TestFakeEngineTestModeGating:
    """The "fake" catalog entry (WHISP-46 e2e harness) must only exist when
    WHISPER_ALOUD_TEST_MODE=1 -- registration is a module-import-time
    decision, so a production install (env var never set) never has it,
    even if an attacker-controlled config.json sets model.engine="fake"."""

    def test_fake_engine_absent_by_default(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        # Re-import catalog fresh to re-run its module-level conditional.
        import importlib

        from whisper_aloud.stt import catalog as catalog_module

        importlib.reload(catalog_module)
        try:
            assert "fake" not in catalog_module.engines()
            assert catalog_module.models_for("fake") == []
            assert "fake" not in catalog_module.engine_models()
        finally:
            importlib.reload(catalog_module)

    def test_fake_engine_present_in_test_mode(self, monkeypatch):
        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        import importlib

        from whisper_aloud.stt import catalog as catalog_module

        importlib.reload(catalog_module)
        try:
            assert "fake" in catalog_module.engines()
            assert catalog_module.get("fake", "fake") is not None
            assert "fake" in catalog_module.engine_models()
        finally:
            monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
            importlib.reload(catalog_module)  # restore module state for later tests
