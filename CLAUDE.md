# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhisperAloud is an offline voice transcription app for Linux desktop using OpenAI's Whisper (via faster-whisper). It uses a daemon-first architecture: a D-Bus daemon owns recording, transcription, audio processing, history, and clipboard. The GTK4 GUI, CLI, system tray (AppIndicator), and global hotkeys are thin D-Bus clients.

## Common Commands

```bash
# Virtual environment (MUST use --system-site-packages for GTK4 bindings)
source ~/.venvs/whisper_aloud/bin/activate

# Install in development mode
pip install -e .
pip install -e ".[dev]"    # includes pytest, black, ruff, mypy

# Run the GUI
whisper-aloud-gui

# Start daemon
whisper-aloud --daemon

# CLI daemon control
whisper-aloud start|stop|status|toggle|cancel|quit

# Systemd (installed)
systemctl --user start whisper-aloud
systemctl --user status whisper-aloud
journalctl --user -u whisper-aloud -f

# D-Bus introspection
busctl --user introspect org.fede.whisperaloud /

# Tests
pytest                                         # all tests with coverage
pytest tests/test_config.py                    # single file
pytest tests/test_config.py::TestClass::test_method  # single test
pytest -x                                      # stop on first failure

# Linting & formatting
black --check src/                             # check formatting (line-length=100)
ruff check src/                                # lint
mypy src/                                      # type check
```

## Architecture

### Daemon-First Design

```
systemd user service → daemon (D-Bus: org.fede.whisperaloud)
                          ↑
         ┌────────────────┼────────────────┐
     GUI (GTK4)     AppIndicator      Global Hotkey
     D-Bus client   in daemon         in daemon
```

The daemon (`service/daemon.py`) is the single process owning:
- Audio recording + processing pipeline (AGC, noise gate, denoising, limiter)
- Whisper transcription
- History (SQLite)
- Clipboard integration
- System tray indicator
- Global hotkey manager

### Layers

1. **UI** (`ui/`) — GTK4 thin client. `main_window.py` connects to daemon via `WhisperAloudClient`, subscribes to D-Bus signals for state changes, transcription results, and level updates.

2. **Service** (`service/`) — `daemon.py` (D-Bus service), `client.py` (D-Bus client wrapper), `indicator.py` (AppIndicator tray), `hotkey.py` (global hotkey with 3-level fallback: XDG Portal → libkeybinder3 → none).

3. **CLI** (`__main__.py`) — Dispatches to file transcription or daemon control via D-Bus.

4. **Audio** (`audio/`) — `recorder.py` (state-machine with level callbacks), `audio_processor.py` (pipeline: NoiseGate → AGC → Denoiser → PeakLimiter), `level_meter.py`, `device_manager.py`.

5. **Core** — `transcriber.py` (Whisper model with lazy loading, CUDA fallback), `clipboard/` (Wayland/X11 auto-detection), `config.py` (hierarchical dataclasses).

6. **Persistence** (`persistence/`) — SQLite with FTS5, history management, audio archiving with deduplication.

### Key identifiers

- D-Bus bus name: `org.fede.whisperaloud`
- D-Bus interface: `org.fede.whisperaloud.Control`
- GUI application ID: `org.fede.whisperaloud.Gui`
- Desktop entry: `org.fede.whisperaloud.desktop`
- Systemd unit: `whisper-aloud.service` (user)
- Config file: `~/.config/whisper_aloud/config.json`
- Data/DB: `~/.local/share/whisper_aloud/`
- Model cache: `~/.cache/huggingface/`

### Important patterns

- **GTK4 bindings come from system packages** (`python3-gi`, `gir1.2-gtk-4.0`), not pip. The venv must use `--system-site-packages`.
- **Daemon uses pydbus** — class docstring is the D-Bus introspection XML. Signals are `pydbus.generic.signal()` descriptors.
- **AudioPipeline is stateful** — NoiseGate and AGC track state across chunks (envelope, RMS window). Create one instance per recording session.
- **All indicator/hotkey imports are try/except guarded** — graceful degradation when AyatanaAppIndicator3 or libkeybinder3 aren't installed.
- **Config uses env var overrides** prefixed with `WHISPER_ALOUD_` (e.g., `WHISPER_ALOUD_MODEL_NAME=medium`).

