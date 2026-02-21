# WhisperAloud

WhisperAloud is an offline voice dictation app for Linux desktop.
It is built around a daemon-first architecture: one D-Bus service owns recording, transcription, history, and clipboard actions, while GUI and CLI act as thin clients.

Personal note: this project is designed for real day-to-day writing on Linux, not just demo transcription.

## Why WhisperAloud

- Fully local speech-to-text using `faster-whisper`
- Linux/GNOME-first UX with GTK4 GUI, tray indicator, and hotkeys
- Daemon-first model for stable long-running sessions
- History persistence in SQLite (with optional audio archive)
- Clipboard copy/paste workflow for fast "speak -> paste"

## Core Features

- Offline transcription with Whisper models (`tiny` to `large-v3`)
- Real-time audio level updates while recording
- Voice activity handling and audio processing pipeline
- D-Bus control API (`org.fede.whisperaloud`)
- CLI commands for daemon lifecycle and recording control
- GTK4 client UI with settings and history panel
- Systemd user service support

## Architecture At A Glance

```text
systemd user service
        |
        v
org.fede.whisperaloud (daemon)
  - recorder + processing
  - transcriber
  - clipboard
  - persistence
  - hotkeys + tray
        ^
        |
  +-----+-----------------------+
  |                             |
GTK4 GUI client             CLI client
(whisper-aloud-gui)         (whisper-aloud ...)
```

## Quick Start

### 1. Clone and install

```bash
git clone git@github.com:AlterMundi/whisperAloud.git
cd whisperAloud
./install.sh
```

Optional dev install:

```bash
./install.sh --dev
```

### 2. Launch GUI

```bash
whisper-aloud-gui
```

### 3. Or run daemon + CLI control

```bash
whisper-aloud --daemon

# In another terminal
whisper-aloud start
whisper-aloud stop
whisper-aloud status
whisper-aloud toggle
whisper-aloud cancel
whisper-aloud reload
whisper-aloud quit
```

## Manual Installation (if you prefer explicit setup)

```bash
# Debian/Ubuntu base dependencies
sudo apt update && sudo apt install -y \
  portaudio19-dev libportaudio2 \
  python3-dev python3-venv python3-pip \
  python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gsound-1.0 \
  wl-clipboard xclip

# IMPORTANT: keep system GTK bindings visible in venv
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages
source ~/.venvs/whisper_aloud/bin/activate

pip install -e .
python scripts/check_dependencies.py
```

For distro-specific variants, see `INSTALL.md` and `DEPENDENCIES.md`.

## CLI Usage

### File transcription (legacy single-shot mode)

```bash
whisper-aloud path/to/audio.wav --model base --language es --device auto
```

### Config validation

```bash
whisper-aloud config validate
```

## System Integration

After package-style installation, the project ships data for:

- User systemd unit: `whisper-aloud.service`
- D-Bus service name: `org.fede.whisperaloud`
- Desktop entry: `org.fede.whisperaloud.desktop`

Useful commands:

```bash
systemctl --user start whisper-aloud
systemctl --user status whisper-aloud
journalctl --user -u whisper-aloud -f
busctl --user introspect org.fede.whisperaloud /org/fede/whisperaloud
```

## Configuration

Runtime config file:

- `~/.config/whisper_aloud/config.json`

Data location:

- `~/.local/share/whisper_aloud/`

Example:

```json
{
  "model": {"name": "base", "device": "auto", "compute_type": "int8"},
  "transcription": {"language": "es"},
  "clipboard": {"auto_copy": true, "auto_paste": true},
  "hotkey": {"toggle_recording": "<Super><Alt>r"}
}
```

Environment overrides are supported (for example):

```bash
export WHISPER_ALOUD_MODEL_NAME=small
export WHISPER_ALOUD_MODEL_DEVICE=cpu
```

## Development

```bash
source .venv/bin/activate
pip install -e ".[dev]"

# Deterministic test subset (no display/audio/dbus integration tests)
./scripts/test_ci.sh

# Quality checks
python -m ruff check src tests
python -m mypy src
```

## Repository Layout

```text
src/whisper_aloud/
  audio/         # recording + processing
  clipboard/     # copy/paste integration
  persistence/   # SQLite history + archive
  service/       # daemon, dbus client, hotkey, tray
  ui/            # GTK4 client
  __main__.py    # CLI entrypoint

data/            # systemd/dbus/desktop metadata
scripts/         # validation and utility scripts
tests/           # unit + integration tests
```

## Documentation Map

- `INSTALL.md` - full installation guide
- `DEPENDENCIES.md` - system and Python dependency reference
- `TROUBLESHOOTING.md` - common runtime issues and fixes
- `CHANGELOG.md` - user-facing release history
- `docs/release-checklist.md` - manual smoke and release procedure
- `scripts/README.md` - validation and utility scripts
- `examples/README.md` - example scripts

## Release Notes

- `release-0.1.0` tag freezes the pre-refactor `master` snapshot.
- Current codebase targets the `0.2.0` daemon-first migration release.

## Contributing

1. Create a feature branch from `master`
2. Keep changes scoped and tested
3. Run `./scripts/test_ci.sh` before opening PRs
4. Include rationale for user-facing behavior changes

## License

MIT (declared in `pyproject.toml`).
