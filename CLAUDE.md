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
