"""Pure logic for the catalog-driven model picker (WHISP-42). No GTK/Adw import —
testable without a display, per this codebase's *_logic.py convention."""
from __future__ import annotations

from dataclasses import dataclass

from ..stt import catalog
from ..stt.catalog import ModelEntry

_STANDARD_LICENSES = frozenset({"MIT", "Apache-2.0", "CC-BY-4.0", "BSD-3-Clause"})


def selectable_entries(language: str, device: str) -> list[ModelEntry]:
    """Catalog entries visible in the Model row for a (language, device) pair.

    Language filtering reuses catalog.filter_by_language (auto passes everything).
    Device filtering keeps an entry only if `device` is in entry.devices, OR device
    is "auto"/falsy (passes everything, matching filter_by_language's convention).

    Tolerates entries whose `engine` isn't a key in `catalog.ENGINES` (e.g. a
    streaming engine registered on another branch before its label lands here) —
    this function only reads `.languages`/`.devices`, never `catalog.ENGINES`.
    """
    by_language = catalog.filter_by_language(catalog.entries(), language)
    if not device or device == "auto":
        return list(by_language)
    return [e for e in by_language if device in e.devices]


def default_index(
    entries: list[ModelEntry], current_engine: str, current_name: str, language: str
) -> int:
    """Index to preselect in `entries`.

    Preselects (current_engine, current_name) if present (by name or alias).
    Otherwise preselects catalog.recommended(language=language) if present in `entries`.
    Otherwise index 0. Never raises (mirrors the June spec's "never crash on stale
    config" requirement) — an unknown engine/name combination, or an empty `entries`
    list (returns 0, caller must guard len(entries) == 0 before indexing).
    """
    if not entries:
        return 0
    for i, e in enumerate(entries):
        if e.engine == current_engine and (e.name == current_name or current_name in e.aliases):
            return i
    try:
        recommended = catalog.recommended(language=language)
    except LookupError:
        return 0
    for i, e in enumerate(entries):
        if e.engine == recommended.engine and e.name == recommended.name:
            return i
    return 0


def badges_for(entry: ModelEntry) -> list[str]:
    """Ordered badge strings: "Streaming" if entry.streaming, entry.license if
    is_nonstandard_license(entry.license), else []. Download state is NOT included
    here (it needs filesystem state — see download_state_for)."""
    badges = []
    if entry.streaming:
        badges.append("Streaming")
    if is_nonstandard_license(entry.license):
        badges.append(entry.license)
    return badges


def is_nonstandard_license(license_str: str) -> bool:
    return license_str not in _STANDARD_LICENSES


def needs_download_note(downloaded: bool) -> bool:
    return not downloaded


def save_selection(engine: str, name: str) -> tuple[str, str]:
    """Validates (engine, name) resolves to a real catalog entry (via catalog.get) and
    returns the canonical (entry.engine, entry.name) to write into config.model.engine/
    .name — resolving any alias the UI might have round-tripped. Raises ValueError if
    catalog.get(engine, name) is None (should be unreachable in practice, since the UI
    only ever offers catalog-sourced items, but this is the save-path's last line of
    defense, mirroring config.validate()'s own belt-and-suspenders check)."""
    entry = catalog.get(engine, name)
    if entry is None:
        raise ValueError(f"Unknown (engine={engine!r}, name={name!r}) — not in the catalog")
    return entry.engine, entry.name
