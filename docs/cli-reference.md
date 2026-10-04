# CLI reference

Full `whisper-aloud` command-line reference, moved out of the README
(WHISP-85) to keep the README scannable. See also
[`docs/dbus-api.md`](dbus-api.md) for the daemon operations these commands
drive over D-Bus, and [`docs/configuration.md`](configuration.md) for the
config file these commands read/write.

## Daemon lifecycle and recording control

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

`status` also shows the model readiness state next to the model, e.g.
`Model:    base (cpu) [ready]` (`loading`, `ready`, `missing`,
`downloading`, `error`), plus a `Model error:` line explaining a `missing`
or `error` state. The daemon loads the model in the background right after
it starts; a recording made while it is still `loading` is transcribed as
soon as it is ready.

## File transcription (legacy single-shot mode)

```bash
whisper-aloud transcribe path/to/audio.wav --model base --language es --device auto
```

### Choosing an engine and model

`--engine`/`--model` pick from the STT catalog (`whisper-aloud models` lists
it). With neither flag, the catalog's recommended model for `--language` is
used (faster-whisper's `base` for "auto", matching the historical default):

```bash
# Explicit engine + model
whisper-aloud transcribe meeting.wav --engine parakeet --model parakeet-tdt-0.6b-v3

# Just a model name -- its engine is resolved automatically
whisper-aloud transcribe meeting.wav --model parakeet-tdt-0.6b-v3
```

A model that is still pending license review (shown as "(pending license
review)" by `whisper-aloud models --all`, hidden from the plain `models`
listing) is never auto-selected and is rejected unless passed explicitly
together with `--accept-license`. A model whose license has been reviewed but
is non-standard for this project (e.g. Nemotron's OpenMDW-1.1, or Parakeet's
CC-BY-4.0) shows up normally and needs no `--accept-license`;
`download-model`/`transcribe` print a one-line license notice instead. See
[`THIRD_PARTY_MODELS.md`](THIRD_PARTY_MODELS.md) for what each model license requires.

### Output formats and timestamps

`--format` writes `txt` (default, plain text — unchanged from before unless
`--timestamps` is also given), `srt`, `vtt`, or `json`, to stdout or to
`--output FILE`:

```bash
whisper-aloud transcribe meeting.wav --format srt --output meeting.srt
whisper-aloud transcribe meeting.wav --timestamps          # txt with [hh:mm:ss] lines
```

### Transcribing a long meeting

faster-whisper decodes long audio natively. Other engines (Parakeet and
other sherpa-onnx backends) take one bounded clip per call, so `transcribe`
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
without touching daemon/GUI VAD settings, and `--normalize` applies a
dynamic loudness pass (reusing the daemon's own AGC) before transcription,
useful for quiet or unevenly leveled recordings.

### Reading audio from stdin

`file` may be `-` instead of a path, to read the audio from stdin (buffered
to a private temp file, removed afterwards):

```bash
arecord -f cd -d 10 -t raw | sox -t raw -r 44100 -e signed -b 16 -c 1 - -t wav - \
  | whisper-aloud transcribe -
```

This is also the form to use when running the Flatpak build from a script:
see ["Running `transcribe` inside the Flatpak
sandbox"](flatpak.md#running-transcribe-inside-the-flatpak-sandbox) in
`docs/flatpak.md` for why a plain host path doesn't work there and the two
ways around it (`--file-forwarding`, or piping via stdin as above).

## Config show / validation

```bash
whisper-aloud config show             # grouped, human-readable text (default action for `config` too)
whisper-aloud config show --json      # machine-readable: full config + which keys are env-overridden
whisper-aloud config validate
```

`config show` prints the **full** effective configuration (every section
of `WhisperAloudConfig.to_dict()`: model, transcription, audio, clipboard,
notifications, persistence, audio_processing, hotkey, recording_flow,
input_gain, startup) — defaults, overridden by the config file, overridden
again by any `WHISPER_ALOUD_*` env var, in that order. A value currently
set by an env var is marked `(env)` in the text output, and listed under
`"env_overrides"` (a `{"section.field": "ENV_VAR_NAME"}` map) in the
`--json` output, so it's clear a value didn't come from `config.json`. It
never writes the file; a malformed `config.json` prints a warning to
stderr and falls back to defaults instead of failing. `config validate`
instead reports whether `config.json` parses and summarizes a few key
settings, failing loudly (non-zero exit) on malformed JSON.

## Browsing and exporting history

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
defaults to 500 and is capped at 5000 entries per export — see
[`docs/dbus-api.md#exporthistory`](dbus-api.md#exporthistory) for the exact
semantics and why `ExportHistory` returns content instead of accepting an
output path itself.

## System integration

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
