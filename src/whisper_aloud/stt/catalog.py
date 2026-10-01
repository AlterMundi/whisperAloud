"""STT model catalog — single source of truth for engine/model metadata (WHISP-50).

`config.ENGINE_MODELS` and `model_download.MODEL_SOURCES` are both *derived* from this
module so there is exactly one place that declares which (engine, model name) pairs
exist, what they cost to download/run, and which one is recommended by default.

This module must not import `..config` (or anything that imports it) — `config.py`
imports *this* module to build `ENGINE_MODELS`, and a cycle would break at import time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

# ISO 639-1 codes for the 25 European languages in the Parakeet TDT v3 model card.
_PARAKEET_V3_LANGUAGES: tuple[str, ...] = (
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
)


@dataclass(frozen=True)
class ModelEntry:
    """One (engine, model name) catalog entry with everything the UI/CLI need to show it."""

    engine: str
    name: str
    label: str
    size_mb: Optional[int]
    languages: Optional[tuple[str, ...]]  # None = multilingual/~99 langs
    streaming: bool
    license: str
    devices: tuple[str, ...]
    tier: str  # "fast" | "balanced" | "accurate"
    recommended: bool
    download_url: Optional[str] = None
    notes: str = ""
    aliases: tuple[str, ...] = ()
    # WHISP-56 audit item 4: true for an entry whose license is pending human legal
    # review (see its `notes`). Such an entry is still fully wired (config
    # validation, create_engine) for testers who hand-configure it, but is hidden
    # from the CLI/UI picker by default and can never be an auto-selected default.
    # Additive + defaults False so other in-flight catalog work (e.g. WHISP-42's
    # selector, which filters by ENGINES/badges) merges cleanly against this.
    needs_review: bool = False


# Human-facing label for each engine key. Order here is display order for an engine
# picker; `engines()` returns these keys in this order.
ENGINES: dict[str, str] = {
    "faster-whisper": "faster-whisper (Whisper)",
    "parakeet": "Parakeet (NVIDIA, sherpa-onnx)",
}


# Declaration order == display order within an engine.
_ENTRIES: tuple[ModelEntry, ...] = (
    ModelEntry(
        engine="faster-whisper",
        name="tiny",
        label="Whisper tiny",
        size_mb=75,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="fast",
        recommended=False,
        notes="Approx size (CTranslate2 int8).",
    ),
    ModelEntry(
        engine="faster-whisper",
        name="base",
        label="Whisper base",
        size_mb=145,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="fast",
        recommended=True,
        notes="Approx size (CTranslate2 int8). Default faster-whisper model.",
    ),
    ModelEntry(
        engine="faster-whisper",
        name="small",
        label="Whisper small",
        size_mb=484,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="balanced",
        recommended=False,
        notes="Approx size (CTranslate2 int8).",
    ),
    ModelEntry(
        engine="faster-whisper",
        name="medium",
        label="Whisper medium",
        size_mb=1530,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="balanced",
        recommended=False,
        notes="Approx size (CTranslate2 int8).",
    ),
    ModelEntry(
        engine="faster-whisper",
        name="large-v1",
        label="Whisper large-v1",
        size_mb=3100,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="accurate",
        recommended=False,
        notes="Approx size, same architecture class as large-v3 (1550M params).",
    ),
    ModelEntry(
        engine="faster-whisper",
        name="large-v2",
        label="Whisper large-v2",
        size_mb=3100,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="accurate",
        recommended=False,
        notes="Approx size, same architecture class as large-v3 (1550M params).",
    ),
    ModelEntry(
        engine="faster-whisper",
        name="large-v3",
        label="Whisper large-v3",
        size_mb=3100,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="accurate",
        recommended=False,
        notes="Approx size (CTranslate2 fp16/int8 mix).",
        aliases=("large",),
    ),
    ModelEntry(
        engine="faster-whisper",
        name="large-v3-turbo",
        label="Whisper large-v3-turbo",
        size_mb=1620,
        languages=None,
        streaming=False,
        license="MIT",
        devices=("cpu", "cuda"),
        tier="balanced",
        recommended=False,
        notes="Approx size (CTranslate2 int8). Distilled large-v3: near large-v3 "
        "accuracy at roughly medium-model speed.",
        aliases=("turbo",),
    ),
    ModelEntry(
        engine="parakeet",
        name="parakeet-tdt-0.6b-v3",
        label="Parakeet TDT 0.6B v3",
        size_mb=671,
        languages=_PARAKEET_V3_LANGUAGES,
        streaming=False,
        license="CC-BY-4.0",
        devices=("cpu", "cuda"),
        tier="fast",
        recommended=True,
        download_url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
        ),
        notes=(
            "671 MB int8 sherpa-onnx tarball. Recommended for es/en on CPU: "
            "bench/MATRIX.md shows ES WER 3.2% / RTF 0.151 and EN WER 10.5% "
            "(EN figure is suspected to be a bench text-normalization outlier, "
            "see docs/research/2026-10-01-sota-stt-offline/report.md)."
        ),
    ),
    ModelEntry(
        engine="nemotron-streaming",
        name="nemotron-3.5-streaming-560ms",
        label="Nemotron 3.5 ASR Streaming 0.6B (560ms)",
        size_mb=682,
        languages=("es", "en"),
        streaming=True,
        license="OpenMDW-1.1",
        devices=("cpu",),
        tier="balanced",
        recommended=False,
        download_url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2"
        ),
        notes=(
            "WHISP-56: genuinely cache-aware streaming (560 ms chunks), int8-only "
            "export, greedy_search decoding only (no hotwords/beam search for "
            "model_type='nemotron' yet). NEEDS-HUMAN: OpenMDW-1.1 license is pending "
            "legal review against the project's existing CC-BY-4.0/MIT STT "
            "dependencies (see docs/superpowers/specs/"
            "2026-10-01-whisp55-nemotron-sherpa-findings.md) -- not recommended "
            "until that review completes."
        ),
        needs_review=True,
    ),
)

# Intentionally NOT added to `ENGINES` (the UI/CLI engine-picker list): this model is
# wired end-to-end (catalog, config validation, create_engine) for development and
# testing, but kept out of the picker until the OpenMDW-1.1 license review above
# completes. `models_for`/`get`/`engine_models` all still find it via `_ENTRIES`.

# WHISP-46 e2e harness: a hidden "fake" engine/model entry, registered only
# when WHISPER_ALOUD_TEST_MODE=1 is set at *import time* (module-import-time
# decision, not a `hidden` flag filtered by entries()/engines()/models_for()
# -- see design doc rationale). This means a production install -- where the
# env var is never set -- never has "fake" in ENGINE_MODELS at all, so
# config.validate() rejects it exactly like any other unknown engine, even
# if an attacker-controlled config.json sets model.engine="fake".
if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
    _ENTRIES = _ENTRIES + (
        ModelEntry(
            engine="fake",
            name="fake",
            label="Fake (test only)",
            size_mb=0,
            languages=None,
            streaming=False,
            license="N/A",
            devices=("cpu",),
            tier="fast",
            recommended=False,
            notes="Deterministic test double — reads a sidecar .txt next to a "
            "fixture WAV. Never shown to real users.",
        ),
    )
    ENGINES["fake"] = "Fake (test only)"


def entries() -> tuple[ModelEntry, ...]:
    """All catalog entries, in declaration/display order."""
    return _ENTRIES


def engines() -> list[str]:
    """Engine keys in display order."""
    return list(ENGINES.keys())


def models_for(engine: str) -> list[ModelEntry]:
    """Catalog entries for one engine, in display order. Empty list for an unknown engine."""
    return [e for e in _ENTRIES if e.engine == engine]


def get(engine: str, name: str) -> Optional[ModelEntry]:
    """Look up an entry by engine + name, resolving aliases. None if not found."""
    for entry in models_for(engine):
        if entry.name == name or name in entry.aliases:
            return entry
    return None


def filter_by_language(ents: Iterable[ModelEntry], language: str) -> tuple[ModelEntry, ...]:
    """Entries that support `language`. "auto" (or falsy) passes everything through."""
    if not language or language == "auto":
        return tuple(ents)
    return tuple(e for e in ents if e.languages is None or language in e.languages)


def recommended(engine: Optional[str] = None, language: Optional[str] = None) -> ModelEntry:
    """The recommended entry, optionally narrowed by engine and/or language.

    Every engine has exactly one entry with `recommended=True`. When a language is
    given, an entry whose `languages` explicitly names it (a specialized model) wins
    over a multilingual (`languages=None`) recommended entry, since the specific
    recommendation is the more deliberate one (e.g. Parakeet for es/en over the
    generic faster-whisper default).

    An entry with `needs_review=True` (pending license review) is never returned,
    even if it is also marked `recommended=True` -- this should not currently
    happen (the only `needs_review` entry today has `recommended=False`), but this
    is a deliberate, defensive invariant (WHISP-56 audit item 4).

    Raises LookupError if no entry matches (should not happen for a known engine).
    """
    candidates: tuple[ModelEntry, ...] = _ENTRIES
    if engine is not None:
        candidates = tuple(e for e in candidates if e.engine == engine)
    candidates = tuple(e for e in candidates if e.recommended and not e.needs_review)
    if not candidates:
        raise LookupError(f"No recommended model for engine={engine!r}")

    if language and language != "auto":
        matched = filter_by_language(candidates, language)
        specific = tuple(e for e in matched if e.languages is not None)
        if specific:
            return specific[0]
        if matched:
            return matched[0]
        # No recommended candidate declares support for this language; fall back
        # to the general recommendation rather than raising.

    return candidates[0]


def engine_models() -> dict[str, list[str]]:
    """All valid model names per engine, including aliases — same shape as the legacy
    `config.ENGINE_MODELS` dict literal this replaces."""
    result: dict[str, list[str]] = {}
    for entry in _ENTRIES:
        names = result.setdefault(entry.engine, [])
        names.append(entry.name)
        names.extend(entry.aliases)
    return result
