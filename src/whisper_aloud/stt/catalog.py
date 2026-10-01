"""STT model catalog — single source of truth for engine/model metadata (WHISP-50).

`config.ENGINE_MODELS` and `model_download.MODEL_SOURCES` are both *derived* from this
module so there is exactly one place that declares which (engine, model name) pairs
exist, what they cost to download/run, and which one is recommended by default.

This module must not import `..config` (or anything that imports it) — `config.py`
imports *this* module to build `ENGINE_MODELS`, and a cycle would break at import time.
"""
from __future__ import annotations

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
)


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

    Raises LookupError if no entry matches (should not happen for a known engine).
    """
    candidates: tuple[ModelEntry, ...] = _ENTRIES
    if engine is not None:
        candidates = tuple(e for e in candidates if e.engine == engine)
    candidates = tuple(e for e in candidates if e.recommended)
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
