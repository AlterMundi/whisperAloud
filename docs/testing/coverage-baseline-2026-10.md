# Coverage Baseline — October 2026 (WHISP-45)

- **Date:** 2026-10-01
- **Measured on:** master `a332713` plus the coverage-config commit, before any WHISP-45 tests.
  The branch was later rebased onto a newer master (WHISP-65 added ~70 tests), so re-running at the
  rebased commit gives different totals; these figures describe the pre-WHISP-65 tree.
- **Command:** `pytest -q --cov=whisper_aloud --cov-branch --cov-report=term-missing`
- **Suite result:** 568 passed, 10 skipped, 8 xfailed
- **Overall coverage:** 48% statements (6587 stmts / 3430 missed), branch coverage tracked (1422 branches, 214 partial)

This is a point-in-time baseline, not a gate — `pyproject.toml` has no `fail_under`. It exists so future
regressions in critical, non-GTK logic are visible, and to identify the weakest modules for the
WHISP-45 regression tests added alongside it.

## Per-module coverage

| Module | Stmts | Miss | Branch | BrPart | Cover |
|---|---:|---:|---:|---:|---:|
| `__init__.py` | 19 | 5 | 4 | 1 | 65% |
| `__main__.py` | 366 | 126 | 88 | 16 | 63% |
| `audio/__init__.py` | 11 | 1 | 2 | 1 | 85% |
| `audio/audio_processor.py` | 235 | 8 | 68 | 8 | 95% |
| `audio/device_manager.py` | 65 | 3 | 14 | 2 | 94% |
| `audio/level_meter.py` | 34 | 1 | 6 | 1 | 95% |
| `audio/recorder.py` | 156 | 23 | 34 | 10 | 83% |
| `clipboard/__init__.py` | 4 | 0 | 0 | 0 | 100% |
| `clipboard/clipboard_manager.py` | 104 | 30 | 16 | 2 | 73% |
| `clipboard/paste_simulator.py` | 108 | 44 | 24 | 2 | 58% |
| `clipboard/window_manager.py` | 121 | 53 | 32 | 5 | 54% |
| `config.py` | 396 | 70 | 178 | 59 | 76% |
| `exceptions.py` | 22 | 0 | 0 | 0 | 100% |
| `gnome_integration.py` | 65 | 47 | 18 | 0 | 22% |
| `persistence/__init__.py` | 13 | 1 | 2 | 1 | 87% |
| `persistence/audio_archive.py` | 86 | 14 | 16 | 1 | 85% |
| `persistence/database.py` | 190 | 36 | 22 | 4 | 78% |
| `persistence/history_manager.py` | 176 | 36 | 54 | 12 | 75% |
| `persistence/models.py` | 27 | 0 | 0 | 0 | 100% |
| `service/__init__.py` | 11 | 7 | 2 | 0 | 31% |
| `service/client.py` | 224 | 75 | 48 | 3 | 67% |
| `service/daemon.py` | 494 | 148 | 124 | 34 | 68% |
| `service/history_client.py` | 113 | 12 | 28 | 8 | 86% |
| `service/hotkey.py` | 119 | 35 | 20 | 3 | 71% |
| `service/hotkey_portal.py` | 128 | 103 | 30 | 0 | 16% |
| `service/indicator.py` | 94 | 11 | 16 | 3 | 85% |
| `service/media_control.py` | 188 | 41 | 50 | 13 | 77% |
| `stt/base.py` | 28 | 1 | 2 | 1 | 93% |
| `stt/catalog.py` | 58 | 2 | 18 | 1 | 93% |
| `stt/engine.py` | 14 | 0 | 4 | 0 | 100% |
| `stt/faster_whisper_engine.py` | 26 | 5 | 4 | 1 | 80% |
| `stt/model_download.py` | 51 | 10 | 16 | 3 | 75% |
| `stt/parakeet_engine.py` | 40 | 11 | 6 | 2 | 72% |
| `stt/parakeet_speech_engine.py` | 79 | 42 | 12 | 2 | 43% |
| `transcriber.py` | 177 | 54 | 48 | 13 | 67% |
| `ui/__init__.py` | 17 | 11 | 2 | 0 | 32% |
| `ui/app.py` | 42 | 42 | 4 | 0 | 0% |
| `ui/error_handler.py` | 92 | 92 | 22 | 0 | 0% |
| `ui/history_item.py` | 139 | 139 | 20 | 0 | 0% |
| `ui/history_logic.py` | 20 | 1 | 6 | 1 | 92% |
| `ui/history_panel.py` | 293 | 293 | 66 | 0 | 0% |
| `ui/history_panel_logic.py` | 29 | 1 | 14 | 1 | 95% |
| `ui/level_meter.py` | 92 | 75 | 8 | 0 | 17% |
| `ui/level_meter_logic.py` | 15 | 0 | 6 | 0 | 100% |
| `ui/main_window.py` | 687 | 687 | 130 | 0 | 0% |
| `ui/main_window_logic.py` | 29 | 2 | 10 | 0 | 95% |
| `ui/settings_dialog.py` | 769 | 769 | 80 | 0 | 0% |
| `ui/settings_logic.py` | 18 | 0 | 6 | 0 | 100% |
| `ui/shortcuts_window.py` | 92 | 92 | 8 | 0 | 0% |
| `ui/sound_feedback.py` | 63 | 63 | 6 | 0 | 0% |
| `ui/status_bar.py` | 91 | 91 | 14 | 0 | 0% |
| `ui/styles.py` | 17 | 17 | 2 | 0 | 0% |
| `ui/utils.py` | 26 | 0 | 6 | 0 | 100% |
| `utils/validation_helpers.py` | 10 | 0 | 6 | 0 | 100% |
| **TOTAL** | **6587** | **3430** | **1422** | **214** | **48%** |

