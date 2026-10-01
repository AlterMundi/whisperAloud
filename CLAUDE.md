# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WhisperAloud is an offline voice transcription app for Linux desktop using OpenAI's Whisper (via faster-whisper). Daemon-first architecture: a D-Bus daemon (`service/daemon.py`) is the single process owning recording, the audio processing pipeline (NoiseGate → AGC → Denoiser → PeakLimiter), Whisper transcription, history (SQLite + FTS5), clipboard, system tray, and global hotkeys. The GTK4 GUI (`ui/`), CLI (`__main__.py`), tray, and hotkeys are thin D-Bus clients via `service/client.py`.

## Key Identifiers

- D-Bus bus name: `org.fede.whisperaloud`; interface: `org.fede.whisperaloud.Control`
- GUI application ID: `org.fede.whisperaloud.Gui`; desktop entry: `org.fede.whisperaloud.desktop`
- Systemd unit: `whisper-aloud.service` (user scope)
- Config: `~/.config/whisper_aloud/config.json`; data/DB: `~/.local/share/whisper_aloud/`; model cache: `~/.cache/huggingface/`

## Gotchas & Important Patterns

- **The venv MUST use `--system-site-packages`** — GTK4 bindings come from system packages (`python3-gi`, `gir1.2-gtk-4.0`), not pip. Dev venv: `source ~/.venvs/whisper_aloud/bin/activate`.
- **Daemon uses pydbus** — the service class docstring IS the D-Bus introspection XML. Signals are `pydbus.generic.signal()` descriptors.
- **AudioPipeline is stateful** — NoiseGate and AGC track state across chunks (envelope, RMS window). Create one instance per recording session.
- **Optional deps degrade gracefully** — all indicator/hotkey imports are try/except guarded (AyatanaAppIndicator3, libkeybinder3 may be absent). Global hotkey has a 3-level fallback: XDG Portal → libkeybinder3 → none.
- **Config supports env var overrides** prefixed `WHISPER_ALOUD_` (e.g., `WHISPER_ALOUD_MODEL_NAME=medium`).

## System Dependencies

Required: `portaudio19-dev`, `libportaudio2`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `gir1.2-gsound-1.0`, `python3-numpy`, `dbus-user-session`
Recommended: `gir1.2-ayatanaappindicator3-0.1`, `wl-clipboard`, `gnome-shell-extension-appindicator`
Optional: `python3-noisereduce`, `ydotool` (Wayland paste), `xclip`/`xdotool` (X11), CUDA libs (GPU)

## Packaging

Debian packaging lives in `debian/` (two packages: `whisper-aloud` arch-all Python code, and `whisper-aloud-engine-ctranslate2` amd64 vendored venv with faster-whisper).

## Task Tracking

This project uses Lattice for task tracking (`.lattice/` initialized). See the `/lattice` skill for full workflow conventions.
