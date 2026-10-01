# WHISP-42 — Catalog-Driven Settings Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded, non-catalog-aware "Model:" dropdown in `ui/settings_dialog.py`
with a catalog-driven picker (`AdwComboRow` "Model" + `AdwExpanderRow` "Advanced") per the design
doc. **This plan belongs to a follow-up ticket** (WHISP-42's implementation, dispatched after this
design); WHISP-42 itself produced only the design doc and this plan, per its brief (design-only,
no production/test code changes).

**Spec:** `docs/superpowers/specs/2026-10-01-whisp42-selector-redesign.md` — read it in full
before starting; this plan assumes its §0–§9 context and doesn't re-derive the reasoning.

**Two open questions the spec flags (§7) are NOT resolved by this plan** — they're product/design
calls for Fede, not implementation details:
1. Should `catalog.recommended()` gain a `device` parameter for true hardware-awareness, or is
   the filter-only interpretation (this plan's Task 2) sufficient?
2. English-only labels ("Model"/"Advanced", per spec §0's finding) vs. Spanish ("Modelo"/
   "Avanzado", per the literal brief) — this plan implements **English**, per the spec's decision;
   if Fede overrides that before implementation starts, Task 4/6's literal strings are the only
   things that change.

If either answer changes, adjust the relevant task before starting it — don't guess silently.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/whisper_aloud/stt/model_download.py` | export `is_present(name: str) -> bool` (public wrapper over the existing `_is_present(Path)`) | Modify |
| `src/whisper_aloud/ui/model_picker_logic.py` | `selectable_entries`, `default_index`, `badges_for`, `is_nonstandard_license`, `DownloadState`/`download_state_for`, `needs_download_note`, `save_selection` | Create |
| `tests/test_model_picker_logic.py` | unit tests for all of the above | Create |
| `src/whisper_aloud/ui/settings_dialog.py` | replace the "Model:" `Gtk.DropDown` rows (lines ~230, ~1023) and the `compute_device_dropdown` (~785) with the `AdwComboRow`/`AdwExpanderRow` boxed-list; add the download/license notices; add the full-catalog window; rewire `_on_save_clicked` and `_capture_ui_state` | Modify |
| `tests/test_settings_dialog_logic.py` | confirm unchanged (no new tests expected — see spec §2.2) | Verify only |
| `docs/superpowers/specs/2026-06-09-whisp42-settings-engine-selector-design.md` | already marked superseded by WHISP-42's design commit | No change (done) |

**Invariants:**
- `MODEL_RELOAD_FIELDS` and `detect_config_changes` in `config.py` are **not modified** — every
  field this plan's UI writes (`engine`, `name`, `device`, `compute_type`) is already in that set.
- `stt/catalog.py` is **not modified** — Task 2's "hardware-aware" filtering happens in
  `model_picker_logic.py`, not by adding a parameter to `catalog.recommended()` (open question 1
  above; this plan takes the "no catalog change" branch).
- `config.validate()`'s `valid_compute_types = ["int8", "float16", "float32"]` list is the source
  of truth for the new Compute Type row's options — don't hardcode a second list that could drift.
- Every new GTK/Adw widget keeps the dialog's existing `wa-*` CSS class convention where
  applicable (check `styles.py` for existing classes like `wa-dialog-window`/`wa-headerbar` before
  inventing new ones).

Run pytest as `python -m pytest` inside `~/.venvs/whisper_aloud` (system pytest lacks deps,
notably `pydbus`/`PyGObject`/`faster_whisper`). Confirm the implementation branch is rebased past
master commit `f839796` (test isolation in `conftest.py`) before running the suite — **do not run
pytest on a branch that lacks it**, per repo safety rules.

---

## Task 1: Export `model_download.is_present(name)`

**Files:**
- Modify: `src/whisper_aloud/stt/model_download.py`
- Test: `tests/stt/test_model_download.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stt/test_model_download.py — append
def test_is_present_true_when_model_files_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    (d / "encoder.onnx").write_bytes(b"\x00")
    assert md.is_present("parakeet-tdt-0.6b-v3") is True


def test_is_present_false_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert md.is_present("parakeet-tdt-0.6b-v3") is False


def test_is_present_false_for_partial_download(monkeypatch, tmp_path):
    """tokens.txt alone (no encoder*.onnx) is not a complete model."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = md.model_dir("parakeet-tdt-0.6b-v3")
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n")
    assert md.is_present("parakeet-tdt-0.6b-v3") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/stt/test_model_download.py -k is_present -v`
Expected: FAIL — `AttributeError: module 'whisper_aloud.stt.model_download' has no attribute
'is_present'`.

- [ ] **Step 3: Implement**

In `model_download.py`, after `_is_present`:

```python
def is_present(name: str) -> bool:
    """Public, name-based wrapper over _is_present — used by the Settings UI (WHISP-42) to
    show a model's downloaded/not-downloaded state without reaching into model_dir()/
    _is_present() directly."""
    return _is_present(model_dir(name))
```

Leave `_is_present(d: Path)` and `ensure_model`'s internal call to it unchanged — this is a pure
addition, not a rename, so nothing else in `model_download.py` or its existing tests/callers
changes.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/stt/test_model_download.py -v` — all pass, including pre-existing
tests (confirms this is additive only).

- [ ] **Step 5: Commit**

`feat(stt): export model_download.is_present(name) for the settings UI`

---

## Task 2: `model_picker_logic.py` — entry selection and badges (no filesystem)

**Files:**
- Create: `src/whisper_aloud/ui/model_picker_logic.py`
- Test: `tests/test_model_picker_logic.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_picker_logic.py
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
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_model_picker_logic.py -v`
Expected: FAIL — `ModuleNotFoundError: whisper_aloud.ui.model_picker_logic` (the whole file is
new; `download_state_for`'s tests are deliberately deferred to Task 3, which needs the filesystem
helper from Task 1 — don't add those tests here yet).

- [ ] **Step 3: Implement**

```python
# src/whisper_aloud/ui/model_picker_logic.py
"""Pure logic for the catalog-driven model picker (WHISP-42). No GTK/Adw import —
testable without a display, per this codebase's *_logic.py convention."""
from __future__ import annotations

from dataclasses import dataclass

from ..stt import catalog
from ..stt.catalog import ModelEntry

_STANDARD_LICENSES = frozenset({"MIT", "Apache-2.0", "CC-BY-4.0", "BSD-3-Clause"})


def selectable_entries(language: str, device: str) -> list[ModelEntry]:
    """Catalog entries visible in the Model row for a (language, device) pair."""
    by_language = catalog.filter_by_language(catalog.entries(), language)
    if not device or device == "auto":
        return list(by_language)
    return [e for e in by_language if device in e.devices]


def default_index(
    entries: list[ModelEntry], current_engine: str, current_name: str, language: str
) -> int:
    """Index to preselect in `entries`. See spec §2.1 for the fallback chain."""
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
    """Ordered badge strings: Streaming, then non-standard license. Download state is
    NOT included — it needs filesystem state (see download_state_for)."""
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
    """Resolves (engine, name) to the canonical catalog form. Raises ValueError if the
    pair isn't a real catalog entry (last line of defense — the UI should only ever
    offer catalog-sourced items)."""
    entry = catalog.get(engine, name)
    if entry is None:
        raise ValueError(f"Unknown (engine={engine!r}, name={name!r}) — not in the catalog")
    return entry.engine, entry.name
```

(`DownloadState`/`download_state_for` are added in Task 3, not here — this task is filesystem-free
by design so its tests never touch `tmp_path`/`monkeypatch` of cache dirs.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_model_picker_logic.py -v` — all pass.
Also run `python -m pytest tests/stt/test_catalog.py -v` (confirms this task didn't accidentally
monkeypatch-leak `catalog.entries` across test modules — pytest isolates monkeypatch per test, but
worth a sanity check given `test_selectable_entries_filters_by_device` patches it).

- [ ] **Step 5: Commit**

`feat(ui): add model_picker_logic — catalog-driven entry selection and badges`

---

## Task 3: `download_state_for` — per-engine download detection

**Files:**
- Modify: `src/whisper_aloud/ui/model_picker_logic.py`
- Test: `tests/test_model_picker_logic.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_picker_logic.py — append
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
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    entry = catalog.get("faster-whisper", "base")
    state = mpl.download_state_for(entry)
    assert state.downloaded is True
    assert state.detection == "hf-cache-heuristic"


def test_download_state_faster_whisper_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
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
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    entry = catalog.get("faster-whisper", "turbo")  # resolved via alias
    assert entry.name == "large-v3-turbo"
    state = mpl.download_state_for(entry)
    assert state.downloaded is True
```

Before writing these, **check which env var actually controls `huggingface_hub`'s cache root in
the installed version** (`python -c "from huggingface_hub import constants; print(constants.
HF_HUB_CACHE)"` inside the venv, or check `faster_whisper`'s own `download_root` default/
`huggingface_hub.constants` source) — `HF_HOME` (→ `$HF_HOME/hub`) is the documented modern
variable, but confirm against the installed version pinned in `pyproject.toml`/`requirements`
before hardcoding it, and prefer reading `huggingface_hub.constants.HF_HUB_CACHE` directly over
reimplementing the precedence logic, if that import is cheap enough to do lazily (it's already a
transitive dependency via `faster-whisper`).

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/test_model_picker_logic.py -k download_state -v`
Expected: FAIL — `AttributeError: module 'model_picker_logic' has no attribute
'download_state_for'`.

- [ ] **Step 3: Implement**

```python
# model_picker_logic.py — add imports and:
import os
from pathlib import Path

from ..stt import model_download


@dataclass(frozen=True)
class DownloadState:
    downloaded: bool
    detection: str  # "sherpa-onnx-cache" | "hf-cache-heuristic" | "unknown"


def _hf_hub_cache_root() -> Path:
    try:
        from huggingface_hub import constants
        return Path(constants.HF_HUB_CACHE)
    except Exception:
        # Fall back to the documented default precedence if huggingface_hub's
        # constants module shape ever changes.
        home = os.environ.get("HF_HOME")
        if home:
            return Path(home) / "hub"
        return Path.home() / ".cache" / "huggingface" / "hub"


def download_state_for(entry: ModelEntry) -> DownloadState:
    if entry.engine == "parakeet":
        return DownloadState(
            downloaded=model_download.is_present(entry.name),
            detection="sherpa-onnx-cache",
        )
    if entry.engine == "faster-whisper":
        repo_dir = _hf_hub_cache_root() / f"models--Systran--faster-whisper-{entry.name}"
        downloaded = repo_dir.is_dir() and any(
            (snap / "model.bin").exists()
            for snap in (repo_dir / "snapshots").glob("*")
        )
        return DownloadState(downloaded=downloaded, detection="hf-cache-heuristic")
    return DownloadState(downloaded=False, detection="unknown")
```

Adjust `_hf_hub_cache_root`'s exact env-var/constants lookup per what Step 1's research found —
the snippet above is this plan's best guess, not verified against the installed
`huggingface_hub` version.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_model_picker_logic.py -v` — all pass. Then
`python -m pytest tests/stt/test_model_download.py -v` (confirm Task 1's `is_present` still
passes; this task is its first real caller).

- [ ] **Step 5: Commit**

`feat(ui): add download_state_for — per-engine model-downloaded detection`

---

## Task 4: Replace the Model row with `AdwComboRow` in `settings_dialog.py`

**Files:**
- Modify: `src/whisper_aloud/ui/settings_dialog.py`

This task has no new pure-logic tests (the dialog itself isn't unit tested, per spec §8/this
repo's convention) — it's verified by manual smoke-test (Step 4) plus the full suite staying
green (nothing else should regress from a GTK-only change).

- [ ] **Step 1: Confirm the manual verification plan before touching code**

Since there's no automated widget test, write down what "works" means first:
1. Launch the dialog against a temp config/HOME (reuse whatever harness `run`/the project's dev
   workflow uses — check for a `.claude/launch.json` entry or `whisper-aloud --gui` invocation;
   do **not** touch the user's real `~/.config/whisper_aloud`).
2. Confirm the Model row shows the current `config.model.engine`/`.name` preselected.
3. Change `transcription.language` (via whatever existing control sets it) and reopen Settings —
   confirm the Model list changes (e.g. `ja` hides Parakeet, `es`/`en`/`auto` show it).
4. Confirm badges render (size/tier always; streaming/license absent today — no entry has them
   yet, so just confirm their absence doesn't crash or show empty pills).
5. Confirm the not-downloaded notice appears/disappears correctly for a model you know is/isn't
   cached locally (check `~/.cache/huggingface/hub` and `~/.cache/whisper_aloud/models/` — do
   **not** delete anything there, just observe).

- [ ] **Step 2: Build the boxed-list and `AdwComboRow`**

In `settings_dialog.py`:
1. Add `gi.require_version('Adw', '1')` and `from gi.repository import Adw` alongside the
   existing `Gtk`/`GLib`/`Gdk` imports.
2. Add `from .model_picker_logic import (badges_for, default_index, download_state_for,
   needs_download_note, save_selection, selectable_entries)` and `from ..stt import catalog`.
3. In `_add_general_page()`, replace the "Model selector" block (the `model_box`/
   `self.model_dropdown` lines) with:
   ```python
   self._model_list_box = Gtk.ListBox()
   self._model_list_box.add_css_class("boxed-list")
   self._model_list_box.set_selection_mode(Gtk.SelectionMode.NONE)

   self.model_row = Adw.ComboRow()
   self.model_row.set_title("Model")
   self._model_entries: list = []  # populated by _refresh_model_row()
   self._refresh_model_row()
   self._model_list_box.append(self.model_row)
   page.append(self._model_list_box)

   self._download_notice = Gtk.Label(wrap=True, xalign=0)
   self._download_notice.add_css_class("dim-label")
   self._download_notice.set_visible(False)
   page.append(self._download_notice)

   self._license_notice = Gtk.Label(wrap=True, xalign=0)
   self._license_notice.add_css_class("dim-label")
   self._license_notice.set_visible(False)
   page.append(self._license_notice)

   self.model_row.connect("notify::selected", self._on_model_row_changed)
   ```
4. Implement `_refresh_model_row(self)`: calls `selectable_entries(self._config.transcription.
   language, self._config.model.device)`, stores it in `self._model_entries`, builds a
   `Gtk.StringList` from `f"{e.label} · {_fmt_size(e.size_mb)} · {e.tier}"` strings (write a tiny
   `_fmt_size(mb)` helper: `f"{mb} MB"` or `"—"` for `None`), sets it via
   `self.model_row.set_model(...)`, and selects `default_index(self._model_entries, self._config.
   model.engine, self._config.model.name, self._config.transcription.language)`.
   - Badges: start with the row-label-only version (string includes size/tier) to get something
     working end-to-end first; add the `Gtk.SignalListItemFactory`-based badge pills (spec §1.2)
     as a visible follow-up within this same task once the plain version works — don't block the
     whole task on getting the factory exactly right first. If the factory approach proves fiddly
     under time pressure, ship the plain-string version and leave a `# TODO(WHISP-42-followup):
     per-row badge pills` comment — a working combo row without pills is still a correct
     superset of the old behavior (old code had zero metadata in the row at all), so don't let
     the badge polish block landing the core picker.
   - Call `_update_notices()` (next bullet) at the end of `_refresh_model_row`.
5. Implement `_update_notices(self)`: gets the currently selected entry (`self._model_entries[
   self.model_row.get_selected()]`, guard empty-list/`-1` same as every other dropdown read in
   this file already does), computes `download_state_for(entry)` and `badges_for(entry)`, sets
   `self._download_notice`'s text/visibility via `needs_download_note(state.downloaded)`, and
   `self._license_notice`'s text/visibility via whether `entry.license` is in `badges_for(entry)`
   (i.e. non-standard).
6. `_on_model_row_changed(self, row, _pspec)`: calls `_update_notices()` and, if this change was
   user-driven (not programmatic re-population from an Advanced→Engine change — guard with a
   `self._suppress_model_sync` flag set around programmatic `set_selected` calls, a pattern
   already implied by this file's existing dirty-tracking guards), re-syncs the Advanced→Engine
   row to the newly selected entry's engine (Task 5 wires the reverse direction).
7. Remove the now-dead duplicate model-dropdown block at the old `:1023` location (the `_on_save_
   clicked` one) — Task 6 replaces it.

- [ ] **Step 3: Run the full suite (no new tests yet, but confirm no import/collection break)**

Run: `python -m pytest -q`
Expected: same pass count as before this task (GTK import errors would show up here even though
most tests skip GTK-dependent modules via existing skip markers — check how the suite currently
handles `settings_dialog.py`'s GTK import requirement, e.g. `pytest.importorskip` or a module-level
skip, and make sure this task's new `Adw` import follows the same pattern).

- [ ] **Step 4: Manual smoke test**

Follow Step 1's checklist against a real (but disposable/temp-HOME) GUI launch. Do not skip this —
it's the only verification this task gets.

- [ ] **Step 5: Commit**

`feat(ui): replace hardcoded Model dropdown with a catalog-driven AdwComboRow`

---

## Task 5: `AdwExpanderRow` "Advanced" — Engine, Device, Compute type

**Files:**
- Modify: `src/whisper_aloud/ui/settings_dialog.py`

- [ ] **Step 1: Build the expander row**

In the same boxed-list from Task 4 (append after `self.model_row`):
```python
self.advanced_row = Adw.ExpanderRow()
self.advanced_row.set_title("Advanced")
self.advanced_row.set_expanded(False)

self.engine_row = Adw.ComboRow()
self.engine_row.set_title("Engine")
self.engine_row.set_model(Gtk.StringList.new(
    [catalog.ENGINES[k] for k in catalog.engines()]
))
self._engine_keys = catalog.engines()
self.engine_row.set_selected(self._engine_keys.index(self._config.model.engine))
self.advanced_row.add_row(self.engine_row)

self.device_row = Adw.ComboRow()
self.device_row.set_title("Device")
self.device_row.set_model(Gtk.StringList.new(["CPU", "CUDA (GPU)"]))
self.device_row.set_selected(1 if self._config.model.device == "cuda" else 0)
self.advanced_row.add_row(self.device_row)

self.compute_type_row = Adw.ComboRow()
self.compute_type_row.set_title("Compute type")
compute_types = ["int8", "float16", "float32"]  # mirrors config.validate()'s allowlist
self.compute_type_row.set_model(Gtk.StringList.new(compute_types))
self.compute_type_row.set_selected(
    compute_types.index(self._config.model.compute_type)
    if self._config.model.compute_type in compute_types else 0
)
self.advanced_row.add_row(self.compute_type_row)

self._model_list_box.append(self.advanced_row)

self.engine_row.connect("notify::selected", self._on_engine_row_changed)
```

(Remove the old `compute_device_dropdown` block from `_add_advanced_page()` entirely — Device now
lives here; `_add_advanced_page()` keeps its other sections, e.g. "Audio Processing", unchanged.
If removing the whole "Performance" section from `_add_advanced_page()` leaves it empty of
anything but "Audio Processing", consider whether the section heading `Gtk.Label(label=
"Performance")` should move too — implementer's call, cosmetic only.)

- [ ] **Step 2: Wire `_on_engine_row_changed` and the compute-type sensitivity rule**

```python
def _on_engine_row_changed(self, row, _pspec) -> None:
    if getattr(self, "_suppress_model_sync", False):
        return
    engine = self._engine_keys[row.get_selected()]
    self.compute_type_row.set_sensitive(engine != "parakeet")
    if engine == "parakeet":
        self.compute_type_row.set_tooltip_text("Not used by this engine")
    else:
        self.compute_type_row.set_tooltip_text(None)
    self._suppress_model_sync = True
    try:
        self._refresh_model_row(preferred_engine=engine)
    finally:
        self._suppress_model_sync = False
```

`_refresh_model_row` (Task 4) needs a `preferred_engine: Optional[str] = None` parameter: when
given, `default_index` should be computed against that engine (i.e. pick the first/recommended
entry *for that engine*) rather than `self._config.model.engine` — thread this through
`selectable_entries`/`default_index`'s existing signature by filtering `self._model_entries` to
`preferred_engine` first when set, falling back to the full language/device filter's
`default_index` call only on that engine-filtered subset. (`model_picker_logic.py` itself doesn't
need a new function for this — filtering the already-computed `entries` list by `.engine ==
preferred_engine` before calling `default_index` is enough; add this as a one-line filter in
`_refresh_model_row`, not a new pure-logic function, since "filter a list by one more attribute"
doesn't need its own test file entry — it's exercised by Task 4/5's manual smoke test.)

Also complete Task 4 Step 2's `_on_model_row_changed` reverse sync, now that `engine_row` exists:
```python
    self._suppress_model_sync = True
    try:
        entry = self._model_entries[row.get_selected()]
        self.engine_row.set_selected(self._engine_keys.index(entry.engine))
    finally:
        self._suppress_model_sync = False
```

- [ ] **Step 3: Set initial compute-type sensitivity**

Call the same sensitivity logic once at construction time (after both rows exist), since the
dialog opens with whatever `config.model.engine` already is — `self._on_engine_row_changed(self.
engine_row, None)` right after both connects, guarded by the same `_suppress_model_sync` pattern
so it doesn't trigger a spurious model-row repopulation on dialog open (it's already correctly
populated from Task 4's `_refresh_model_row()` call at construction).

- [ ] **Step 4: Run the full suite + manual smoke test**

Run: `python -m pytest -q` — same pass count as Task 4 (still no new automated tests for the
dialog). Manually: toggle Engine in Advanced, confirm Model row's list and selection update;
change Model row to the other engine's entry, confirm Advanced→Engine updates to match; confirm
Compute Type row greys out when Engine = Parakeet.

- [ ] **Step 5: Commit**

`feat(ui): add Advanced expander row for Engine/Device/Compute type`

---

## Task 6: Rewire `_on_save_clicked` and `_capture_ui_state`

**Files:**
- Modify: `src/whisper_aloud/ui/settings_dialog.py`

- [ ] **Step 1: Update `_on_save_clicked`**

Replace the old `models = ["tiny", ...]; self._config.model.name = models[...]` block (and the
`compute_device_dropdown` read, now superseded by `device_row`) with:
```python
selected_entry = self._model_entries[self.model_row.get_selected()]
engine, name = save_selection(selected_entry.engine, selected_entry.name)
self._config.model.engine = engine
self._config.model.name = name

devices = ["cpu", "cuda"]
self._config.model.device = devices[self.device_row.get_selected()]

compute_types = ["int8", "float16", "float32"]
self._config.model.compute_type = compute_types[self.compute_type_row.get_selected()]
```
Guard empty `self._model_entries`/`get_selected() == -1` the same defensive way the file already
guards `audio_device_dropdown`/`compute_device_dropdown` reads today (grep `!= -1` in this file
for the existing pattern and match it) — don't introduce a new error-handling style for this one
save path.

- [ ] **Step 2: Update `_capture_ui_state`**

Replace the old `"model": self.model_dropdown.get_selected()` dict entry (line ~529) with:
```python
"model_engine": self._engine_keys[self.engine_row.get_selected()],
"model_name": self._model_entries[self.model_row.get_selected()].name
    if self._model_entries else "",
"model_device": self.device_row.get_selected(),
"model_compute_type": self.compute_type_row.get_selected(),
```
(Capturing the resolved engine/name pair rather than a raw dropdown index is deliberate — unlike
the old flat list, `self.model_row`'s index means different things before/after an Advanced→
Engine change repopulates its model, so comparing raw indices across a repopulation would produce
false-positive or false-negative "unsaved changes" signals. Comparing the resolved `(engine,
name)` string pair is index-repopulation-safe.)

Also check the `_connect_dirty_tracking`/`_style_setting_rows_in_page` loops (lines ~569, ~601)
that iterate over known dropdown widgets (`self.model_dropdown` is referenced at both) — update
those lists to include `self.model_row`/`self.engine_row`/`self.device_row`/`self.compute_type_
row` instead of the removed `self.model_dropdown`/`self.compute_device_dropdown`, and confirm
`isinstance(widget, Gtk.DropDown)` checks (line ~601) need an `Adw.ComboRow` branch added (it is
not a `Gtk.DropDown` subclass) — check what that branch actually does (styling? signal
connection?) before assuming it's a one-line type swap.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: no change in pass/fail count from Task 5 (still dialog-only changes, no new automated
coverage — this task is pure wiring cleanup).

- [ ] **Step 4: Manual smoke test**

Change the model, save, confirm `config.model.engine`/`.name`/`.device`/`.compute_type` persist
(check the written `~/.config/whisper_aloud/config.json` in a disposable test HOME, or whatever
the project's existing manual-test convention is — do not use the real `~/.config/whisper_aloud`).
Reopen Settings, confirm the saved selection round-trips correctly into `default_index`.
Make an unsaved change and confirm the discard-confirmation dialog (`_show_discard_confirmation`)
still fires correctly off the new `_capture_ui_state` dict.

- [ ] **Step 5: Commit**

`refactor(ui): rewire settings save/dirty-tracking for the catalog-driven model picker`

---

## Task 7: Full-catalog view

**Files:**
- Modify: `src/whisper_aloud/ui/settings_dialog.py`

- [ ] **Step 1: Add the "View full catalog…" row**

Below `self.advanced_row` in the boxed-list (or as its own small `Gtk.ListBox`/`AdwActionRow`
right under it):
```python
catalog_row = Adw.ActionRow()
catalog_row.set_title("View full catalog…")
catalog_row.set_activatable(True)
catalog_row.connect("activated", self._on_view_catalog_clicked)
self._model_list_box.append(catalog_row)
```

- [ ] **Step 2: Implement the catalog window**

```python
def _on_view_catalog_clicked(self, _row) -> None:
    window = Gtk.Window(transient_for=self, modal=True)
    window.set_title("Model Catalog")
    window.set_default_size(560, 480)

    scrolled = Gtk.ScrolledWindow(vexpand=True)
    list_box = Gtk.ListBox()
    list_box.add_css_class("boxed-list")
    for entry in catalog.entries():
        row = Adw.ActionRow()
        row.set_title(entry.label)
        langs = "multilingual" if entry.languages is None else f"{len(entry.languages)} languages"
        size = f"{entry.size_mb} MB" if entry.size_mb is not None else "size unknown"
        state = download_state_for(entry)
        downloaded = "downloaded" if state.downloaded else "not downloaded"
        row.set_subtitle(f"{entry.engine} · {size} · {langs} · {entry.license} · {downloaded}")
        row.set_activatable(True)
        row.connect("activated", self._on_catalog_entry_chosen, entry, window)
        list_box.append(row)
    scrolled.set_child(list_box)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Gtk.HeaderBar())
    box.append(scrolled)
    window.set_child(box)
    window.present()

def _on_catalog_entry_chosen(self, _row, entry, window) -> None:
    window.close()
    self._suppress_model_sync = True
    try:
        self._refresh_model_row(preferred_engine=entry.engine, preferred_name=entry.name)
        self.engine_row.set_selected(self._engine_keys.index(entry.engine))
    finally:
        self._suppress_model_sync = False
    self._update_notices()
```

`_refresh_model_row` needs one more optional parameter, `preferred_name: Optional[str] = None`,
used alongside `preferred_engine` from Task 5 — when both are given, select that exact entry
instead of calling `default_index`'s fallback chain (the user just explicitly picked it from the
catalog, so honor the explicit choice, not the recommendation heuristic).

- [ ] **Step 2: Run the full suite + manual smoke test**

Run: `python -m pytest -q` — unchanged count. Manually: open the catalog view, confirm every
`catalog.entries()` row appears (cross-check count against `whisper-aloud models` CLI output),
pick one outside the current language filter (e.g. open with `transcription.language="ja"` and
pick the Parakeet entry from the catalog view), confirm it becomes the Model row's selection
despite being language-filtered out of that row's normal list.

- [ ] **Step 3: Commit**

`feat(ui): add full-catalog view, reachable from the model picker`

---

## Verification checklist (run before declaring WHISP-42 implementation complete)

- [ ] `python -m pytest -q` — full suite green, count increased only by Tasks 1–3's new unit
  tests (no dialog-level automated tests are expected, per this repo's convention).
- [ ] `tests/test_settings_dialog_logic.py` — unchanged, still green (confirms this plan never
  needed to touch `settings_logic.py`, per spec §2.2).
- [ ] `tests/stt/test_catalog.py`, `tests/test_config.py` — unchanged, still green (confirms
  `catalog.py` and `MODEL_RELOAD_FIELDS`/`detect_config_changes` were never modified).
- [ ] Manual smoke test from Tasks 4/5/6/7 completed against a disposable temp-HOME GUI launch —
  not against the real `~/.config/whisper_aloud`.
- [ ] Grep `settings_dialog.py` for any remaining reference to `self.model_dropdown` or
  `self.compute_device_dropdown` — should be zero (fully replaced, not left dangling).
- [ ] The two open questions at the top of this plan are still answered the same way they were
  when this plan was written — if either was overridden by Fede in the meantime, confirm the
  affected task was adjusted before it was implemented, not after.
