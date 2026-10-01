# WHISP-42 — Settings Engine/Model Selector, Redesigned Around the Catalog

- **Lattice:** WHISP-42 (subtask of WHISP-32; depends on WHISP-50, which is done)
- **Date:** 2026-10-01
- **Status:** design, pre-implementation
- **Supersedes:** `docs/superpowers/specs/2026-06-09-whisp42-settings-engine-selector-design.md`
  (approved 2026-06-09, **never implemented** — see "Current state" below).
- **Builds on:** WHISP-50 (`src/whisper_aloud/stt/catalog.py`, the single source of truth for
  `(engine, model)` metadata), WHISP-36 (Parakeet as a selectable engine, `MODEL_RELOAD_FIELDS`).

## 0. Current state (read this before the rest of the doc)

The June spec was approved but **nothing in it shipped**. `settings_dialog.py` today has no
engine selector at all:

- The "Model:" row (`ui/settings_dialog.py:230`, duplicated at `:1023`) is a plain
  `Gtk.DropDown.new_from_strings(["tiny (fastest)", "base", "small", "medium", "large (most
  accurate)"])` over a hardcoded `["tiny", "base", "small", "medium", "large"]` list — not
  engine-aware, and it silently excludes `large-v1`/`large-v2`/`large-v3-turbo`/`parakeet-tdt-*`
  from `ENGINE_MODELS`.
- `compute_type` (`config.model.compute_type`, in `MODEL_RELOAD_FIELDS`) has **no UI control at
  all** — it can only be set via env var or hand-edited config.
- The device dropdown (`compute_device_dropdown`, CPU/CUDA) lives in a separate "Advanced" stack
  page, unrelated to the model row.
- `ui/settings_logic.py` holds none of the helpers (`engine_options`, `resolve_model_selection`,
  `needs_download_note`) the June spec proposed — that spec's section 2 was design only.
- **No libadwaita widget is used anywhere in `src/whisper_aloud/ui/`** despite `gir1.2-adw-1`
  being a required system dependency (CLAUDE.md) and the project describing itself as a
  "GTK4/libadwaita GUI". The whole dialog is a `Gtk.Window` containing a `Gtk.Stack` of
  hand-built `Gtk.Box`/`Gtk.Label`/`Gtk.DropDown` rows (`_add_general_page`,
  `_add_advanced_page`, `_add_history_page` in `settings_dialog.py`). `AdwComboRow` and
  `AdwExpanderRow` in this ticket will be **the first real libadwaita widgets in the codebase**.
- Since the June spec, WHISP-50 landed `stt/catalog.py`: `ModelEntry` (engine, name, label,
  size_mb, languages, streaming, license, devices, tier, recommended, download_url, notes,
  aliases) is now the single source of truth. `config.ENGINE_MODELS` and
  `model_download.MODEL_SOURCES` are *derived* from it. `catalog.recommended(engine=None,
  language=None)` and `catalog.filter_by_language(entries, language)` already exist and are
  unit-tested (`tests/stt/test_catalog.py`).

This redesign replaces the June spec's engine-first, flat-dropdown design with a
recommendation-first design driven entirely by the catalog, per the 2026-10-01 research report
(`docs/research/2026-10-01-sota-stt-offline/report.md`, P0 WHISP-42 row) and competitor patterns
(`docs/research/2026-10-01-sota-stt-offline/notes/apps_packaging.md`).

### Scope (decided)
A catalog-driven model picker: one recommended-model `AdwComboRow` at the top, language-filtered;
an `AdwExpanderRow` "Advanced" for engine/device/compute type as a manual override; a
not-downloaded notice sized from the catalog; a non-standard-license notice; a full-catalog view.
Pure logic in `ui/model_picker_logic.py` (new) plus small additions to `ui/settings_logic.py`.
GTK/Adw stays thin glue. `config.model.engine`/`.name`/`.device` save unchanged in shape;
`compute_type` gets its first UI control.

### Non-goals (YAGNI, explicitly deferred)
- **No in-app download with a progress bar.** The daemon still lazy-downloads on first use
  (sherpa-onnx tarball via `model_download.ensure_model`, or faster-whisper's own HF-cache
  download inside `WhisperModel(...)`). This ticket only shows a notice with the expected size.
  Follow-up ticket: **WHISP-58 "In-app model download with progress"** (number reserved here;
  create it in Lattice when this ticket is picked up) — a `Downloading(bytes, total)` D-Bus
  signal from `model_download._download`'s existing progress hook (already computed, currently
  only logged) plus a progress row in the dialog.
- **No streaming-aware recording-mode UI.** WHISP-56/57 (streaming engine protocol + daemon
  session) haven't landed; see §5 for how this picker degrades today and upgrades later.
- **No model deletion/management UI.** Picking a different model doesn't offer to free disk
  space for the old one.
- **No gettext/i18n scaffolding.** See §4 — this ticket's brief assumed existing Spanish UI
  strings; that premise is false (see finding below), and introducing i18n infrastructure for one
  dialog would be a large, unscoped change.