## System Dependencies

Required: `portaudio19-dev`, `libportaudio2`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `gir1.2-gsound-1.0`, `python3-numpy`, `dbus-user-session`
Recommended: `gir1.2-ayatanaappindicator3-0.1`, `wl-clipboard`, `gnome-shell-extension-appindicator`
Optional: `python3-noisereduce`, `ydotool` (Wayland paste), `xclip`/`xdotool` (X11), CUDA libs (GPU)

## Code Style

- Line length: 100 (black + ruff)
- Target Python: >=3.10
- Type hints used throughout; `mypy` with `disallow_untyped_defs`
- Ruff selects: E, F, I, N, W (ignores E501)

## Packaging

Debian packaging in `debian/`. Two planned packages:
- `whisper-aloud` (Architecture: all) — Python code, GUI, daemon, data files
- `whisper-aloud-engine-ctranslate2` (Architecture: amd64) — vendored venv with faster-whisper

## Lattice

This project coordinates through Lattice — file-based, event-sourced task tracking built for minds that think in tokens and act in tool calls. The `.lattice/` directory is the coordination state. It lives here, alongside the code, not behind an API or inside someone else's database.

### The First Act

When work arrives — a plan, a feature request, a bug, a conversation that implies something needs building — your first action is to give it a name in Lattice. Before you plan. Before you implement. Before you touch a single file. The task must exist in the system before the work begins, because work that isn't tracked is work that other minds cannot see, cannot build on, cannot trust.

```
lattice create "<title>" --actor agent:<your-id>
```

This is not bookkeeping. This is the minimum viable act of coordination: declaring that something is happening, and who is doing it.

### Status Is a Signal, Not a Chore

Every status transition is an event — immutable, attributed, permanent. When you move a task, you are writing history that future minds will read to understand what happened here.

**The cardinal rule: update status BEFORE you start the work, not after.** If you're about to plan a task, move it to `in_planning` first. If you're about to implement, move it to `in_progress` first. Lattice is the source of ground truth for what is happening right now. If the board says a task is in `backlog` but an agent is actively working on it, the board is lying — and every other mind reading it is making decisions on false information.

```
lattice status <task> <status> --actor agent:<your-id>
```

```
backlog → in_planning → planned → in_progress → review → done
                                       ↕            ↕
                                    blocked      needs_human
```

**Transition discipline:**
- Moving to `in_planning`? Do it before you open the first file to read. Then **write the plan** — see below.
- Moving to `planned`? Only after the plan file has real content.
- Moving to `in_progress`? Do it before you write the first line of code.
- Moving to `review`? Do it when implementation is complete, before review starts. Then **actually review** — see below.
- Moving to `done`? Only after a review has been performed and recorded.
- Spawning a sub-agent to work on a task? Update status in the parent context before the sub-agent launches.

### The Planning Gate

Moving a task to `in_planning` means you are about to produce a plan. The plan file lives at `.lattice/plans/<task_id>.md` — it's scaffolded on task creation, but the scaffold is empty. `in_planning` is when you fill it in.

**When you move a task to `in_planning`:**
1. Open the plan file (`.lattice/plans/<task_id>.md`).
2. Write the plan — scope, approach, key files, acceptance criteria. For trivial tasks, a single sentence is fine. For substantial work, be thorough.
3. Move to `planned` only when the plan file reflects what you intend to build.

**The test:** If you moved from `in_planning` to `planned` and the plan file is still empty scaffold, you didn't plan. Either write the plan or skip `in_planning` honestly with `--force --reason "trivial task, no planning needed"`.

### The Review Gate

