# WhisperAloud Professional Upgrade Design

**Date:** 2026-02-19
**Status:** Approved
**Branch:** devel
**Audited:** 3x ia-bridge sessions (architecture, hotkey+tray+systemd, packaging)

## Goal

Transform WhisperAloud from a functional core into a professional, robust Linux desktop app — the default built-in transcription tool that makes alternatives unnecessary.

Three axes:
1. **Non-invasive operation** — daemon + global hotkey + tray, no window required
2. **Professional audio** — AGC, noise gate, denoising pipeline
3. **Distribution** — packaged .deb installable on any Debian/Ubuntu

## 1. Architecture: Daemon-First

The D-Bus daemon becomes the single process owning audio, transcription, history, and clipboard. Everything else is a client.

```
systemd user service → daemon (D-Bus) → recorder, transcriber, history, clipboard
                          ↑
         ┌────────────────┼────────────────┐
     GUI (GTK4)     AppIndicator      Global Hotkey
     (optional)     (always active)    (XDG Portal)
     D-Bus client   in daemon process  in daemon process
```

**Lifecycle:**
1. Login → systemd starts `whisper-aloud.service`
2. Daemon registers D-Bus name, global hotkey, tray icon
3. User presses hotkey → daemon records → transcribes → clipboard + notification
4. User opens GUI → connects to daemon via D-Bus → shows live state, history
5. GUI closes → daemon continues running
6. Daemon crashes → systemd restarts in 3s → GUI reconnects via `NameOwnerChanged`

**Key decision:** No fallback mode. The GUI never instantiates Transcriber/Recorder directly. If the daemon is unreachable after retries, GUI shows a diagnostic dialog with `journalctl` hint.

## 2. Audio Pipeline: AGC + Noise Gate + Denoising

Replace the current basic pipeline (trim silence + peak normalize) with a real-time professional pipeline:

```
Microphone → sounddevice callback (100ms chunks)
    │
    ├─ 1. Noise Gate
    │     RMS < threshold → silence
    │     Smooth attack/release (no clicks)
    │
    ├─ 2. AGC (Automatic Gain Control)
    │     Target: -18 dBFS (LUFS-like)
    │     Sliding window RMS (300ms)
    │     Max gain +30dB, min -10dB
    │     Attack 10ms, release 100ms
    │
    ├─ 3. Denoising
    │     noisereduce (Python, spectral gating)
    │     Alternative: RNNoise via subprocess
    │
    └─ 4. Peak Limiter
          Hard limit at -1 dBFS
          Prevents clipping post-AGC
```

**Order rationale:**
- Gate first: cuts background noise before AGC amplifies it
- AGC second: normalizes clean voice level
- Denoising third: cleans residuals the gate missed
- Limiter last: safety against clipping

**Implementation:**
- New `AudioPipeline` class in `audio/audio_processor.py` (replaces current static methods)
- Stateful (tracks RMS for AGC sliding window)
- Each stage toggleable via config
- Level meter reads post-pipeline level

**New config section:**
```python
@dataclass
class AudioProcessingConfig:
    noise_gate_enabled: bool = True
    noise_gate_threshold_db: float = -40.0
    agc_enabled: bool = True
    agc_target_db: float = -18.0
    agc_max_gain_db: float = 30.0
    denoising_enabled: bool = True
    denoising_strength: float = 0.5  # 0.0-1.0
    limiter_enabled: bool = True
    limiter_ceiling_db: float = -1.0
```

## 3. Global Hotkey

Three-level fallback:

| Priority | Backend | When |
|----------|---------|------|
| 1 | XDG Desktop Portal (GlobalShortcuts) | GNOME 46+, KDE 6+, Wayland |
| 2 | libkeybinder3 | X11, any desktop |
| 3 | D-Bus + Custom Shortcut | GNOME 43-45 Wayland (no portal) |

For fallback 3: daemon exposes D-Bus methods, `.desktop` file has Actions (Toggle, Cancel), user configures system shortcut manually. UI shows infobar guiding the user if no automatic backend is available.

**Config:**
```python
@dataclass
class HotkeyConfig:
    toggle_recording: str = "<Super><Alt>r"
    cancel_recording: str = "<Super><Alt>Escape"
```

**Runtime detection:** Try portal first → try libkeybinder3 → fall back to D-Bus-only with user guidance.

## 4. AppIndicator (Tray Icon)

Uses `gi.repository.AyatanaAppIndicator3` (GTK3), lives in daemon process.

**States:**
- Idle: `audio-input-microphone-symbolic`
- Recording: `media-record-symbolic` (red)
- Transcribing: `system-run-symbolic`

**Context menu:**
- Toggle Recording (shows hotkey)
- Open WhisperAloud → opens/raises GUI
- ---
- Last: "truncated text..." → copies to clipboard
- ---
- Settings...
- Quit

**GNOME compatibility:** Requires AppIndicator extension (preinstalled on Ubuntu, `gnome-shell-extension-appindicator` on Debian). If not detected at startup, auto-hide tray and use desktop notifications as fallback.

**Note:** AyatanaAppIndicator3 is GTK3. The daemon runs a GTK3 main loop for the indicator. This is standard and expected.

## 5. Systemd Integration

### User service (`data/whisper-aloud.service`)
```ini
[Unit]
Description=WhisperAloud Transcription Service
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=dbus
BusName=org.fede.whisperaloud
ExecStart=/usr/lib/whisper-aloud/run --daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
```

### D-Bus activation (`data/org.fede.whisperaloud.service`)
```ini
[D-BUS Service]
Name=org.fede.whisperaloud
SystemdService=whisper-aloud.service
```

