# WHISP-42 — Settings UI Engine Selector

- **Lattice:** WHISP-42 (subtask of WHISP-32; related to WHISP-36)
- **Date:** 2026-06-09
- **Status:** approved design, pre-implementation
- **Builds on:** WHISP-36 (Parakeet is a selectable production engine; `config.model.engine`
  + engine-aware `model.name` validation via `config.ENGINE_MODELS`; daemon reloads on engine change).

## 1. Purpose

Expose the engine choice in the GTK Settings dialog so a GUI-only user can switch between
faster-whisper and Parakeet without editing config or using the CLI. Today the dialog has a
"Model:" dropdown but no "Engine:" selector, and the model list is hardcoded — this adds the
engine selector and makes the model list engine-aware, sourced from `config.ENGINE_MODELS`.

### Scope (decided)
Engine selector + engine-aware model dropdown + an inline "model downloads on first use" note
for Parakeet, wired through the existing save → daemon-reload path. Pure index↔value logic lives
in `settings_logic.py` (unit-tested); the GTK dialog is thin glue.

### Non-goals (YAGNI)
- No in-UI download flow / progress bar (the daemon lazy-downloads; an inline note sets the
  expectation). No "Download now" button.
- No per-language auto-engine switching (a single manually-selected engine, per WHISP-36).
- No compute-device (CPU/CUDA) gating per engine — the Parakeet engine already does CUDA→CPU
  fallback, so the existing device dropdown is left unchanged.
- No model deletion/management UI.

## 2. Pure logic helpers (`src/whisper_aloud/ui/settings_logic.py`)

The index↔value mapping is the testable core; it goes in `settings_logic.py` (which already
holds pure, unit-tested dialog helpers) — NOT in the GTK dialog.

```python
from ..config import ENGINE_MODELS

ENGINE_LABELS: dict[str, str] = {
    "faster-whisper": "faster-whisper",
    "parakeet": "Parakeet (NVIDIA)",
}


def engine_options() -> list[str]:
    """Ordered engine keys for the engine dropdown (single source of truth)."""
    return list(ENGINE_MODELS.keys())


def engine_display_label(engine: str) -> str:
    """Friendly label for an engine key, falling back to the key itself."""
    return ENGINE_LABELS.get(engine, engine)


def models_for_engine(engine: str) -> list[str]:
    """Valid model names for an engine (empty list for an unknown engine)."""
    return list(ENGINE_MODELS.get(engine, []))


def resolve_model_selection(engine: str, current_model: str) -> tuple[list[str], int]:
    """Model list for `engine` plus the index to preselect.

    Preselects `current_model` when it's valid for the engine; otherwise index 0
    (e.g. switching faster-whisper 'base' -> parakeet resets to the parakeet model).
    Returns ([], 0) for an unknown engine.
    """
    models = models_for_engine(engine)
    if not models:
        return [], 0
    try:
        return models, models.index(current_model)
    except ValueError:
        return models, 0


def needs_download_note(engine: str, model_present: bool) -> bool:
    """Whether to show the 'downloads on first use' note (filesystem check stays in the dialog)."""
    return engine == "parakeet" and not model_present
```

Notes:
- `engine_options()` / `models_for_engine()` derive from `config.ENGINE_MODELS`, so the dialog
  can never offer a model that `config.validate()` would reject, and both auto-update as engines
  are added.
- **Display-label tradeoff:** the model dropdown will show raw model names from `ENGINE_MODELS`
  (e.g. `tiny`, `base`, …, `large-v3-turbo`, `turbo`; `parakeet-tdt-0.6b-v3`). The current
  curated annotations (`tiny (fastest)` / `large (most accurate)`) are dropped — a deliberate
  simplification to keep `ENGINE_MODELS` the single source. Engines get friendly labels via the
  small `ENGINE_LABELS` map (labels only — no validation impact).

## 3. Settings dialog (`src/whisper_aloud/ui/settings_dialog.py`)

In the existing "Transcription" section, **above** the "Model:" row:

- **Engine row** — a `Gtk.DropDown` built from `engine_options()` rendered through
  `engine_display_label(...)`. Store the ordered key list (`self._engine_keys = engine_options()`)
  so the selected index maps back to an engine key. Preselect `self._config.model.engine`.
- **Model dropdown** — built from `models_for_engine(self._config.model.engine)` (replacing the
  hardcoded `["tiny","base","small","medium","large"]` lists at lines 233 and 1023 — a
  duplication this change removes). Preselect via `resolve_model_selection(engine, config.model.name)`.
  Repopulation uses `self.model_dropdown.set_model(Gtk.StringList.new(models))` then
  `set_selected(index)`.
