# Configuration reference

This is the full config reference moved out of the README (WHISP-85) to
keep the README scannable.

## File location

Runtime config file:

- `~/.config/whisper_aloud/config.json`

Data location (history database, optional audio archive):

- `~/.local/share/whisper_aloud/`

## Example

```json
{
  "model": {"name": "base", "device": "auto", "compute_type": "int8"},
  "transcription": {"language": "es"},
  "clipboard": {"auto_copy": true, "auto_paste": true},
  "hotkey": {"toggle_recording": "<Super><Alt>r"}
}
```

Validate a config file:

```bash
whisper-aloud config validate
```

## Environment variable overrides

Every config field can be overridden with an environment variable prefixed
`WHISPER_ALOUD_`, for example:

```bash
export WHISPER_ALOUD_MODEL_NAME=small
export WHISPER_ALOUD_MODEL_DEVICE=cpu
```

## Live dictation high-pass filter

Live dictation's audio pipeline (`gate -> AGC -> denoise -> limiter`) can
also run a causal high-pass first (`audio_processing.highpass_hz`, env
override `WHISPER_ALOUD_AUDIO_HIGHPASS_HZ`), the live equivalent of file
transcription's `transcription.highpass_hz` — kept as a separate knob since
the two run on different code paths and must never both apply to the same
audio. Unlike the file path (on by default at 80Hz), this one defaults to
`0` (off, opt-in): measured against a real rumble fixture, the causal filter
showed a small net *negative* for Silero VAD retention right around the
project's default VAD threshold, and live dictation does run Silero VAD by
default (`Transcriber.transcribe_numpy`) — see
`AudioProcessingConfig.highpass_hz`'s docstring and
`scripts/measure_live_highpass_vad_retention.py` for the full measurement.

## See also

- [`docs/dbus-api.md`](dbus-api.md) — `GetConfig`/`SetConfig`/`ReloadConfig`
  over D-Bus
- [`docs/cli-reference.md`](cli-reference.md) — CLI usage
- [`TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) — common runtime issues
