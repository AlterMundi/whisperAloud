# Input gain normalization (0.3.0)

Status: implemented (digital stage). Hardware source-volume backend: deferred
(see "Optional hardware backend").

## Problem

Every stage of the live chain (`HighPass → NoiseGate → AGC → Denoiser →
PeakLimiter`, plus `trim_silence` ahead of it) was tuned against an input
level nobody controls. The mic gain belongs to the user and varies by
device, room and person. Field recording (Flatpak, WHISP-93):

| measure | value |
|---|---|
| raw RMS (whole recording) | -47 dBFS |
| raw peak | -29 dBFS |
| noise floor | about -66 dBFS |

At that level the fixed NoiseGate (open at -40 dBFS, close at -46 dBFS)
stayed shut through most of the speech, so the transcript came out
incomplete. Inside Flatpak, `GainController` (wpctl) can't help because the
sandbox has no `wpctl`.

Chasing per-stage thresholds against an un-normalized input is the wrong
fix. The input should be normalized **first**, so that every later stage
sees speech at a known level.

## Decision (maintainer scope, 2026-10-04)

1. Add **one new stage in front of the existing chain**: a digital adaptive
   input normalizer. The existing chain, its order, its thresholds and its
   config keys stay exactly as they are. `trim_silence` stays as is too; it
   just runs on normalized audio now.
2. By default, **don't touch the device or source volume**. The mic gain
   belongs to the user.
3. Hardware (PulseAudio-protocol source volume) control becomes an
   **optional backend that is off by default**
   (`input_gain.hardware_control = false`). This release defers it (see
   below).
4. `GainController` (wpctl, `recording_flow.raise_mic_gain`) keeps its
   current behaviour. "Proposal" below argues for a different default.

## How other apps do it

- **WebRTC APM AGC2 (Chromium, Electron apps, Meet, Discord):** the
  default "adaptive digital" controller never touches the OS mic volume.
  It estimates the *speech* level from frames that the VAD marks as voiced
  and applies a smoothed digital gain toward a target. The gain is bounded:
  by default it's capped at 30 dB, it can rise by at most 3 dB/s, it keeps
  headroom so it can't clip, and it's capped so the amplified noise floor
  stays under a maximum output noise level (-50 dBFS). A limiter follows.
  The older analog AGC1 path that moved the OS mixer volume is the legacy,
  opt-in route.
- **Per-app capture stream:** under PipeWire and PulseAudio each app gets
  its own capture stream (a source-output), and that stream has its own
  volume. Apps that do want hardware-ish control change that stream volume,
  not the shared source volume that every other app also hears. This is the
  right target if the hardware backend ever lands.