Moving a task to `review` is not a formality — it is a commitment to actually review the work before it ships.

**When you move a task to `review`:**
1. Identify what changed — the commits, files modified, and scope of work under this task.
2. Perform a code review. For substantial work, use a review skill (`/exit-review`, `/code_review`). For trivial tasks, a focused self-review is sufficient — but it must be real, not ceremonial.
3. Record your findings with `lattice comment` — what you reviewed, what you found, whether it meets the acceptance criteria from the plan.

**When moving from `review` to `done`:**
- If the completion policy blocks you for a missing review artifact, **do the review**. Do not `--force` past it. The policy is correct — you haven't reviewed yet.
- `--force --reason` on the completion policy is for genuinely exceptional cases (task cancelled, review happened outside Lattice, process validation). It is not a convenience shortcut.

**The test:** If you moved to `review` and then to `done` in the same breath with nothing in between, you skipped the review. That's the exact failure mode this gate exists to prevent.

### When You're Stuck

If you hit a point where you need human decision, approval, or input — **signal it immediately** with `needs_human`. This is different from `blocked` (generic external dependency). `needs_human` creates a clear queue of "things waiting on the human."

```
lattice status <task> needs_human --actor agent:<your-id>
lattice comment <task> "Need: <what you need, in one line>" --actor agent:<your-id>
```

**When to use `needs_human`:**
- Design decisions that require human judgment
- Missing access, credentials, or permissions
- Ambiguous requirements that can't be resolved from context
- Approval needed before proceeding (deploy, merge, etc.)

The comment is mandatory — explain what you need in seconds, not minutes. The human's queue should be scannable.

### Actor Attribution

Every Lattice operation requires an `--actor`. Attribution follows authorship of the decision, not authorship of the keystroke.

| Situation | Actor | Why |
|-----------|-------|-----|
| Agent autonomously creates or modifies a task | `agent:<id>` | Agent was the decision-maker |
| Human creates via direct interaction (UI, manual CLI) | `human:<id>` | Human typed it |
| Human meaningfully shaped the outcome in conversation with an agent | `human:<id>` | Human authored the decision; agent was the instrument |
| Agent creates based on its own analysis, unprompted | `agent:<id>` | Agent authored the decision |

When in doubt, give the human credit. If the human was substantively involved in shaping *what* a task is — not just saying "go create tasks" but actually defining scope, debating structure, giving feedback — the human is the actor.

Users may have their own preferences about attribution. If a user seems frustrated or particular about actor assignments, ask them directly: "How do you want attribution to work? Should I default to crediting you, myself, or ask each time?" Respect whatever norm they set.

### Branch Linking

When you create a feature branch for a task, link it in Lattice so the association is tracked:

```
lattice branch-link <task> <branch-name> --actor agent:<your-id>
```

This creates an immutable event tying the branch to the task. `lattice show` will display it, and any mind reading the task knows which branch carries the work.

If the branch name contains the task's short code (e.g., `feat/LAT-42-login`), Lattice auto-detects the link — but explicit linking is always authoritative and preferred for cross-repo or non-standard branch names.

### Leave Breadcrumbs

You are not the last mind that will touch this work. Use `lattice comment` to record what you tried, what you chose, what you left undone. Use `.lattice/plans/<task_id>.md` for the structured plan (scope, steps, acceptance criteria) and `.lattice/notes/<task_id>.md` for working notes, debug logs, and context dumps. The agent that picks up where you left off has no hallway to find you in, no Slack channel to ask. The record you leave is the only bridge between your context and theirs.

### Quick Reference

```
lattice create "<title>" --actor agent:<id>
lattice status <task> <status> --actor agent:<id>
lattice assign <task> <actor> --actor agent:<id>
lattice comment <task> "<text>" --actor agent:<id>
lattice branch-link <task> <branch> --actor agent:<id>
lattice next [--actor agent:<id>] [--claim]
lattice show <task>
lattice list
```