(`ui/*` widget modules at 0% are GTK4 classes that require a real display and are deliberately not
unit-tested directly — their pure logic is split into `*_logic.py` siblings, most of which are at
92–100%. This split is expected and matches CLAUDE.md guidance; it is not treated as a gap here.)

## Top gaps (non-GTK, worth regression tests)

1. **`service/daemon.py` (68%, 148 missed stmts, 34 partial branches)** — the state machine
   (idle → recording → transcribing → idle), cancel, transcription-error handling, auto-stop at max
   duration, and toggle-while-transcribing paths are largely untested. Largest single risk area in
   the daemon; WHISP-45 adds `tests/test_daemon_state_machine.py` for this (new file, per brief, to
   avoid conflicts with other in-flight work on `tests/test_daemon.py`).
2. **`clipboard/paste_simulator.py` (58%)** and **`clipboard/window_manager.py` (54%)** — paste
   backend selection/fallback logic (ydotool/xdotool/xclip decisions) is thin on coverage.
3. **`config.py` (76%, many partial branches around lines 306–353)** — the `MODEL_RELOAD_FIELDS`
   diff/reload branch matrix is only partially exercised.
4. **`stt/model_download.py` / `stt/faster_whisper_engine.py` / `stt/parakeet_engine.py`
   (`create_engine` factory paths, 72–80%)** — error paths (missing optional deps, bad model name,
   download failure) are undertested.
5. **`persistence/history_manager.py` (75%)** — delete/export paths and some search edge cases are
   missed.
6. **`__main__.py` (63%)** — several CLI argument/error branches (lines 133–167, 199–281, 354–387)
   are not exercised by `tests/test_cli.py`.
7. **`service/hotkey_portal.py` (16%)** and **`gnome_integration.py` (22%)** — thin wrappers around
   D-Bus portals; lowest raw numbers but mostly thin passthrough code, lower regression value than
   the items above.

Items 1–6 above are the ones addressed by the regression tests added in this ticket; `hotkey_portal.py`
is out of scope (overlaps the off-limits startup/bus-acquisition path called out in the brief).
