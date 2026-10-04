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

## Input normalization (`input_gain`)

Before anything else in the live chain (trim, high-pass, gate, AGC,
denoise, limiter), live audio goes through a digital input normalizer. It
measures your speech level, from voiced frames only, and applies a smooth,
amplify-only gain toward a target. A quiet mic then no longer leaves the
fixed-threshold stages (like the -40 dBFS noise gate) cutting off speech. It
never changes your mic's device volume. Design and defaults:
[`design/input-gain-loop.md`](design/input-gain-loop.md).

| key | default | env override |
|---|---|---|
| `input_gain.enabled` | `true` | `WHISPER_ALOUD_INPUT_GAIN_ENABLED` |
| `input_gain.target_dbfs` | `-20.0` | `WHISPER_ALOUD_INPUT_GAIN_TARGET_DBFS` |
| `input_gain.max_gain_db` | `30.0` | `WHISPER_ALOUD_INPUT_GAIN_MAX_GAIN_DB` |
| `input_gain.max_output_noise_dbfs` | `-50.0` | `WHISPER_ALOUD_INPUT_GAIN_MAX_OUTPUT_NOISE_DBFS` |
| `input_gain.peak_ceiling_dbfs` | `-3.0` | — |
| `input_gain.max_gain_change_db_per_s` | `10.0` | — |
| `input_gain.hardware_control` | `false` | `WHISPER_ALOUD_INPUT_GAIN_HARDWARE_CONTROL` |

`hardware_control` is reserved. Setting it to `true` only logs a warning in
this release. `GetStatus()["input_gain"]` reports the active mode and the
last recording's gain. Each recording also logs one INFO line starting with
`Input normalization (batch)`. That line shows the speech level before and
after normalization, the gain applied (min, max and final), and what
limited the gain.

`recording_flow.raise_mic_gain` (native installs only, via `wpctl`) is
separate and unchanged. It raises the shared mic volume to
`target_gain_linear` while recording and restores it afterwards.

## Start in background at login (`startup`)

| key | default | env override |
|---|---|---|
| `startup.autostart_background` | `false` | `WHISPER_ALOUD_STARTUP_AUTOSTART_BACKGROUND` |

Set it from Settings → General → Startup, not by hand: the switch is what
creates or removes the login entry (the Background portal inside Flatpak,
`~/.config/autostart/whisper-aloud-autostart.desktop` natively), and it
stores here the state that was actually confirmed. Editing the key alone
(or `SetConfig` over D-Bus) changes what `GetStatus()["autostart"]`
reports, not what starts at login. The login entry runs the daemon only
(`whisper-aloud --daemon`), never the window. See
[`flatpak.md`](flatpak.md#start-in-background-at-login-background-portal).

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
