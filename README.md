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

- Offline transcription with Whisper models (`tiny` to `large-v3`), plus
  catalog-driven Parakeet and Nemotron (streaming) engines
- Live partial transcripts in the main window while recording
- In-app model download with progress, cancel, and license consent
- Real-time audio level updates while recording
- Voice activity handling and audio processing pipeline
- D-Bus control API (`org.fede.whisperaloud`), with on-demand activation
- CLI commands for daemon lifecycle, recording control, file transcription
  (with chunking and SRT/VTT/JSON output), and history list/export
- GTK4 client UI with settings, model picker, and history panel
- Systemd user service support

## What's New in 0.3.0

See `CHANGELOG.md` for the full list. Highlights: selectable speech engines
(Parakeet, Nemotron streaming) with in-app model download, live partial
transcripts, a `transcribe` CLI with chunking/format/engine options and m4a
input, history export, D-Bus activation with a single start mechanism, and
Flatpak portal paste.

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
whisper-aloud transcribe path/to/audio.wav --model base --language es --device auto
```

### Choosing an engine and model

`--engine`/`--model` pick from the STT catalog (`whisper-aloud models` lists it).
With neither flag, the catalog's recommended model for `--language` is used
(faster-whisper's `base` for "auto", matching the historical default):

```bash
# Explicit engine + model
whisper-aloud transcribe meeting.wav --engine parakeet --model parakeet-tdt-0.6b-v3

# Just a model name -- its engine is resolved automatically
whisper-aloud transcribe meeting.wav --model parakeet-tdt-0.6b-v3
```

A model pending license review (shown as "(pending license review)" by
`whisper-aloud models --all`) is never auto-selected and is rejected unless
passed explicitly together with `--accept-license`.

### Output formats and timestamps

`--format` writes `txt` (default, plain text -- unchanged from before unless
`--timestamps` is also given), `srt`, `vtt`, or `json`, to stdout or to
`--output FILE`:

```bash
whisper-aloud transcribe meeting.wav --format srt --output meeting.srt
whisper-aloud transcribe meeting.wav --timestamps          # txt with [hh:mm:ss] lines
```

### Transcribing a long meeting

faster-whisper decodes long audio natively. Other engines (Parakeet and other
sherpa-onnx backends) take one bounded clip per call, so `transcribe`
automatically splits long input into ~25s chunks, cutting at the quietest
100ms frame near each chunk boundary so words aren't split, and stitches the
timestamps back together:

```bash
# A 30+ minute recording, decoded via ffmpeg/PyAV if it's not a WAV (m4a, mp3, ...),
# transcribed in chunks with Parakeet (~20x realtime on CPU vs. ~1x for
# faster-whisper large-v3), written as a timestamped SRT:
whisper-aloud transcribe meeting.m4a \
  --engine parakeet --model parakeet-tdt-0.6b-v3 \
  --format srt --output meeting.srt --verbose
```

`--verbose` prints per-chunk progress (`chunk i/N elapsed=... rtf=...`) to
stderr. `--no-vad` disables faster-whisper's VAD filter for a single run
without touching daemon/GUI VAD settings, and `--normalize` applies a dynamic
loudness pass (reusing the daemon's own AGC) before transcription, useful for
quiet or unevenly leveled recordings.

### Config validation

```bash
whisper-aloud config validate
```

### Browsing and exporting history

These go through the running daemon over D-Bus, like every other `history`
command (`whisper-aloud --daemon` must already be running):

```bash
# Most recent entries (default limit: 10)
whisper-aloud history list
whisper-aloud history list --limit 25
whisper-aloud history list --favorites

# Full-text search (same FTS5 engine as the GUI's history panel)
whisper-aloud history search "budget meeting"