- **Engine-change handler** — connect the engine dropdown's `notify::selected`. On change:
  1. `engine = self._engine_keys[dropdown.get_selected()]`
  2. `models, idx = resolve_model_selection(engine, <current model value>)`
  3. `self.model_dropdown.set_model(Gtk.StringList.new(models)); self.model_dropdown.set_selected(idx)`
  4. update the download note (below).
- **Inline download note** — a `Gtk.Label` (dimmed, wrapping) under the model row, text
  *"Parakeet model (~671 MB) downloads on first use."* Visibility set via
  `needs_download_note(engine, model_present)`, where `model_present` is computed in the dialog
  from `model_download.model_dir(model_name)` + the same presence check the downloader uses.
  Recomputed on engine change and on model change. (Import `model_download` lazily inside the
  helper method to avoid pulling it into dialog construction cost.)
- **Save** (`_on_save_clicked`) — set both fields from the dropdowns:
  - `engine = self._engine_keys[self.engine_dropdown.get_selected()]`
  - `model = models_for_engine(engine)[self.model_dropdown.get_selected()]`
  - `self._config.model.engine = engine; self._config.model.name = model`
  The existing language/device/audio save logic is unchanged.
- **Unsaved-change tracking** — add `"engine": self.engine_dropdown.get_selected()` to the
  `_capture_ui_state()` dict (line ~529) alongside the existing `"model"` index. (Switching
  engine repopulates the model dropdown and changes indices — correctly registering as an unsaved
  change.)

## 4. Data flow / reload (unchanged plumbing)
Save → existing `on_save_callback` → daemon `ReloadConfig`. WHISP-36's reload-condition fix
(`detect_config_changes(...).requires_model_reload`) means an `engine` change now reloads the
model, so switching engine in Settings takes effect on the next transcription (lazy-downloading
the Parakeet model if absent). No new wiring needed here.

## 5. Error handling
- The dropdowns only offer valid `(engine, model)` combinations (sourced from `ENGINE_MODELS`),
  so `config.validate()` on save cannot reject the model for the engine. Language validation is
  unchanged.
- If `config.model.engine`/`name` somehow hold a value absent from the current lists at dialog
  open (e.g. hand-edited config), `resolve_model_selection` falls back to index 0 and the engine
  dropdown falls back to index 0 if the key is unknown — the dialog never crashes on stale config.

## 6. Testing
- **`settings_logic` unit tests** (`tests/test_settings_dialog_logic.py`):
  - `engine_options()` returns `["faster-whisper", "parakeet"]` (order from ENGINE_MODELS).
  - `engine_display_label("parakeet") == "Parakeet (NVIDIA)"`; unknown key falls back to itself.
  - `models_for_engine("parakeet") == ["parakeet-tdt-0.6b-v3"]`; unknown engine → `[]`.
  - `resolve_model_selection("faster-whisper", "small")` → that list, index of "small".
  - `resolve_model_selection("parakeet", "base")` → parakeet list, index `0` (cross-engine reset).
  - `resolve_model_selection("nope", "x")` → `([], 0)`.
  - `needs_download_note("parakeet", False) is True`; `("parakeet", True) is False`;
    `("faster-whisper", False) is False`.
- **No direct GTK unit test** for the dialog (consistent with the existing logic-in-`settings_logic`
  approach; the dialog is thin glue over the tested helpers).
- **Regression:** existing `tests/test_settings_dialog_logic.py` tests pass unchanged.

## 7. Acceptance criteria
- [ ] Settings shows an "Engine:" dropdown (faster-whisper / "Parakeet (NVIDIA)") above "Model:".
- [ ] Selecting an engine repopulates the model dropdown from `ENGINE_MODELS[engine]`, preserving
      the current model if still valid, else resetting to the first.
- [ ] Selecting Parakeet shows the "~671 MB downloads on first use" note when the model is absent;
      the note hides when the model is present or the engine is faster-whisper.
- [ ] Saving writes `config.model.engine` and `config.model.name`; the daemon reload switches the
      live engine (via the WHISP-36 fix).
- [ ] The hardcoded model lists at lines 233 & 1023 are removed in favor of `ENGINE_MODELS`-sourced
      helpers.
- [ ] New `settings_logic` helpers are unit-tested; all pre-existing settings-logic tests pass.

## 8. Risks / follow-ups
- **Annotation drop** (`tiny (fastest)` etc.) is a minor UX regression accepted for DRY; a
  model-label map in `settings_logic` is a cheap follow-up if desired.
- **CUDA + Parakeet** isn't gated in the UI; relies on the engine's CUDA→CPU fallback. A future
  enhancement could disable/annotate CUDA when Parakeet lacks a GPU build, but it's not required.
- **In-UI download flow with progress** remains the natural next follow-up after this.
