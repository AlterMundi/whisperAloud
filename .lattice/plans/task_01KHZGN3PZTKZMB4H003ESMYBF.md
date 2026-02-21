# WHISP-15 — Smart recording flow: MPRIS pause + hardware gain calibration + restore

## Goal

When the user hits Record:
1. Any playing media pauses (MPRIS)
2. Mic input gain raises to a calibrated level
3. Recording proceeds with clean, controlled gain
4. On Stop: mic gain restores to original value, media resumes

## Cost Breakdown

### Piece 1 — MPRIS pause/resume ✅ CHEAP (~2h)

**How:** D-Bus calls to `org.mpris.MediaPlayer2.Player.Pause` / `.Play` on all active players.

**Stack already present:** pydbus is a daemon dependency. MPRIS proxy detected on this system.

```python
# In daemon.py (pseudocode):
def _pause_media(self):
    bus = pydbus.SessionBus()
    for name in bus.list_names():
        if name.startswith("org.mpris.MediaPlayer2."):
            player = bus.get(name, "/org/mpris/MediaPlayer2")
            if player.PlaybackStatus == "Playing":
                player.Pause()
                self._paused_players.append(name)

def _resume_media(self):
    for name in self._paused_players:
        bus.get(name, "/org/mpris/MediaPlayer2").Play()
    self._paused_players.clear()
```

**Risk:** None. Standard D-Bus, zero new deps. Some players don't implement MPRIS (rare).

---

### Piece 2 — Save + raise + restore hardware mic gain ✅ CHEAP (~2h)

**How:** `wpctl` is installed (PipeWire native). `pactl`/pulsectl not needed.

```bash
# Save current gain
wpctl get-volume @DEFAULT_AUDIO_SOURCE@  # e.g. "Volume: 0.90"

# Raise to target (e.g. 1.0 = 100%)
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0

# After recording: restore
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.90
```

From Python: `subprocess.run(["wpctl", ...])` or parse wpctl output.

**Risk:** Low. wpctl is stable PipeWire API. Works on PulseAudio via PipeWire compat layer (this system has pw-cli). Fails gracefully if wpctl absent (skip silently).

---

### Piece 3 — Auto-calibration (pre-record level probe) ⚠️ MEDIUM (~1 day)

**Goal:** Instead of hardcoding "set to 100%", probe for the gain that puts speech at −18 dBFS.

**How:** Before recording, open a 0.5s capture burst, measure RMS, binary-search the wpctl volume knob until signal lands in target range.

**Problems:**
- Adds ~1-2s pre-recording delay (UX friction: user clicks Record, nothing happens visibly)
- Need user to actually speak during calibration (or use a fixed preset)
- Environment noise (fan, keyboard) can fool the probe
- Gain changes take a few frames to propagate through the audio stack

**Verdict:** Optional/phase-2. A simpler approach — **always raise to a fixed high level** (e.g. 90%) rather than auto-probing — covers 90% of users with zero delay. The post-processing AGC (already implemented) then normalizes residual level differences.

---

### Piece 4 — Orchestration in daemon state machine ✅ LOW (~2h)

Wire into existing `_start_recording()` / `_stop_recording()`:

```python
def _start_recording(self):
    self._pause_media()
    self._save_and_raise_mic_gain()
    self.recorder.start()

def _stop_recording(self):
    audio = self.recorder.stop()
    self._restore_mic_gain()
    self._resume_media()
    # transcribe ...
```

---

## Summary

| Piece | Cost | Risk | Deps |
|-------|------|------|------|
| MPRIS pause/resume | ~2h | Low | pydbus (already present) |
| wpctl gain save/raise/restore | ~2h | Low | wpctl (already installed) |
| Auto-calibration probe | ~1 day | Medium | same |
| Daemon orchestration | ~2h | Low | — |

**Recommended scope for v1:** Pieces 1 + 2 + 4 (no auto-calibration).
Total: ~6h, no new dependencies, graceful degradation if MPRIS/wpctl absent.

Auto-calibration can be a follow-up once the basic flow is proven.

## Acceptance Criteria

- [ ] Clicking Record pauses active MPRIS players
- [ ] Mic input gain raised to configured target (default 90%, configurable)
- [ ] Original gain restored on stop/cancel
- [ ] MPRIS players resumed on stop/cancel
- [ ] All existing tests still pass
- [ ] Graceful no-op if wpctl not present or no MPRIS players active
- [ ] New config knobs: `recording_raise_gain: bool`, `recording_target_gain: float`
- [ ] MPRIS and gain ops logged at DEBUG level

---

## ia-bridge Audit Results (2026-02-21)

### Architecture changes from audit

- **New module**: `src/whisper_aloud/service/media_control.py` (`MprisController` + `GainController`)
- **Per-session state object** in daemon to track paused players + gain snapshot → idempotent stop

### MPRIS refinements
- Check `CanPause` before calling `Pause()`
- Track `paused_by_us` set; resume only players still Paused
- Best-effort 2s timeout per player — never block recording start
- No default sleeps; optional `pre_pause_delay_ms`/`post_resume_delay_ms` (default 0)

### Gain control refinements
- Pin to concrete node ID at raise time via `wpctl inspect @DEFAULT_AUDIO_SOURCE@`
- `set_volume = max(current, target)` — never lower gain
- If muted: skip gain raise, log, do NOT unmute (privacy)
- Crash safety: persist `{node_id, volume}` to `/run/user/$UID/whisperaloud-gain.json` + `atexit`

### Config structure
```python
@dataclass
class RecordingFlowConfig:
    pause_media: bool = True
    raise_mic_gain: bool = False   # opt-in
    target_gain_linear: float = 0.85
    pre_pause_delay_ms: int = 0
    post_resume_delay_ms: int = 0
    gain_restore_on_crash: bool = True
```

### Deferred to v2
- MPRIS `PropertiesChanged` signal subscription (detect mid-session manual resume)
- Unmuting behaviour
- Per-call D-Bus timeouts (pydbus limitations)

### Medium-confidence risk
- `wpctl get-volume` output format across WirePlumber versions/locales — parse defensively