# Export -- the daemon renders the content and returns it; this command
# writes it to --output (or prints it to stdout if --output is omitted)
whisper-aloud history export --format md --output history.md
whisper-aloud history export --format json --query "budget" --favorites
whisper-aloud history export --format csv --since 2026-01-01 --until 2026-01-31
```

`--format` is one of `md`/`json`/`csv`/`txt`. Filters (`--since`/`--until`,
ISO dates; `--favorites`; `--query`; `--limit`) all compose. `--limit`
defaults to 500 and is capped at 5000 entries per export -- see
[`docs/dbus-api.md`](docs/dbus-api.md#exporthistory) for the exact
semantics and why `ExportHistory` returns content instead of accepting an
output path itself.

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

## Integrations / D-Bus API

WhisperAloud's daemon publishes `org.fede.whisperaloud.Control` on the
session bus as a stable, versioned API (read its `ApiVersion` property) —
every recording/transcription/history/config operation the GUI and CLI use
is available to other clients and scripts the same way. See
[`docs/dbus-api.md`](docs/dbus-api.md) for the full method/signal/property
reference, a state diagram, and `busctl`/`gdbus`/Python examples.

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

Live dictation's audio pipeline (`gate -> AGC -> denoise -> limiter`) can
also run a causal high-pass first (`audio_processing.highpass_hz`, env
override `WHISPER_ALOUD_AUDIO_HIGHPASS_HZ`), the live equivalent of file
transcription's `transcription.highpass_hz` -- kept as a separate knob since
the two run on different code paths and must never both apply to the same
audio. Unlike the file path (on by default at 80Hz), this one defaults to
`0` (off, opt-in): measured against a real rumble fixture, the causal filter
showed a small net *negative* for Silero VAD retention right around the
project's default VAD threshold, and live dictation does run Silero VAD by
default (`Transcriber.transcribe_numpy`) -- see
`AudioProcessingConfig.highpass_hz`'s docstring and
`scripts/measure_live_highpass_vad_retention.py` for the full measurement.

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

## Testing

Several of these wrap the real pytest invocation instead of calling it directly
because the daemon, D-Bus, and GTK touch the real desktop by default (the
session bus, the display, the clipboard); the wrappers give each suite its own
disposable bus/display/HOME so a test run can never reach your session.

```bash
source .venv/bin/activate
pip install -e ".[dev]"

# Unit tests: fast, no display/audio/dbus (same subset CI's `fast` job runs)
./scripts/test_ci.sh

# e2e: real daemon subprocess + fake audio/STT, on a private dbus-run-session bus
./scripts/test_e2e.sh

# GUI smoke tests: real GTK4/libadwaita widgets under a private Xvfb display
# and D-Bus session, with a disposable HOME/XDG tree
./scripts/test_gui.sh

# Any test marked dbus_private (e.g. the hotkey/portal flow), on a private bus
./scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_portal_flow.py
```

`test_e2e.sh` and `test_private_bus.sh` honor a `PYTHON` env var (defaulting to
`python`, i.e. whatever is first on `PATH`) to pick the interpreter, and
`test_gui.sh` honors `WHISPER_ALOUD_VENV_PYTHON` (defaulting to
`~/.venvs/whisper_aloud/bin/python`) for the same purpose — CI uses these to
point at a `--system-site-packages` venv built from the runner's own `python3`.

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
- `docs/dbus-api.md` - versioned D-Bus API reference (org.fede.whisperaloud.Control)
- `TROUBLESHOOTING.md` - common runtime issues and fixes
- `CHANGELOG.md` - user-facing release history
- `docs/release-checklist.md` - manual smoke and release procedure
- `scripts/README.md` - validation and utility scripts
- `examples/README.md` - example scripts

## Release Notes

- `release-0.1.0` tag freezes the pre-refactor `master` snapshot.
- Current codebase targets the `0.3.0` release.

## Contributing

1. Create a feature branch from `master`
2. Keep changes scoped and tested
3. Run `./scripts/test_ci.sh` before opening PRs
4. Include rationale for user-facing behavior changes

## License

MIT (declared in `pyproject.toml`).