- **Neural VAD and ASR are fairly gain-robust:** Silero VAD (used by
  faster-whisper's `vad_filter`) and Whisper's log-mel front end both cope
  with level changes far better than an energy gate does. The biggest
  practical win from normalizing is therefore that the *energy-based*
  stages (NoiseGate, AGC, `trim_silence`'s guards) land in their designed
  operating region. Making Whisper itself "hear better" is secondary.

## The stage: `InputNormalizer` (`audio/input_gain.py`)

Pure numpy and stateful. It works on 10 ms frames at the pipeline rate
(16 kHz, after resampling).

### Measurement (per 10 ms frame)

- `frame_db`: RMS level in dBFS. `peak`: max |x|.
- Frames below -90 dBFS are **digital silence**: device warm-up zeros,
  muted input and so on. They're ignored entirely, so they can never drag
  the noise-floor estimate down to -inf.
- **Noise floor:** a minimum-statistics tracker. It falls instantly to a
  quieter frame and rises slowly, at 3 dB per second of audio, so a long
  stretch of speech doesn't drag it up.
- **Voiced:** `frame_db >= floor + 10 dB` and `frame_db >= -75 dBFS`.
  Near-silence and pauses are never voiced, so they never move the
  estimate. This is what stops the loop from pumping noise up during
  pauses.
- **Speech level:** the dB-domain mean of voiced frames. It is cumulative
  for the first 0.5 s of voiced audio, then an EMA with a 1 s time constant
  of *voiced* audio. It counts as valid once 0.2 s of voiced audio has been
  seen.

### Gain law

```
G* = target_dbfs - speech_db                        (aim)
G* = min(G*, max_gain_db)                           (bounded boost)
G* = min(G*, max_output_noise_dbfs - noise_floor)   (don't lift noise above the cap)
G* = max(G*, 0)                                     (amplify only; never attenuate)
per frame:  g = min(G, peak_ceiling_dbfs - frame_peak_db)   (headroom: never clip)
            g rises back after a headroom cut at <= 60 dB/s (release)
```

Within a frame, a rising gain is ramped linearly from the previous value,
and every sample's gain is at most that frame's `g`. A falling gain applies
from the frame's first sample. So `|out| <= 10^(peak_ceiling_dbfs/20)`
holds **by construction**, and the clip test checks it.

| default | value | why |
|---|---|---|
| `target_dbfs` | -20 dBFS | Speech RMS at -20 leaves about 17 dB of crest-factor headroom below the -3 dBFS peak ceiling. It sits 2 dB under the unchanged AGC target (-18), so the AGC only makes small corrections, and 20 dB above the unchanged gate's -40 open threshold. |
| `max_gain_db` | +30 dB | Covers the field case: about -45 to -47 dBFS speech needs +25 to +27 dB. It matches WebRTC AGC2's default cap. More than that only amplifies a nearly dead channel. |
| `max_output_noise_dbfs` | -50 dBFS | Same as AGC2. It keeps the amplified noise floor more than 4 dB under the unchanged NoiseGate *close* threshold (-46 dBFS), so the gate still closes in pauses after normalization. In the field case (floor -66) this caps the boost at +16 dB, which puts speech around -26 to -31 dBFS: well inside the gate's open region instead of muted. |
| `peak_ceiling_dbfs` | -3 dBFS | Headroom above which no normalized sample may go. The unchanged PeakLimiter (-1 dBFS) still handles anything the AGC adds later. |
| `max_gain_change_db_per_s` | 10 dB/s | Upward slew for the **streaming** path only, because the batch path uses a single non-causal gain. AGC2 uses 3 dB/s, but it runs for whole calls. Dictation sessions last seconds, and the session starts from the previous session's gain (in-memory), so 10 dB/s is a compromise between convergence speed and audible pumping. Downward moves are not slew-limited. |

The stage **only amplifies**. Loud input goes through at 0 dB, and the
unchanged AGC and limiter handle it as before. That keeps the change
strictly additive for users whose mic was already hot.

### Where it runs

The PortAudio callback still only copies frames. The normalizer never runs
in it.

- **Batch (`AudioRecorder.stop()`):** `mono → resample(16k) →
  InputNormalizer.normalize_buffer → trim_silence → AudioPipeline.process`
  (which is unchanged). `normalize_buffer` makes two passes: pass 1 runs
  the estimator over the whole recording, and pass 2 applies the resulting
  constant `G` with only the per-frame headroom cuts. Because `G` is
  constant, the batch path can't pump at all.
- **Streaming (`on_processed_chunk`):** `resample → InputNormalizer.process_chunk
  → AudioPipeline.process_chunk`. A fresh normalizer is built per session
  and estimates causally as it goes. It starts from the last *batch* gain
  of this recorder, kept in memory and not persisted. While nothing is
  voiced (silence), the gain holds and never rises.

### Observability

After each batch normalization, an INFO line reports: speech level before,
noise floor, voiced seconds, applied gain min/max/final, what limited the
gain (`target`, `max_gain`, `noise`, or `no_speech`), and speech level
after. The streaming session logs the same summary when its worker stops.

`GetStatus()["input_gain"]` (`a{sv}`) contains:

- `mode`: `"digital"`, or `"off"`
- `enabled`, `target_dbfs`, `max_gain_db`, `hardware_control`
- `last_gain_db` and `last_speech_dbfs`, from the last batch normalization;
  `NaN` before the first one

`mic_gain_control` (WHISP-93) is unchanged. It still reports wpctl
availability.

## Config

New section `input_gain` (all keys optional):

| key | default | env override |
|---|---|---|
| `enabled` | `true` | `WHISPER_ALOUD_INPUT_GAIN_ENABLED` |
| `target_dbfs` | `-20.0` | `WHISPER_ALOUD_INPUT_GAIN_TARGET_DBFS` |
| `max_gain_db` | `30.0` | `WHISPER_ALOUD_INPUT_GAIN_MAX_GAIN_DB` |
| `max_output_noise_dbfs` | `-50.0` | `WHISPER_ALOUD_INPUT_GAIN_MAX_OUTPUT_NOISE_DBFS` |
| `peak_ceiling_dbfs` | `-3.0` | — |
| `max_gain_change_db_per_s` | `10.0` | — |
| `hardware_control` | `false` | `WHISPER_ALOUD_INPUT_GAIN_HARDWARE_CONTROL` |

`hardware_control = true` is accepted, but in this release it only logs a
WARNING and the stage stays digital-only (see below). `recording_flow.*`
and `audio_processing.*` are untouched.

## Optional hardware backend (deferred)

Design kept for a follow-up. Nothing in this release changes any device or
stream volume.

- **Backend:** the PulseAudio native protocol through `libpulse.so.0`.
  Use either a minimal ctypes binding (no new dependency: the GNOME
  runtime and every desktop distro ship libpulse) or `pulsectl` (pure
  Python over ctypes, Debian has `python3-pulsectl`). It works inside
  Flatpak (`--socket=pulseaudio`, PipeWire-pulse on the host) and
  natively.
- **Target:** the capture stream's own **source-output** volume, found by
  `application.process.id == getpid()`, in preference to the shared
  source volume (see "How other apps do it"). Fall back to the source
  reported by that source-output, then to the default source.
- **Loop:** the same measurement as the digital stage. Above it sits a
  slow outer loop: drop fast on peaks near -3 dBFS, rise slowly, step at
  most ±6 dB per update, keep a ±3 dB hysteresis band, allow no more than
  one change per 0.5 s of audio, and skip the next 150 ms of audio after a
  change (settling) so the loop never measures its own transient. Scale
  is cubic (`dB = 60·log10(v)`), with bounds of [0.1, 1.0] by default.
  Above 100% the server applies a software gain that is no better than
  ours, and it clips in the server before our clip guard can measure it.
  The digital stage covers anything that remains.
- **Safety:** snapshot the original volume (per channel, plus mute) before
  any change. Never unmute. Restore at stop, cancel, or exception. Keep a
  crash-recovery file at `$XDG_RUNTIME_DIR` (Flatpak:
  `$XDG_RUNTIME_DIR/app/$FLATPAK_ID`) and replay it on the next daemon
  start, using `GainController`'s pattern. Under
  `WHISPER_ALOUD_TEST_MODE=1` the backend is disabled outright.
- **Why deferred:** it's opt-in and off by default. It needs a new binding,
  and it can't be verified without driving a live sound server, which is
  out of bounds for this ticket's test rules. The digital stage solves the
  field failure on its own.

## Proposal: `recording_flow.raise_mic_gain` default

Proposal only; this release doesn't change it. `GainController` raises the
*shared* device source volume to a fixed 0.85 before every native
recording and restores it afterwards. It isn't measurement-driven. It
changes what every other app hears from the mic while a recording is
running, and if the daemon dies between raise and restore, the restore
depends on the crash-recovery file. With the digital stage in front of the
chain, the reason for it (a quiet mic) is already handled without touching
user state. **Proposal: default `raise_mic_gain` to `false` in the next
minor release.** Keep the key for users who want it, and revisit once the
optional hardware backend (per-stream volume, measurement-driven) exists.

## Observation (not changed, per scope)

On normalized input, `AGC`'s 300 ms window still sees the amplified noise
in pauses once the gate's release tail has decayed, and it can boost that
noise by up to its `max_gain_db` (20 dB). This behaviour predates this
change. AGC only "holds" below an RMS of 1e-8, and the gate's 50 ms release
tail takes several hundred milliseconds to fall that far. This change
doesn't make the effect worse: the -50 dBFS noise cap is what lets the gate
close in pauses at all. The effect hasn't been measured here. A follow-up
could make AGC hold on unvoiced frames using the same voiced decision.