### SIGTERM handler
On SIGTERM: stop active recording (don't transcribe), flush audio if save_audio=true, emit `StatusChanged("shutdown")`, release D-Bus name, exit 0.

## 6. GNOME Shell Extension

**Deleted.** The `gnome-extension/` directory is removed entirely. AppIndicator + XDG Portal hotkey cover all its functionality universally.

## 7. Packaging

### Two .deb packages

| Package | Contents | Architecture |
|---------|----------|-------------|
| `whisper-aloud` | App code, GUI, daemon, desktop/D-Bus/systemd files, icons, AppStream | `all` |
| `whisper-aloud-engine-ctranslate2` | Vendored venv: faster-whisper, ctranslate2, tokenizers, sounddevice | `amd64` |

### Unified identifiers
All IDs use `org.fede.whisperaloud` (lowercase, consistent).

### File installation paths
```
/usr/lib/python3/dist-packages/whisper_aloud/       ← Python package
/usr/lib/whisper-aloud/run                           ← shell wrapper (prefers venv)
/usr/lib/whisper-aloud/run-gui                       ← GUI wrapper
/usr/lib/whisper-aloud/venv/                         ← engine venv (from engine pkg)
/usr/bin/whisper-aloud → /usr/lib/whisper-aloud/run  ← symlink
/usr/bin/whisper-aloud-gui → run-gui                 ← symlink
/usr/share/applications/org.fede.whisperaloud.desktop
/usr/share/icons/hicolor/scalable/apps/org.fede.whisperaloud.svg
/usr/share/dbus-1/services/org.fede.whisperaloud.service
/usr/share/dbus-1/interfaces/org.fede.whisperaloud.xml
/usr/share/metainfo/org.fede.whisperaloud.metainfo.xml
/lib/systemd/user/whisper-aloud.service
```

### Dependencies
```
whisper-aloud Depends:
  python3 (>= 3.10), python3-gi, python3-gi-cairo,
  gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-ayatanaappindicator3-0.1,
  gir1.2-gsound-1.0, libportaudio2, python3-numpy,
  dbus-user-session

whisper-aloud Recommends:
  xdg-desktop-portal, xdg-desktop-portal-gtk,
  wl-clipboard, gnome-shell-extension-appindicator

whisper-aloud Suggests:
  python3-noisereduce, ydotool
```

### Build tool
`debhelper + dh-python` (official Debian tooling). Engine venv built at package build time with vendored wheels (no network in debian/rules).

### Desktop file with Actions
```ini
[Desktop Entry]
Name=WhisperAloud
Comment=Offline voice transcription
Exec=/usr/bin/whisper-aloud-gui
TryExec=whisper-aloud-gui
Icon=org.fede.whisperaloud
Type=Application
Categories=AudioVideo;Audio;Utility;GTK;

[Desktop Action Toggle]
Name=Toggle Recording
Exec=/usr/bin/whisper-aloud toggle

[Desktop Action Cancel]
Name=Cancel Recording
Exec=/usr/bin/whisper-aloud cancel
```

### Rollback
If the engine venv is too complex for first release, ship only `whisper-aloud` + bootstrap script that installs engine to `~/.local/share/whisper-aloud/venv/`.

## 8. D-Bus API Contract

**Bus name:** `org.fede.whisperaloud`
**Object path:** `/org/fede/whisperaloud`
**Interface:** `org.fede.whisperaloud.Control`

### Methods

| Method | In | Out | Description |
|--------|----|----|-------------|
| `StartRecording` | — | `b success` | Start recording |
| `StopRecording` | — | `s text` | Stop and transcribe, returns text |
| `ToggleRecording` | — | `s state` | Toggle, returns "recording"/"idle" |
| `CancelRecording` | — | `b success` | Abort without transcribing |
| `GetStatus` | — | `a{sv}` | state, version, model, device, uptime, hotkey_backend |
| `GetHistory` | `u limit` | `aa{sv}` | Array of entries: id, text, timestamp, duration, language |
| `GetConfig` | — | `a{sv}` | Current config as dict |
| `SetConfig` | `a{sv} changes` | `b success` | Apply config changes |
| `ReloadConfig` | — | `b success` | Reload from file |
| `Quit` | — | `b success` | Clean shutdown |

### Signals

| Signal | Args | When |
|--------|------|------|
| `RecordingStarted` | — | Recording begins |
| `RecordingStopped` | — | Recording ends (before transcription) |
| `TranscriptionReady` | `s text, a{sv} meta` | Transcription complete. Meta: duration, language, confidence, history_id |
| `LevelUpdate` | `d level` | Audio level 0.0-1.0, throttled to max 10Hz |
| `StatusChanged` | `s state` | "idle", "recording", "transcribing", "error" |
| `ConfigChanged` | `a{sv} changes` | Changed config keys |
| `Error` | `s code, s message` | Error codes: no_microphone, model_load_failed, model_not_found, transcription_failed, recording_in_progress, not_recording, config_invalid, permission_denied |

### Implementation notes
- `LevelUpdate` throttled: audio callback at ~100Hz, signal emitted at max 10Hz (peak of last batch)
- `StopRecording` is sync on D-Bus (blocks until transcription done). GUI uses async pattern: call stop, wait for `TranscriptionReady` signal
- Introspection XML shipped at `/usr/share/dbus-1/interfaces/org.fede.whisperaloud.xml`

## Audit Trail

- **Architecture audit:** ia-bridge session `20260219-170309` — confirmed Daemon-First (Option A), rejected GUI fallback (Option C)
- **Hotkey+Tray+Systemd audit:** ia-bridge session `20260219-172502` — identified GNOME 43 portal gap, added 3-level hotkey fallback, GTK3 tray note, SIGTERM handler
- **Packaging audit:** ia-bridge session `20260219-181446` — split into 2 packages, unified IDs, debhelper+dh-python, vendored engine venv, added dbus-user-session dep
