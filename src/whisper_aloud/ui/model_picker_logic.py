"""Pure logic for the catalog-driven model picker (WHISP-42). No GTK/Adw import —
testable without a display, per this codebase's *_logic.py convention."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..stt import catalog, model_download
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

    Excludes any entry with `needs_review=True` (WHISP-56: a license pending human
    legal review, e.g. the real nemotron-streaming entry) — such an entry is never a
    default-list choice, only reachable through the full-catalog view (WHISP-42 audit
    MEDIUM #3).
    """
    by_language = catalog.filter_by_language(catalog.entries(), language)
    if device and device != "auto":
        by_language = [e for e in by_language if device in e.devices]
    return [e for e in by_language if not e.needs_review]


def entries_for_engine(language: str, device: str, engine: str) -> tuple[list[ModelEntry], bool]:
    """Entries for one `engine`, honoring `language`/`device` when possible.

    Returns `(entries, used_fallback)`. If `selectable_entries(language, device)`
    narrowed to `engine` is non-empty, that's returned with `used_fallback=False`.
    Otherwise falls back to `catalog.models_for(engine)` (bypassing the language/
    device filter entirely — the same escape hatch the full-catalog view uses) with
    `used_fallback=True`: silently keeping the *previous* engine's entries on screen
    (the WHISP-42 audit HIGH #2 bug) is worse than showing this engine's models even
    though they don't match the configured language — the user explicitly asked for
    this engine, so leaving the Model row showing a different engine's entry (and
    persisting it on Save) is the actual bug, not showing an imperfect-language match.

    `catalog.models_for(engine)` already excludes nothing by `needs_review` — that's
    intentional here too, matching the full-catalog view's "show everything for this
    engine" semantics once a user has gone looking for it via Advanced->Engine.
    """
    matches = [e for e in selectable_entries(language, device) if e.engine == engine]
    if matches:
        return matches, False
    return catalog.models_for(engine), True


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
    is_nonstandard_license(entry.license), "pending license review" if
    entry.needs_review (WHISP-56), else []. Download state is NOT included here
    (it needs filesystem state — see download_state_for)."""
    badges = []
    if entry.streaming:
        badges.append("Streaming")
    if is_nonstandard_license(entry.license):
        badges.append(entry.license)
    if entry.needs_review:
        badges.append("pending license review")
    return badges


def is_nonstandard_license(license_str: str) -> bool:
    return license_str not in _STANDARD_LICENSES


def needs_download_note(downloaded: bool) -> bool:
    return not downloaded


def _fmt_size(size_mb: Optional[int]) -> str:
    """Human-facing size string for a catalog entry, or "—" when unknown."""
    return f"{size_mb} MB" if size_mb is not None else "—"


def row_label(entry: ModelEntry, downloaded: bool) -> str:
    """Full display string for a Model-row or catalog-view row: the base
    "{label} · {size} · {tier}" text, with badges_for(entry) and the live download
    state each appended as a plain " · <badge>" suffix (WHISP-42 audit MEDIUM #4 — no
    per-row pill widgets yet; see settings_dialog.py's WHISP-42-followup TODO).

    "not downloaded" is only appended when `downloaded` is False — a downloaded entry
    gets no download-state suffix at all, keeping the common case's label short.
    """
    parts = [f"{entry.label} · {_fmt_size(entry.size_mb)} · {entry.tier}"]
    parts.extend(badges_for(entry))
    if not downloaded:
        parts.append("not downloaded")
    return " · ".join(parts)


# Device row <-> config.model.device mapping (audit MEDIUM #5): fixed display order.
# "auto" previously had no row option at all and was silently coerced to "cpu" on
# Save — it's now a first-class, round-trippable choice.
_DEVICE_OPTIONS: tuple[str, ...] = ("auto", "cpu", "cuda")


def device_row_index(device: str) -> int:
    """Index to preselect in the Device row for a configured device string. Defaults
    to "auto" (index 0) for an unrecognized value — the safest default, since it never
    excludes a model and never silently narrows to one backend."""
    return _DEVICE_OPTIONS.index(device) if device in _DEVICE_OPTIONS else 0


def device_for_index(index: int) -> str:
    """Config value for a Device row selection index. Defaults to "auto" for an
    out-of-range index (including Gtk.INVALID_LIST_POSITION) — belt-and-suspenders,
    should not happen since the row always has exactly these three options."""
    if 0 <= index < len(_DEVICE_OPTIONS):
        return _DEVICE_OPTIONS[index]
    return "auto"


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


@dataclass(frozen=True)
class DownloadState:
    downloaded: bool
    detection: str  # "sherpa-onnx-cache" | "hf-cache-heuristic" | "unknown"


def _hf_hub_cache_root() -> Path:
    """Best-effort resolution of huggingface_hub's cache root, matching the precedence
    huggingface_hub itself uses (HF_HOME/HUGGINGFACE_HUB_CACHE, else ~/.cache/huggingface/hub).
    Prefers reading huggingface_hub.constants.HF_HUB_CACHE directly over reimplementing
    that precedence, since huggingface_hub is already a transitive dependency (via
    faster-whisper) when faster-whisper is installed."""
    try:
        from huggingface_hub import constants

        return Path(constants.HF_HUB_CACHE)
    except Exception:
        home = os.environ.get("HF_HOME")
        if home:
            return Path(home) / "hub"
        return Path.home() / ".cache" / "huggingface" / "hub"


def download_state_for(entry: ModelEntry) -> DownloadState:
    """Whether `entry`'s weights are already on disk, and how we know.

    - any entry with a `download_url` (e.g. `parakeet`, and -- as of WHISP-86 --
      `nemotron-streaming`): delegates to model_download.is_present(entry.name) (the
      same tokens.txt/encoder*.onnx presence check model_download.ensure_model/
      download_model use). Derived from `download_url` rather than a hardcoded
      engine name, matching service/daemon.py's `_run_download` routing fix
      (WHISP-86 bug: this used to only check `engine == "parakeet"`, so Nemotron
      always showed as "not downloaded" in Settings even once its weights were on
      disk -- every sherpa-onnx tarball entry shares this one presence check).
    - engine == "faster-whisper": a *heuristic* over the Hugging Face hub cache — looks
      for a `models--Systran--faster-whisper-<canonical-name>` directory containing at
      least one snapshot with a `model.bin` file. This is best-effort: a nonstandard
      HF_HUB_CACHE layout or a custom `model.download_root` can produce a false
      "not downloaded" notice even though the model loads fine — documented limitation,
      not a correctness bug (worst case is an unnecessary notice, not a functional break).
    - Unknown engine/no download_url (e.g. a future catalog entry this module doesn't
      know how to check): detection = "unknown", downloaded = False (conservative:
      show the notice rather than hide it).
    """
    if entry.download_url:
        return DownloadState(
            downloaded=model_download.is_present(entry.name),
            detection="sherpa-onnx-cache",
        )
    if entry.engine == "faster-whisper":
        repo_dir = _hf_hub_cache_root() / f"models--Systran--faster-whisper-{entry.name}"
        snapshots = repo_dir / "snapshots"
        downloaded = snapshots.is_dir() and any(
            (snap / "model.bin").exists() for snap in snapshots.glob("*")
        )
        return DownloadState(downloaded=bool(downloaded), detection="hf-cache-heuristic")
    return DownloadState(downloaded=False, detection="unknown")
