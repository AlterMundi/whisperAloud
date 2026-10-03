<div align="center">

# WhisperAloud

**Offline voice dictation for the Linux desktop.** A D-Bus daemon does the
recording and transcription with Whisper (`faster-whisper`) or Parakeet
(sherpa-onnx); a GTK4/libadwaita app and a CLI are thin clients. Nothing
leaves your machine.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/AlterMundi/whisperAloud?include_prereleases)](https://github.com/AlterMundi/whisperAloud/releases)
[![CI](https://github.com/AlterMundi/whisperAloud/actions/workflows/ci.yml/badge.svg)](https://github.com/AlterMundi/whisperAloud/actions/workflows/ci.yml)
[![Flathub](https://img.shields.io/badge/Flathub-coming%20soon-lightgrey)](docs/flatpak.md)

</div>

> **Demo — placeholder.** This spot will hold a GIF of the hotkey → speak →
> live transcript → paste flow, recorded during Fede's field test
> ([`docs/validation/0.3.0-field-checklist.md`](docs/validation/0.3.0-field-checklist.md)). Expected file:
> `docs/assets/demo-dictation.gif`. See [`docs/assets/README.md`](docs/assets/README.md)
> for the full list of pending screenshots/GIFs.

> **Main window — placeholder.** Idle and recording states of the GTK4
> app. Expected files: `docs/assets/demo-main-window.gif`,
> `docs/assets/demo-recording.gif`.

## Why WhisperAloud

- **Fully offline** — speech-to-text runs locally (`faster-whisper`, or the
  faster Parakeet/sherpa-onnx engine); no audio or transcript is ever sent
  to a server.
- **Daemon-first** — one D-Bus service (`org.fede.whisperaloud`) owns the
  microphone, the model, and history; the GTK4 GUI, CLI, tray, and hotkey
  are thin clients that can restart independently of a running recording.
- **Linux/GNOME-native** — GTK4 + libadwaita UI, systemd user service,
  tray indicator, XDG Portal / libkeybinder3 global hotkey.
- **Streaming partials** — with the Nemotron streaming engine, words appear
  in the main window as they're recognized, not only after you stop.
- **Searchable history** — every transcript is kept in a local SQLite +
  FTS5 store, exportable to Markdown/JSON/CSV/plain text.

## Install

### Flatpak (recommended, coming soon)

A Flatpak manifest is being finalized on a separate branch
(`packaging/WHISP-61`); see [`docs/flatpak.md`](docs/flatpak.md) for the
sandbox/portal details it depends on (global hotkey and paste both go
through `org.freedesktop.portal.Desktop`, no raw X11/Wayland access).
Once published:

```bash
flatpak install flathub org.fede.whisperaloud   # not yet live — see badge above
```

Until then, use the source install below.

### From source (recommended today)

```bash
git clone git@github.com:AlterMundi/whisperAloud.git
cd whisperAloud
./install.sh            # add --dev for an editable/dev install
```

This installs system dependencies (Debian/Ubuntu/Fedora/Arch), creates a
`--system-site-packages` venv at `~/.venvs/whisper_aloud` (required so GTK4's
`python3-gi` bindings are visible), and registers the systemd user service
and desktop entry. Full manual steps, per-distro package names, and
uninstall instructions: [`INSTALL.md`](INSTALL.md) and
[`DEPENDENCIES.md`](DEPENDENCIES.md).

### Debian package (dev/packaging use only)

`debian/` contains packaging for `whisper-aloud` (arch-all Python code) and
`whisper-aloud-engine-ctranslate2` (vendored `faster-whisper` venv). These
are not yet published to any apt repository — build and install them
locally only if you're testing the packaging itself, not as an end-user
install path.

## Quickstart

**Dictate into any app:**

```bash
whisper-aloud-gui        # launch the GTK4 app, or...
whisper-aloud --daemon   # ...run the daemon headless
```

Press the global hotkey (default `<Super><Alt>r`) to start/stop recording;
the transcript is copied to the clipboard and, where supported, pasted
automatically into the focused window.

**Transcribe a recorded meeting:**

```bash
whisper-aloud transcribe meeting.m4a \
  --engine parakeet --model parakeet-tdt-0.6b-v3 \
  --format srt --output meeting.srt --verbose
```

Long files are split into ~25s chunks (cut at quiet frames so words aren't
split) and stitched back into one timestamped output; `m4a`/`mp3`/etc. are
decoded via `ffmpeg`/PyAV automatically. See
[`docs/cli-reference.md`](docs/cli-reference.md) for engine/model selection,
output formats, and VAD/normalization flags.

**Export your history:**

```bash
whisper-aloud history export --format md --output history.md
whisper-aloud history export --format json --query "budget" --favorites
```

Filters compose (`--since`/`--until`, `--favorites`, `--query`, `--limit`,
capped at 5000). Full semantics:
[`docs/dbus-api.md#exporthistory`](docs/dbus-api.md#exporthistory).

## Privacy and offline operation

WhisperAloud makes no network calls during recording or transcription.
Whisper/Parakeet models are downloaded once (on first use, with an explicit
consent step for models under restrictive licenses) and cached under
`~/.cache/huggingface/`; after that, everything — audio capture, the
processing pipeline, transcription, and history — runs locally. Config
lives at `~/.config/whisper_aloud/config.json`, history/data at
`~/.local/share/whisper_aloud/`. See [`docs/configuration.md`](docs/configuration.md)
for the full config reference and environment-variable overrides.

```text
systemd user service
        |
        v
org.fede.whisperaloud (daemon)
  - recorder + processing (gate -> AGC -> denoise -> limiter)
  - transcriber (faster-whisper / Parakeet / Nemotron streaming)
  - clipboard (copy, and portal-based paste under Flatpak)
  - persistence (SQLite + FTS5 history)
  - hotkeys (XDG Portal / libkeybinder3) + tray
        ^
        |
  +-----+-----------------------+
  |                             |
GTK4 GUI client             CLI client
(whisper-aloud-gui)         (whisper-aloud ...)
```

## Integrations: the D-Bus API

The daemon publishes `org.fede.whisperaloud.Control` on the session bus as
a stable, versioned API (read its `ApiVersion` property) — every
recording/transcription/history/config operation the GUI and CLI use is
available to any other client or script the same way:

```bash
busctl --user introspect org.fede.whisperaloud /org/fede/whisperaloud
```

Full method/signal/property reference, a state diagram, and
`busctl`/`gdbus`/Python examples: [`docs/dbus-api.md`](docs/dbus-api.md).

## What's in 0.3.0

See [`CHANGELOG.md`](CHANGELOG.md) for the full list. Highlights:

- Catalog-driven engine selection (`faster-whisper`, Parakeet, Nemotron
  streaming), with in-app model download (progress, cancel, license
  consent).
- Live partial transcripts in the main window while recording.
- `whisper-aloud transcribe` with chunked long-audio support, `srt`/`vtt`/
  `json` output, and `m4a`/non-WAV input.
- `whisper-aloud history list`/`favorites`/`export` CLI commands.
- D-Bus activation: any client starts the daemon on demand, through a
  single, non-racing mechanism.
- Portal-based paste when running as a Flatpak.

## Roadmap

- Finish and publish the Flatpak manifest, then submit to Flathub —
  tracked on `packaging/WHISP-61`; see [`docs/flatpak.md`](docs/flatpak.md)
  for the sandbox work already done to support it.
- Execute the 0.3.0 field-validation pass
  ([`docs/validation/0.3.0-field-checklist.md`](docs/validation/0.3.0-field-checklist.md))
  on a real desktop and fold its findings back into the backlog.
- Broader community visibility: listing on
  [`awesome-voice-typing`](docs/outreach/awesome-voice-typing.md) and a
  public launch post (drafts in [`docs/outreach/`](docs/outreach/)).

This is a working list, not a commitment with dates — see the repo's issue
tracker for day-to-day priorities.

## Development process

WhisperAloud is designed and maintained by Fede. Implementation, tests, and
documentation are produced with heavy use of AI pair-programming (Claude
Code), with every change going through the automated test suite and human
review before it merges — see [`CLAUDE.md`](CLAUDE.md) for the conventions
this repository uses with AI coding agents.

## Contributing

1. Create a feature branch from `master`.
2. Keep changes scoped and tested — see [`CONTRIBUTING.md`](CONTRIBUTING.md)
   for the full workflow, coding style, and test commands
   (`./scripts/test_ci.sh` must pass before opening a PR).
3. Include rationale for user-facing behavior changes.

## Documentation

- [`INSTALL.md`](INSTALL.md) — full installation guide
- [`DEPENDENCIES.md`](DEPENDENCIES.md) — system and Python dependency reference
- [`docs/cli-reference.md`](docs/cli-reference.md) — full CLI reference (transcribe, history, config)
- [`docs/configuration.md`](docs/configuration.md) — config file and environment variable reference
- [`docs/dbus-api.md`](docs/dbus-api.md) — versioned D-Bus API reference
- [`docs/flatpak.md`](docs/flatpak.md) — Flatpak sandbox/portal notes
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — common runtime issues and fixes
- [`docs/release-checklist.md`](docs/release-checklist.md) — manual smoke and release procedure
- [`docs/repo-metadata.md`](docs/repo-metadata.md) — GitHub repo presentation checklist (topics, social preview, etc.)
- [`scripts/README.md`](scripts/README.md) — validation and utility scripts
- [`examples/README.md`](examples/README.md) — example scripts

## Acknowledgments

Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
OpenAI's [Whisper](https://github.com/openai/whisper), the
[Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) models via
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), and GNOME's
GTK4/libadwaita.

## License

MIT — see [`LICENSE`](LICENSE). This covers WhisperAloud's own code only:
speech models are downloaded at runtime (never bundled, including in the
Flatpak) and carry their own licenses; see
[`docs/THIRD_PARTY_MODELS.md`](docs/THIRD_PARTY_MODELS.md).
