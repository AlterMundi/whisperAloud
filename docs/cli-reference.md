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

A model pending license review (shown as "(pending license review)" by
`whisper-aloud models --all`) is never auto-selected and is rejected unless
passed explicitly together with `--accept-license`.

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

## Config validation

```bash
whisper-aloud config validate
```

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
