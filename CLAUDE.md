# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhisperAloud is an offline voice transcription app for Linux/GNOME using OpenAI's Whisper (via faster-whisper). It provides a GTK4 GUI, CLI tool, and D-Bus daemon for recording audio and transcribing it locally.

## Common Commands

```bash
# Virtual environment (MUST use --system-site-packages for GTK4 bindings)
source ~/.venvs/whisper_aloud/bin/activate

# Install in development mode
pip install -e .
pip install -e ".[dev]"    # includes pytest, black, ruff, mypy

# Run the GUI
whisper-aloud-gui

# CLI usage
whisper-aloud audio.wav                        # transcribe a file
whisper-aloud --daemon                         # start D-Bus daemon
whisper-aloud start|stop|status|toggle|quit    # control daemon

# Tests
pytest                                         # all tests with coverage
pytest tests/test_config.py                    # single file
pytest tests/test_config.py::TestClass::test_method  # single test
pytest -x                                      # stop on first failure

# Linting & formatting
black --check src/                             # check formatting (line-length=100)
ruff check src/                                # lint
mypy src/                                      # type check

# System validation
python scripts/validate_system.py
python scripts/check_dependencies.py --verbose
```

## Architecture

### Layers (top to bottom)

1. **UI** (`src/whisper_aloud/ui/`) — GTK4 interface. `main_window.py` (1144 LOC) is the main orchestrator integrating all subsystems. Entry point: `whisper-aloud-gui` → `ui/__init__.py:main()`.

2. **CLI / D-Bus** (`__main__.py`, `service/daemon.py`) — CLI dispatches to file transcription, daemon control commands, or starts the D-Bus service (`org.fede.whisperAloud`). Entry point: `whisper-aloud` → `__main__:main()`.

3. **Core** — `transcriber.py` (Whisper model wrapper with lazy loading, CUDA fallback), `audio/recorder.py` (state-machine recorder with real-time level callbacks), `clipboard/clipboard_manager.py` (Wayland/X11 auto-detection).

4. **Persistence** (`persistence/`) — SQLite with FTS5 full-text search (`database.py`), history management (`history_manager.py`), audio archiving with deduplication (`audio_archive.py`).

5. **Config** (`config.py`) — Hierarchical dataclasses. Priority: defaults < `~/.config/whisper_aloud/config.json` < env vars (`WHISPER_ALOUD_*`). Supports hot-reload and change detection.

### Key paths

- Config file: `~/.config/whisper_aloud/config.json`
- Data/DB: `~/.local/share/whisper_aloud/`
- Model cache: `~/.cache/huggingface/`
- D-Bus name: `org.fede.whisperAloud`
- Desktop entry: `com.whisperaloud.App`

### Important patterns

- **GTK4 bindings come from system packages** (`python3-gi`, `gir1.2-gtk-4.0`), not pip. The venv must use `--system-site-packages`.
- **Transcriber uses lazy model loading** — first call downloads the model (can take minutes).
- **AudioRecorder is a state machine**: IDLE → RECORDING → STOPPED/ERROR. Thread-safe with callbacks for level monitoring.
- **Clipboard auto-detects session type** (Wayland vs X11) and uses appropriate tools (wl-copy/xclip, ydotool/xdotool).
- **Config uses env var overrides** prefixed with `WHISPER_ALOUD_` (e.g., `WHISPER_ALOUD_MODEL_NAME=medium`).

## System Dependencies

Required: `portaudio19-dev`, `libportaudio2`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `gir1.2-gsound-1.0`
Optional: `wl-clipboard`, `ydotool` (Wayland), `xclip`, `xdotool` (X11), CUDA libs (GPU)

## Code Style

- Line length: 100 (black + ruff)
- Target Python: >=3.10
- Type hints used throughout; `mypy` with `disallow_untyped_defs`
- Ruff selects: E, F, I, N, W (ignores E501)
