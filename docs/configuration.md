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

## Auto-paste (`clipboard`)

After a transcription the daemon copies the text to the clipboard
(`auto_copy`) and then, with `auto_paste`, puts it into the app you were
typing in. `paste_backend` chooses how:

| `clipboard.paste_backend` | What happens |
|---|---|
| `auto` (default) | The GNOME Shell extension's `InsertText` when the extension is enabled and reports the capability (extension version 5 or newer). Otherwise, on a native install, `ydotool` (Wayland) or `xdotool` (X11) sends the paste shortcut, as in earlier releases. Inside Flatpak, without the extension, the text stays on the clipboard and a notification says "Text copied — press Ctrl+V to paste". `auto` **never** uses the RemoteDesktop portal. |
| `extension` | The extension only. Without it, clipboard only, with the same notification. |
| `remote-desktop` | The xdg RemoteDesktop portal: the 0.3.0 method, kept as the rollback switch ([ADR 0001](decisions/0001-baseline-before-native-text-insertion.md)). One consent dialog the first time; GNOME then shows the orange "remote access" indicator while the session is open. The session is prewarmed at daemon start only with this setting, and closed as soon as you switch away from it. |
| `clipboard` | Never paste. The text stays on the clipboard, with the "press Ctrl+V" notification. |

The extension inserts the text from inside GNOME Shell: it commits it as
typed input when a text field has input-method focus, and otherwise pastes
it with Shift+Insert (Ctrl+Shift+Insert in terminals). It refuses, and the
daemon tells you the text is on the clipboard, when the screen is locked, a
system dialog is open, a password field has focus, or the window you
dictated into is no longer focused. Details:
[`contrib/gnome-shell-extension/README.md`](../contrib/gnome-shell-extension/README.md#text-insertion-orgfedewhisperaloudtextinput).

| key | default | env override |
|---|---|---|
| `clipboard.auto_copy` | `true` | `WHISPER_ALOUD_CLIPBOARD_AUTO_COPY` |
| `clipboard.auto_paste` | `true` | `WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE` |
| `clipboard.paste_backend` | `"auto"` | `WHISPER_ALOUD_CLIPBOARD_PASTE_BACKEND` |
| `clipboard.paste_to_captured_window` | `true` | `WHISPER_ALOUD_CLIPBOARD_PASTE_TO_CAPTURED_WINDOW` |
| `clipboard.paste_shortcut` | `"ctrl+shift+v"` | `WHISPER_ALOUD_CLIPBOARD_PASTE_SHORTCUT` |
| `clipboard.paste_delay_ms` | `100` | `WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS` |

`paste_shortcut` applies to `ydotool`/`xdotool` and `remote-desktop` only;
the extension picks its own layout-independent chord. To roll back to the
0.3.0 behavior without reinstalling, set `"paste_backend":
"remote-desktop"` in `config.json` (or run the daemon with
`WHISPER_ALOUD_CLIPBOARD_PASTE_BACKEND=remote-desktop`) and reload the
config.

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