- **No CUDA capability probing.** The Advanced device row still just offers CPU/CUDA; whether
  CUDA is actually present is unchanged from today (the faster-whisper/Parakeet engines already
  fall back to CPU at load time if CUDA isn't usable).

### Finding: the brief's "UI is in Spanish today" premise does not hold
The brief asks to "check how strings are handled" because "the UI is in Spanish today." A repo
search (`grep -rn "[áéíóúñÁÉÍÓÚÑ]" src/whisper_aloud/ui/`) found **zero** Spanish strings in the
GUI; every dialog, button, and label in `ui/` is English ("Settings", "Cancel", "Model:",
"Microphone:", …). There is also no `gettext`/`locale`/translation machinery anywhere in
`src/whisper_aloud/`. What *is* in Spanish is the project's internal docs: the 2026-10-01 research
report and its P0 table (which literally names this ticket's own `AdwComboRow`/`AdwExpanderRow`
labels). The brief's "Modelo"/"Avanzado" wording is most likely carried over from that Spanish
research doc, not a reflection of the shipped GUI's actual language.

**Decision:** new rows in this ticket use **English labels** ("Model", "Advanced") to match every
other string in the dialog ("Model:", "Microphone:", "Compute device:"). Shipping one Spanish row
label next to nineteen English ones would be a worse, more confusing regression than not matching
the brief's literal wording. If product wants the GUI in Spanish (or translated), that is a
separate, larger ticket (add `gettext` + `.po` files + a language-detection or settings toggle)
that should be scoped on its own — flagging this as an open question for Fede rather than
guessing at it here, per the "ambiguous brief" rule.

## 1. UX (libadwaita widgets)

### 1.1 Layout inside the existing "Transcription" section
`_add_general_page()`'s "Transcription" section (`ui/settings_dialog.py` around line 217) is
replaced by a `Gtk.ListBox` with the `boxed-list` CSS class (the standard way to host `Adw.*Row`
widgets outside a full `Adw.PreferencesWindow` — GTK4/libadwaita apps use this pattern when only
part of a window is "Adw-flavored"), containing:

```
┌─ boxed-list ──────────────────────────────────────────────────────┐
│ ⚙ Model                                           Whisper base ▾ │   <- AdwComboRow
│   145 MB · fast · downloaded                                      │
├─────────────────────────────────────────────────────────────────┤
│ ▸ Advanced                                                        │   <- AdwExpanderRow
│    (expanded: Engine, Device, Compute type rows)                  │
└─────────────────────────────────────────────────────────────────┘
  ⓘ Parakeet TDT 0.6B v3 (671 MB) downloads the first time you use it.
  ⓘ This model's license (OpenMDW-1.1) is non-standard — see details.
  View full catalog…
```

### 1.2 `AdwComboRow` "Model"
- **Title:** `"Model"`. **Subtitle:** none (per-row detail goes in the popover items, not a
  static subtitle, since the subtitle can't change per-selection without extra plumbing an
  `AdwComboRow` doesn't need).
- **Model list source:** `model_picker_logic.selectable_entries(language, device)` — catalog
  entries where `filter_by_language` passes for the configured `transcription.language`, further
  filtered to entries whose `devices` tuple contains the configured `model.device` ("auto" passes
  everything, since every current catalog entry lists `("cpu", "cuda")` anyway — see §1.3).
  "auto" language shows every entry, per `filter_by_language`'s existing "auto passes everything"
  rule — **this means a user on `language="auto"` with `device="cpu"` still sees both engines,
  including the English-only-by-virtue-of-general-multilingual Whisper models and the
  25-language Parakeet model**, which is correct: "auto" is the model's job to detect, not a
  filter criterion.
- **Row item widget:** an `AdwComboRow` renders each item via a factory producing a
  `Gtk.Label`+suffix box (not plain strings, since we need badges per §1.2.1). Use
  `Gtk.SignalListItemFactory` with `AdwComboRow.set_factory()`/`set_list_factory()` (list-vs-
  selected-item factories so the badges show in the dropdown list without cluttering the closed
  row — closed-row factory just shows `label` via `set_expression`).
- **Row format:** `"{label} · {size} · {tier}"`, e.g. `"Whisper base · 145 MB · fast"`,
  `"Parakeet TDT 0.6B v3 · 671 MB · fast"`. Multilingual entries show no language count; the
  25-language Parakeet entry doesn't show its language list inline (too long) — that detail lives
  in the full-catalog view (§1.5).
- **Badges (suffix widgets, small pill-style labels via CSS, not icons — consistent with no
  existing icon usage in this dialog):**
  - `streaming` → a "Streaming" pill, shown only when `entry.streaming` is `True`. Today this
    badge never appears (no catalog entry has `streaming=True` yet) — see §5.
  - `license` → shown only for a **non-standard** license (see §1.2.2's allowlist) — e.g.
    `"OpenMDW-1.1"` as a pill. MIT/CC-BY-4.0/Apache-2.0 show no pill (reduces visual noise for
    the common case).
  - downloaded/not-downloaded → a small dot or "Downloaded"/"Not downloaded" pill, computed per
    §2.3. This is the one badge shown for *every* row, since it's the single most
    decision-relevant fact once size is already in the label.
- **Default/preselected index:** `model_picker_logic.default_index(entries, config.model.engine,
  config.model.name)` — preselect the currently configured `(engine, name)` if it's still present
  in the filtered list; otherwise preselect `catalog.recommended(language=config.transcription.
  language)` (see §1.2.2 for the language='auto' + hardware-tie-breaking case, and §6 for the
  product decision this is built on). Never silently fall back to index 0 of an arbitrary list —
  always the catalog's own recommendation.
- **Hardware awareness ("recommended ... for the configured language and hardware"):** the
  catalog's `recommended()` has no device/hardware parameter today, only `engine` and `language`.
  Honoring "hardware" without changing `catalog.py` (out of scope — `catalog.py` belongs to
  WHISP-50, already done) means: when CUDA is configured (`config.model.device == "cuda"`),
  `default_index` prefers the `recommended(language=...)` entry only if its `devices` tuple
  includes `"cuda"` (true for every current entry); it does **not** attempt to pick a
  "best-for-GPU" variant distinct from the general recommendation, since the catalog doesn't
  encode a per-device recommendation. This is a documented gap, not silently glossed over — see
  Open Questions.

#### 1.2.1 Badge helper functions (pure, in `model_picker_logic.py`)
```python
def badges_for(entry: ModelEntry) -> list[str]:
    """Ordered badge strings for one entry: streaming, non-standard license, download state
    is NOT included here (it needs filesystem state — see download_state_for)."""
```
Returns e.g. `["Streaming"]`, `["OpenMDW-1.1"]`, `["Streaming", "OpenMDW-1.1"]`, or `[]`.

#### 1.2.2 Non-standard license allowlist
```python
_STANDARD_LICENSES = frozenset({"MIT", "Apache-2.0", "CC-BY-4.0", "BSD-3-Clause"})

def is_nonstandard_license(license_str: str) -> bool:
    return license_str not in _STANDARD_LICENSES
```
Today only `OpenMDW-1.1` (reserved for the Nemotron streaming entry per the research report;
not in the catalog yet) would trigger this. All current catalog entries (`MIT`, `CC-BY-4.0`) are
"standard" and show no license pill. The allowlist is a closed set reviewed by a human when a new
license shows up in the catalog — not an attempt to parse SPDX.

### 1.3 `AdwExpanderRow` "Advanced"
- **Title:** `"Advanced"`. Collapsed by default (`set_expanded(False)`) — this is a manual
  override path for users who know they want a specific engine/device/compute type, not the
  primary flow.
- **Child rows**, added via `add_row()`:
  1. **Engine** — `AdwComboRow`, items from `catalog.engines()` rendered via
     `catalog.ENGINES[key]` labels (`"faster-whisper (Whisper)"`, `"Parakeet (NVIDIA,
     sherpa-onnx)"`). Changing this repopulates the top Model combo row's list
     (`models_for(engine)` filtered by language) **and** re-syncs `default_index` — i.e. picking
     an engine in Advanced is two-way bound with the Model row above, same as the June spec's
     engine→model cascade, just inverted (engine is now the override, not the primary control).
  2. **Device** — `AdwComboRow`, `["CPU", "CUDA (GPU)"]` → `["cpu", "cuda"]` (replaces today's
     `compute_device_dropdown`, same two options, same mapping).
  3. **Compute type** — `AdwComboRow`, `["int8", "float16", "float32"]` (matches
     `config.validate()`'s `valid_compute_types`) — **net-new control**; `compute_type` has no UI
     today (see §0). Only meaningfully affects `faster-whisper` (CTranslate2 quantization);
     sherpa-onnx/Parakeet ignores it today, so when `engine == "parakeet"` this row is set
     insensitive (`set_sensitive(False)`) with a tooltip "Not used by this engine" rather than
     hidden, so the layout doesn't jump.
- **Why "Advanced" holds engine too, not just device/compute-type:** the ticket brief's §1 lists
  "engine, device and compute type" together under Advanced, and `apps_packaging.md`'s
  Vocalinux pattern ("Speech Model" simple section + "Advanced" for "engine type, model size and
  specialization") supports treating engine choice itself as the override axis, with the
  recommended *model* as the primary, opinionated default — not an engine picker that happens to
  also pick a model.

### 1.4 Not-downloaded notice
- An `Adw.Banner`-style or a `Gtk.Revealer`-wrapped inline `Gtk.Label` (dimmed, wrapping) directly
  under the boxed-list, matching the June spec's existing "dimmed Gtk.Label" pattern but now
  catalog-sourced: `f"{entry.label} ({entry.size_mb} MB) downloads the first time you use it."`
  (falls back to `f"{entry.label} downloads the first time you use it."` when `size_mb is None`,
  which doesn't happen today but is a real `Optional[int]` in `ModelEntry`).
- **Visibility:** `model_picker_logic.needs_download_note(downloaded: bool) -> bool` — simply
  `not downloaded`. (The June spec's `needs_download_note(engine, model_present)` hardcoded
  "only Parakeet downloads lazily"; that's now false — both engines can be "not yet downloaded"
  from this dialog's point of view, since a user has very likely never run every `ENGINE_MODELS`
  entry. See §2.3 for what "downloaded" means per engine.)
- Recomputed on every Model-row selection change (and indirectly on every Advanced→Engine change,
  since that changes the Model row's selection).
- **What happens when the user saves with a not-downloaded model selected (§3):** nothing blocks
  the save. The existing reload plumbing (`detect_config_changes(...).requires_model_reload`) is
  unchanged by this ticket; the *daemon*, not the dialog, performs the lazy download on the next
  reload/transcription, exactly as today. The dialog's job is only to set the expectation via the
  notice — not to pre-download, not to block Save, not to show a spinner (that's WHISP-58).

### 1.5 License notice
- A second, separate inline notice (same `Gtk.Revealer`/`Gtk.Label` pattern), shown when
  `is_nonstandard_license(entry.license)`: `f"This model's license ({entry.license}) differs from
  this app's MIT license. <details-or-link>"`. No catalog entry triggers this today (see §1.2.2);
  this is forward-looking for the Nemotron streaming entry (§5) and is specified now so WHISP-56
  doesn't also need a settings-dialog change just to surface its license.
- No dedicated "details" surface is built in this ticket (no popover with full license text) —
  the label itself states the license identifier; a follow-up can link to a license page if a
  human reviews OpenMDW-1.1's terms (the research report flags "full text of OpenMDW-1.1" as an
  unresolved gap — this ticket doesn't resolve it, it only makes sure the UI doesn't hide the
  identifier once such a model exists).

### 1.6 "View full catalog"
- A plain `Gtk.LinkButton`-styled row (or an `AdwActionRow` with a chevron) below the boxed-list:
  `"View full catalog…"`. Opens a small secondary `Gtk.Window` (modal child of Settings, same
  pattern as `_show_discard_confirmation`'s `Gtk.MessageDialog`) listing **every** `catalog.
  entries()` row — not language/device filtered — in a read-only `Gtk.ColumnView` or a simple
  `Gtk.ListBox` of `AdwActionRow`s, one per entry, showing engine, label, size, languages (full
  list or "multilingual"), license, tier, streaming, recommended-for-default flag, and
  downloaded/not. This gives a way to find and manually select a model the primary combo row's
  language filter would otherwise hide (e.g. a non-`auto`, non-Parakeet-language user who still
  wants to try Parakeet knowing it may mis-transcribe).
- Selecting a row in the catalog view sets that `(engine, name)` as the pending selection and
  closes the window, updating both the Model row and the Advanced→Engine row to match (same sync
  as §1.3). This is the only way to cross the language filter from the main UI.
- This is new UI surface (the CLI's `whisper-aloud models` table is the only existing "full
  catalog" view, and it's terminal-only) — kept intentionally minimal (no search/filter inside
  it) since building a polished catalog browser is not this ticket's point; the point is that one
  must exist so the language filter in §1.2 is never a dead end.

## 2. Pure-logic module boundaries

### 2.1 New module: `src/whisper_aloud/ui/model_picker_logic.py`
Pure functions over `stt.catalog.ModelEntry`, importing only `..stt.catalog` and
`..stt.model_download` (for the download-state helper, §2.3) — no GTK/Adw import, so it's
testable without a display. Mirrors the existing split where `ui/settings_logic.py` holds
GTK-adjacent-but-pure helpers and `ui/history_logic.py`/`ui/level_meter_logic.py`/
`ui/main_window_logic.py` hold similarly pure logic for their respective widgets.

```python
"""Pure logic for the catalog-driven model picker (WHISP-42)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..stt import catalog, model_download
from ..stt.catalog import ModelEntry

_STANDARD_LICENSES = frozenset({"MIT", "Apache-2.0", "CC-BY-4.0", "BSD-3-Clause"})


def selectable_entries(language: str, device: str) -> list[ModelEntry]:
    """Catalog entries visible in the Model row for a (language, device) pair.

    Language filtering reuses catalog.filter_by_language (auto passes everything).
    Device filtering keeps an entry only if `device` is in entry.devices, OR device
    is "auto" (passes everything, matching filter_by_language's convention).
    """


def default_index(entries: list[ModelEntry], current_engine: str, current_name: str,
                   language: str) -> int:
    """Index to preselect in `entries`.

    Preselects (current_engine, current_name) if present (by name or alias).
    Otherwise preselects catalog.recommended(language=language) if present in `entries`.
    Otherwise index 0. Never raises (mirrors the June spec's "never crash on stale
    config" requirement) — an unknown engine/name combination, or an empty `entries`
    list (returns 0, caller must guard len(entries) == 0 before indexing).
    """


def badges_for(entry: ModelEntry) -> list[str]:
    """Ordered badge strings: "Streaming" if entry.streaming, entry.license if
    is_nonstandard_license(entry.license), else []."""


def is_nonstandard_license(license_str: str) -> bool:
    return license_str not in _STANDARD_LICENSES


@dataclass(frozen=True)
class DownloadState:
    downloaded: bool
    detection: str  # "sherpa-onnx-cache" | "hf-cache-heuristic" | "unknown"


def download_state_for(entry: ModelEntry) -> DownloadState:
    """Whether `entry`'s weights are already on disk, and how we know.

    - engine == "parakeet" (sherpa-onnx models, entry.download_url is set): delegates to
      model_download.model_dir(entry.name) + the same tokens.txt/encoder*.onnx presence
      check model_download._is_present uses today (needs a small export of that check,
      or re-implementing the two-line glob here — see Open Questions). detection =
      "sherpa-onnx-cache".
    - engine == "faster-whisper": no download_url, no model_download entry. Detection is
      a *heuristic* over the Hugging Face hub cache (~/.cache/huggingface/hub, or
      $HF_HOME / $HUGGINGFACE_HUB_CACHE if set — same precedence huggingface_hub uses):
      look for a `models--Systran--faster-whisper-<canonical-name>` directory containing
      at least one snapshot with a `model.bin` file. `<canonical-name>` resolves aliases
      first (`"turbo"` -> `"large-v3-turbo"`, `"large"` -> `"large-v3"| see catalog
      aliases). detection = "hf-cache-heuristic". This is best-effort: a user who set a
      custom `model.download_root ` (ModelConfig.download_root) or a nonstandard
      HF_HUB_CACHE layout may see a false "not downloaded" notice even though the model
      loads fine — documented as a known limitation (Open Questions), not a correctness
      bug to fix here, since the cost of a false "not downloaded" notice is just an
      unnecessary label, not a functional break.
    - Unknown engine (future catalog entries): detection = "unknown", downloaded = False
      (conservative: show the notice rather than hide it).
    """


def needs_download_note(downloaded: bool) -> bool:
    return not downloaded


def save_selection(engine: str, name: str) -> tuple[str, str]:
    """Validates (engine, name) resolves to a real catalog entry (via catalog.get) and
    returns the canonical (entry.engine, entry.name) to write into config.model.engine/
    .name — resolving any alias the UI might have round-tripped. Raises ValueError if
    catalog.get(engine, name) is None (should be unreachable in practice, since the UI
    only ever offers catalog-sourced items, but this is the save-path's last line of
    defense, mirroring config.validate()'s own belt-and-suspenders check)."""
```

### 2.2 Additions to `ui/settings_logic.py`
Nothing from the June spec's `engine_options`/`engine_display_label`/`models_for_engine`/
`resolve_model_selection` survives — `stt.catalog` already provides `engines()`, `ENGINES`
(labels), `models_for()`, and the new `model_picker_logic.default_index()` replaces
`resolve_model_selection`. `settings_logic.py` gains no new functions for this ticket; it keeps
`has_unsaved_changes`/`should_block_close`/`should_auto_close_on_focus_loss`/
`normalize_language_input` unchanged. (`_capture_ui_state()` in the dialog does need a new
`"model_engine"`/`"model_name"` entry — see §3 — but that's a dialog-internal dict key, not a
new pure-logic function.)

### 2.3 Download-state detection, concretely
`model_download.py` currently exposes `_is_present(d: Path) -> bool` as a private, underscored
helper (only used internally by `ensure_model`). This ticket needs it from the UI layer, so part
of the implementation plan (§ in the companion plan doc) renames/exports it as
`model_download.is_present(name: str) -> bool` (public, taking a model name like `ensure_model`
does, not a raw path) — a one-line, backward-compatible addition (`_is_present` can stay as the
path-taking internal helper `is_present` delegates to, or be inlined; implementer's call).
`model_picker_logic.download_state_for` calls `model_download.is_present(entry.name)` for
`parakeet`-engine entries only; faster-whisper entries use the new HF-cache heuristic described
above, implemented in `model_picker_logic.py` itself (it doesn't belong in `model_download.py`,
which is specifically the sherpa-onnx downloader).

### 2.4 Save → config mapping
Unchanged in shape from the June spec's §3 "Save" bullet, now going through
`model_picker_logic.save_selection`:
```python
engine, name = model_picker_logic.save_selection(
    self._engine_keys[self.engine_row.get_selected()],
    self._pending_model_name,  # tracked across Model-row and catalog-view selection
)
self._config.model.engine = engine
self._config.model.name = name
self._config.model.device = ["cpu", "cuda"][self.device_row.get_selected()]
self._config.model.compute_type = ["int8", "float16", "float32"][self.compute_type_row.get_selected()]
```
Language/audio save logic is unchanged (out of this ticket's scope — `transcription.language` is
an *input* to the model picker, not something this ticket's UI writes).

## 3. Interaction with daemon reload

No change to `MODEL_RELOAD_FIELDS` (`{"engine", "name", "device", "compute_type"}` in
`config.py`) or `detect_config_changes` — every field this dialog's new rows write is already in
that set, so picking a different model, engine, device, or compute type and saving already
triggers `requires_model_reload` and the existing daemon `ReloadConfig` path.

**What happens when the user picks a not-downloaded model:**
1. Dialog shows the §1.4 notice as soon as the row is selected (before Save) — the user commits
   to the choice knowing the size.
2. On Save, `config.model.engine`/`.name` are written and `on_save_callback` fires exactly as
   today — no special-casing for "not downloaded" in the save path itself.
3. The daemon's reload path re-constructs the engine (`create_engine(config)`) and calls
   `.load_model()`/`.load()`. For Parakeet this is `model_download.ensure_model(name)`
   (synchronous network download + extract) inside that call; for faster-whisper it's
   `WhisperModel(model, ...)`'s own lazy HF download. **Both are currently synchronous and
   blocking** — the daemon's reload call blocks until the download finishes (or fails), which
   means the *next* transcription request after a model-switching Save will stall for as long as
   the download takes, with only the daemon's log output (`"downloading model: %d%%"` from
   `model_download._download`'s hook) as feedback, no UI indication. **This is an existing
   behavior this ticket doesn't change** (the June spec's non-goals already excluded in-UI
   download/progress), but it's now more reachable than before (any engine's models can trigger
   it, not just the single previously-undiscoverable Parakeet path), so it's called out here
   explicitly as the main risk this ticket's UX decisions create (§7) and the reason WHISP-58
   (§0) exists as a near-term follow-up rather than a someday-maybe.
4. If the download fails (network error, bad tarball), the existing error path is unchanged —
   whatever `ReloadConfig`/the daemon does today on an engine-load exception. This ticket does not
   add new error handling for that case; it inherits whatever WHISP-36 already does.

## 4. Accessibility and i18n

- **Finding:** see "Finding: the brief's 'UI is in Spanish today' premise does not hold" in §0 —
  there is no i18n infrastructure (`gettext`, `.po`, `locale`) anywhere in `src/whisper_aloud/`.
  All new strings in this ticket are plain Python string literals in English, consistent with
  every existing string in `ui/`.
- **Accessibility of the new widgets:**
  - `AdwComboRow`/`AdwExpanderRow` are standard libadwaita widgets with built-in keyboard
    navigation (Tab/Space/Enter/Arrow keys) and screen-reader semantics (they expose
    `Gtk.Accessible` roles out of the box) — this is a net accessibility *improvement* over the
    current hand-rolled `Gtk.Box` + `Gtk.Label` + `Gtk.DropDown` rows, which have no row-level
    accessible name grouping a label with its control.
  - Badge pills (§1.2) must not be the *only* way information reaches the user — size and tier
    are already in the row's primary label text (`"{label} · {size} · {tier}"`), so a
    screen-reader reading the row's accessible name gets the same information as the sighted
    badge. The streaming/license pills are supplementary (only shown when true/non-standard), so
    their content should also be folded into the row's `Gtk.Accessible:description` (e.g. "Model:
    Parakeet TDT 0.6B v3, 671 megabytes, fast tier, OpenMDW license, not yet downloaded") rather
    than relying on sighted pill color/shape alone.
  - The not-downloaded and license notices (§1.4, §1.5) use `Gtk.Label`, which is
    screen-reader-visible by default; no `aria`-equivalent work needed beyond what GTK4 already
    provides for a realized, visible label.
- **No RTL-specific concern** — nothing in this layout is directionally asymmetric beyond what
  GTK4/Adw already mirror automatically (combo row chevrons, expander row triangles).

## 5. Streaming models (WHISP-56/57 forward compatibility)

No catalog entry has `streaming=True` today — WHISP-56 ("Protocolo StreamingSpeechEngine +
adaptador sherpa-onnx OnlineRecognizer") registers the engine type and, per its own description,
"catálogo (streaming=True)" — i.e. WHISP-56 itself adds a streaming catalog entry (likely the
Nemotron 3.5 Streaming 0.6B model named in the research report, `license="OpenMDW-1.1"`).

This ticket's picker needs **zero code changes** when that lands, by construction:
- The "Streaming" badge (§1.2, §2.1 `badges_for`) already keys off `entry.streaming` — it starts
  showing the moment a `streaming=True` entry exists in the catalog.
- The license notice (§1.5) already keys off `is_nonstandard_license(entry.license)` —
  `"OpenMDW-1.1"` isn't in `_STANDARD_LICENSES`, so it triggers automatically.
- Language filtering (§1.2) and the full-catalog view (§1.6) are catalog-driven, not
  engine-hardcoded — a new streaming entry just appears as another row.
- **What this ticket does *not* pre-build:** any UI distinguishing "this model can also do live
  partial transcripts" beyond the static badge — e.g. no toggle for "use streaming mode," no
  wiring to the `PartialTranscript` D-Bus signal WHISP-57 adds. Selecting a streaming-capable
  model through this picker only changes which engine loads for *batch* transcription (today's
  only mode); turning on live partials is WHISP-57's daemon/session work plus (if needed) a
  separate main-window UI change, not a Settings-dialog concern. This is the right boundary: the
  model picker answers "which model," not "which recording mode."

## 6. Product decision: `catalog.recommended(language='en')` returns Parakeet

**The WHISP-50 audit note (Lattice, 2026-10-01):**
> "catalog.recommended(language='en') devuelve Parakeet (entrada con idioma explícito gana a
> Whisper multilingüe). Revisar como decisión de producto al diseñar el selector."
> ("…returns Parakeet (an entry with an explicit language beats multilingual Whisper). Review as
> a product decision when designing the selector.")

**Decision: keep it.** Ship `catalog.recommended(language='en')` → Parakeet TDT 0.6B v3 as the
preselected English default in the new Model row, unchanged from what `catalog.py` already does.
No code change to `catalog.recommended()`'s tie-breaking rule.

**Justification:**
1. **The rule is correct and general, not an English-specific quirk.** `recommended()`'s rule —
   "an entry with an explicit `languages` match beats a `languages=None` multilingual entry" — is
   what makes Spanish (the primary target locale, per the research report and this repo's
   Spanish-language internal docs) correctly resolve to Parakeet. English is one of Parakeet's 25
   declared `_PARAKEET_V3_LANGUAGES`, so the *same* general rule picks it for English too. Adding
   an English-specific carve-out would make the algorithm inconsistent (24 languages follow the
   rule, 1 doesn't) for a single-language exception, which is a worse long-term design than the
   uniform rule, and `catalog.py` is explicitly out of scope for this ticket to modify (it's
   WHISP-50's, already shipped and tested).
2. **Independent data, not just the suspect local benchmark, supports Parakeet for English.**
   The catalog entry's own `notes` field already flags the local bench's English figure (10.5%
   WER) as "suspected to be a bench text-normalization outlier" — that figure is *not* the basis
   for this decision. The research report's separately-sourced open leaderboard figures (not
   WhisperAloud's own harness) show Parakeet v3 at 6.32% average WER with ~3333 RTFx (highest
   throughput among multilingual open models), versus Whisper large-v3-turbo at 7.83% WER and
   ~200 RTFx. On *that* data, Parakeet is both more accurate and far faster for English than the
   Whisper alternative — recommending it isn't a quality regression hiding behind a buggy
   benchmark, it's consistent with the best outside data available right now.
3. **The bug is in the benchmark harness, not the recommendation.** The research report's own P0
   list includes a separate, already-identified fix: "Corregir la normalización EN" in
   `bench/MATRIX.md` (fix English text normalization, add large-v3-turbo to the matrix). The
   correct remedy for a suspect number is to fix the number, not to special-case product code
   around it. If the corrected local benchmark comes back worse for Parakeet-English than
   Whisper, *that's* the trigger to revisit this decision — not a precondition for shipping
   WHISP-42 now.
4. **The UI never traps a user in the recommendation.** Every English user who prefers the
   already-cached `faster-whisper base` default (today's `ModelConfig.name` default) sees exactly
   that: the Model row shows the current config's selection as preselected *when it's already
   set* (`default_index` only falls back to `recommended()` when the current selection isn't in
   the filtered list at all — i.e. for a fresh install or a language change, not for an existing
   English user who already has `base` configured and saved). Parakeet only becomes the
   *preselected* choice for a configuration that doesn't already name a valid model for the
   current language — exactly the "what should a new/reset user get" question `recommended()`
   exists to answer. A user who wants to compare can reach every model via the combo row
   (same-language entries) or the full-catalog view (§1.6, cross-language).

**Follow-up (not this ticket):** once the P0 bench fix lands (separate ticket, already tracked in
the research report, not WHISP-42), if Parakeet's corrected English WER is meaningfully worse
than Whisper's, consider adding a per-(entry, language) confidence/override field to `ModelEntry`
so `recommended()` could exclude a specific language from an otherwise-recommended entry without
an ad hoc code branch. Speculative — not built now (YAGNI), noted so a future spec doesn't have to
rediscover the option.

## 7. Risks / open questions

- **Blocking reload on download (§3)** is the main UX risk this ticket's wider model
  discoverability creates (more models are now reachable than before, all sharing the same
  synchronous-download-on-reload behavior). Mitigated by the §1.4 notice setting expectations;
  not fixed — WHISP-58 fixes it.
- **faster-whisper download-state detection is a heuristic (§2.3), not authoritative.** A
  nonstandard `HF_HOME`/`HUGGINGFACE_HUB_CACHE`/`model.download_root` can produce a false
  "not downloaded" notice. Low severity (worst case: an unnecessary notice for a model that's
  actually cached) but should be called out in code comments, not silently assumed correct.
- **No per-device ("hardware") recommendation in the catalog (§1.2).** The ticket brief asks for
  "the recommended model for the configured language and hardware," but `catalog.recommended()`
  only takes `engine`/`language`. This spec treats "hardware-aware" as "don't recommend a model
  that excludes the configured device" (a filter) rather than "pick a different model *because*
  of the device" (true hardware-awareness), since the catalog has no data to do the latter today
  (every entry supports both `cpu`/`cuda`). If a future catalog entry is CPU-only or CUDA-only,
  `catalog.recommended()` itself would need a device parameter — out of scope here.
  **Open question for Fede:** is the filter-only interpretation sufficient for this ticket, or
  should `catalog.recommended()` gain a `device` parameter now? This spec assumes the former
  (smaller change, no WHISP-50-territory edit) unless told otherwise.
  **Open question for Fede:** the Spanish-vs-English UI finding (§0) — ship English-only labels
  (this spec's assumption) or is a Spanish GUI an explicit, separately-scoped goal?
- **The full-catalog view (§1.6)** is new UI surface with no precedent in this codebase (only the
  CLI `models` table exists today). Its exact widget (`Gtk.ColumnView` vs. a `Gtk.ListBox` of
  `AdwActionRow`) is left to the implementer's judgment in the plan — a `ColumnView` is more
  "correct" for tabular data but is more GTK4 ceremony (needs a `Gio.ListStore` +
  `Gtk.ColumnViewColumn` factories per field) than a scrollable `AdwActionRow` list, which is
  simpler and matches this dialog's existing low-ceremony style. The companion plan picks the
  `AdwActionRow` list for that reason.

## 8. Test list

All of the following are plain pytest, no GTK runtime required (consistent with the existing
"dialog is thin glue over tested helpers" pattern) — `tests/test_model_picker_logic.py` (new,
flat under `tests/` like `test_settings_dialog_logic.py`/`test_history_panel_logic.py` — this
repo has no `tests/ui/` subpackage for logic tests, only `tests/stt/` for engine-level tests) and
additions to `tests/test_settings_dialog_logic.py` only where `settings_logic.py` itself changes
(it mostly doesn't — see §2.2).

**`tests/test_model_picker_logic.py` (new file):**
- `selectable_entries("es", "cpu")` includes the Parakeet entry and all `faster-whisper` entries
  (both support `"cpu"`); excludes nothing today (every entry supports `cpu`) — add a synthetic
  CPU-only/CUDA-only case via a monkeypatched/fake entry list if the real catalog has no such
  entry yet, so the device filter itself is actually exercised.
- `selectable_entries("en", "cpu")` includes Parakeet (declares `"en"`) and all `faster-whisper`
  entries (`languages=None`); a non-Parakeet, non-`None`-language synthetic entry is excluded.
- `selectable_entries("de", ...)` excludes nothing Parakeet-related is wrong — Parakeet *does*
  support `"de"` (it's in `_PARAKEET_V3_LANGUAGES`) — assert it's **included**; pick a language
  truly outside the 25-language list (e.g. `"ja"`) to assert Parakeet is excluded while
  `faster-whisper` entries remain.
- `selectable_entries("auto", "cpu")` returns every CPU-capable entry (auto passes language
  filtering through unfiltered, per `catalog.filter_by_language`).
- `default_index` returns the index of the currently configured `(engine, name)` when present in
  `entries`.
- `default_index` falls back to `catalog.recommended(language=...)`'s entry when the configured
  `(engine, name)` is absent from `entries` (e.g. stale config naming a model not valid for the
  current language).
- `default_index` with an empty `entries` list returns `0` without raising (caller-side guard
  documented, not enforced by the function raising).
- `default_index(language="en", ...)` with no current selection resolves to the Parakeet entry —
  this test *is* the regression guard for §6's product decision: if a future change to
  `catalog.py` or this function ever silently changes the English default, this test fails and
  forces a conscious re-review, rather than the behavior drifting unnoticed.
- `badges_for(entry)` returns `["Streaming"]` for a synthetic `streaming=True` entry, `[]` for a
  real current catalog entry (none stream yet), and `["OpenMDW-1.1"]` for a synthetic
  non-standard-license entry.
- `badges_for` returns both badges, in order, for a synthetic entry that is both streaming and
  non-standard-licensed (future Nemotron case).
- `is_nonstandard_license("MIT")`, `("CC-BY-4.0")`, `("Apache-2.0")`, `("BSD-3-Clause")` → all
  `False`; `("OpenMDW-1.1")`, `("GPL-3.0")`, `("")` → all `True`.
- `download_state_for` for a Parakeet entry: `downloaded=True` when
  `model_download.model_dir(name)` contains the expected files (monkeypatch/tmp_path the cache
  dir, same pattern `tests/stt/test_model_download.py` presumably already uses — check it first);
  `downloaded=False` when absent.
- `download_state_for` for a faster-whisper entry: `downloaded=True` when a
  `models--Systran--faster-whisper-<name>` directory with a `model.bin`-containing snapshot
  exists under a monkeypatched HF cache root (`monkeypatch.setenv("HF_HOME", tmp_path)` or
  whichever env var the implementation keys off — pin this down in the plan, don't guess at
  implementation time); `downloaded=False` when absent.
- `download_state_for` resolves aliases before checking (e.g. `catalog.get("faster-whisper",
  "turbo")` resolves to `large-v3-turbo` — the HF-cache directory name should be checked under
  the canonical name, not the alias).
- `needs_download_note(True) is False`; `needs_download_note(False) is True`.
- `save_selection("parakeet", "parakeet-tdt-0.6b-v3")` returns the same tuple unchanged (already
  canonical).
- `save_selection("faster-whisper", "turbo")` returns `("faster-whisper", "large-v3-turbo")`
  (alias resolved).
- `save_selection("nope", "x")` raises `ValueError`.

**Regression / integration:**
- `tests/test_settings_dialog_logic.py` — existing tests pass unchanged (no function in that
  module is modified by this ticket per §2.2).
- `tests/test_config.py` — `MODEL_RELOAD_FIELDS`/`detect_config_changes` tests pass unchanged
  (§3: no change to either).
- `tests/stt/test_catalog.py` — existing tests pass unchanged (`catalog.py` is not modified by
  this ticket).
- **No direct GTK/Adw widget test** for the dialog itself, consistent with this codebase's
  existing convention (`settings_dialog.py` has no widget-level tests today; only
  `*_logic.py` modules are unit tested). A manual smoke-test checklist belongs in the
  implementation plan's final verification step, not in the automated suite.

## 9. Acceptance criteria

- [ ] A boxed-list `AdwComboRow` "Model" sits where the old "Model:" row was, showing
      `"{label} · {size} · {tier}"` plus streaming/license/download badges, filtered by
      `transcription.language` and `model.device`.
- [ ] An `AdwExpanderRow` "Advanced", collapsed by default, holds Engine, Device, and Compute
      type rows (compute type is a net-new control — see §0).
- [ ] Changing Engine in Advanced repopulates the Model row's list and re-syncs its selection;
      changing the Model row's selection to a different engine's entry re-syncs Advanced→Engine.
- [ ] Selecting a not-yet-downloaded model shows the sized inline notice; it disappears once a
      downloaded model is selected.
- [ ] Selecting a non-standard-license model shows the license notice (untestable against real
      data today — no such catalog entry exists yet; covered by the `badges_for`/
      `is_nonstandard_license` unit tests against synthetic entries).
- [ ] A "View full catalog…" affordance opens a read-only list of every `catalog.entries()` row,
      unfiltered by language/device, and selecting one updates the Model/Advanced rows to match.
- [ ] Saving writes `config.model.engine`, `.name`, `.device`, `.compute_type` — the first three
      preserve current save semantics, `compute_type` is newly wired.
- [ ] `model_picker_logic.py` is fully unit-tested per §8, with zero GTK/Adw imports.
- [ ] The June 2026-06-09 spec is marked superseded (done, this ticket's first commit).
- [ ] `python -m pytest -q` stays green — this ticket is design-only, so in practice this means
      "the two Open Questions in §7 are resolved, and §8's planned tests pass," verified by the
      implementation ticket, not WHISP-42 itself.
