# Runtime-Assumption Map — WhisperAloud (2026-10-04)

**Directive:** behavior must be resolved at runtime from events or observed conditions — never from fixed waits, magic constants, retry budgets, or assumed success. This is a MAP, not a fix list.

## Summary (counts by category × severity)

| Category | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|
| A. Fixed waits | 1 | 2 | 6 | 9 |
| B. Retry budgets | 0 | 0 | 2 | 2 |
| C. Fire-and-forget | 1 | 1 | 1 | 3 |
| D. Ordering assumptions | 1 | 1 | 0 | 2 |
| E. Magic constants (hidden) | 0 | 1 | 0 | 1 |
| F. Env detection by guess | 0 | 2 | 2 | 4 |
| G. Swallowed failures | 0 | 1 | 2 | 3 |
| **Total** | **3** | **8** | **13** | **24** |

Note: most of the GlobalShortcuts/RemoteDesktop portal code (`service/hotkey_portal.py`, `clipboard/portal_paste_simulator.py`) and the daemon's idle_add/connect-retry logic were *already remediated* by today's merged fixes and are listed below as "resolved, verify-only" rather than findings.

## Findings by subsystem

### Clipboard / paste (daemon ↔ clipboard_manager ↔ paste_simulator)

| Site | Cat | Assumption | Failure scenario | Event that could replace it | Severity |
|---|---|---|---|---|---|
| `clipboard/clipboard_manager.py:95-113` `_copy_wayland` | C | `wl-copy --paste-once` is Popen'd and stdin closed; function returns `True` immediately, before `wl-copy` has registered itself as clipboard owner with the compositor. Already flagged as known/in-flight (branch `fix/copy-paste-order` exists but has no commits yet — diff vs master is empty). | Caller (`daemon.py`'s `_emit_success`) treats `copy()==True` as "clipboard now holds the text" and a sibling thread immediately activates a window + sleeps `paste_delay_ms` (default 100ms) + simulates Ctrl+V. If `wl-copy`'s fork/exec + Wayland registration takes longer than 100ms (cold process start under load, cgroup throttling, swapped-out pages), the paste delivers whatever was previously on the clipboard. | Wait for the Wayland `wl_data_source`/selection-owner handshake, or at minimum poll `wl-paste -n` until it echoes the new text (bounded), before reporting `copy()` as done. | **HIGH** (in-flight fix target) |
| `service/daemon.py:1821` `_do_paste` (inside `_emit_success`'s auto-paste branch) | A/D | After `window_manager.activate_window(captured)`, sleeps `paste_delay_ms/1000.0` (config, default 0.1s) then calls `simulator.simulate_paste()` — assumes the window manager has actually raised/focused the window (and that `_copy_wayland`'s clipboard registration, above, has also landed) within that fixed window. | Slow compositor/animation (fade-in, workspace switch) takes >100ms to actually deliver focus; paste keystroke goes to the wrong (previously focused) window, or is dropped. Compounds with the `_copy_wayland` race above — two independent fixed-time assumptions stacked. | `WindowManager.activate_window` has no "now focused" signal to observe (xdotool/gnome-extension backends are fire-and-forget too, see below) — ideally it would return/await a window-manager "focus-changed" event (e.g. GNOME extension D-Bus call could block until its own activate completes and poll `GetFocusedWindow` to confirm). | **HIGH** (in-flight fix target, same ticket as above) |
| `clipboard/window_manager.py` `_activate_gnome`/`_activate_xdotool` (not shown above but same pattern as `_check_xdotool`) | C | `activate_window()` returns a bool from the D-Bus call / subprocess exit code, not from any later confirmation that focus actually changed. | Caller's 100ms sleep (above) is the *only* thing standing between "activate command issued" and "paste sent" — if activation is itself async on the WM side, nothing observes completion. | Re-query `GetFocusedWindow`/`xdotool getactivewindow` after activation and compare window id before proceeding, instead of sleeping a fixed amount. | **MEDIUM** |
| `clipboard/clipboard_manager.py:134-144` `_copy_x11` (xclip) | A | Legitimate: `subprocess.run(..., timeout=config.timeout_seconds)` is a *bounded guard* on a local CLI tool call, not a settle delay — xclip either completes or the timeout fires and is handled as a real failure (falls back to file). | N/A | N/A | **LOW — legitimate as-is** (bounded local-tool guard, not a settle wait) |
| `clipboard/paste_simulator.py:53-56` `simulate_paste` | A | `time.sleep(paste_delay_ms/1000.0)` *before* calling ydotool/xdotool — a configurable, user-tunable pre-paste delay (documented in `ClipboardConfig.paste_delay_ms`, env-overridable `WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS`). This is the same delay reused in `daemon.py:1821` for window-activation settling (see above) — two different purposes sharing one knob. | If a user sets this to 0 to reduce input lag, it also (inadvertently, via the daemon.py reuse) removes the window-focus settle margin. | Split into two separate config knobs: one documented as "time to wait after copy for clipboard ownership" (ide­ally event-driven per `_copy_wayland` finding above) and one for "time after window activation before sending keys" (ditto). | **MEDIUM** (coupling of two unrelated assumptions under one tunable, documented as a config value so technically "legitimate" but hides the real race) |
| `clipboard/paste_simulator.py:76-101,111-132` ydotool/xdotool calls | A | Legitimate: `subprocess.run(..., timeout=config.timeout_seconds)`, same as xclip — bounded local-tool guard with real failure handling (returns False, logged), not a hidden assumption. | N/A | N/A | **LOW — legitimate** |
| `clipboard/paste_simulator.py:148-224` `check_availability`/`_check_ydotool_permissions` | A/F | `subprocess.run([...], timeout=2)` checks for `which ydotool`/`systemctl --user is-active ydotool.service` — these are one-shot capability probes used only for the GUI's "paste unavailable" diagnostic, not gating the actual paste path. Legitimate bounded probe. | N/A | N/A | **LOW — legitimate** |
| `clipboard/window_manager.py:33-37,43-77` `_detect_backend` | F | Backend (`gnome-extension` / `xdotool` / `none`) is probed once at `WindowManager.__init__` and cached for the daemon's entire lifetime. | User enables the GNOME "whisperaloud" shell extension (or installs xdotool) *after* the daemon has started — `paste_to_captured_window` silently stays on `"none"`/`xdotool` until the daemon is restarted, with no re-probe. | Re-probe lazily on each `get_active_window()`/`activate_window()` call (or at least on a `ShortcutsChanged`-style D-Bus name-owner-changed watch for the extension's bus name) instead of caching forever. | **MEDIUM** |
| `clipboard/clipboard_manager.py:189-201` `check_paste_permissions` | — | Delegates to `build_paste_simulator(...).check_availability()` — fine, no finding. | | | |
| `clipboard/clipboard_manager.py` `detect_session_type` (static, called once in `__init__`) | F | Wayland/X11 session type decided once at construction via env vars + `XDG_RUNTIME_DIR` glob fallback; cached in `self._session_type` for the object's lifetime. | A session type genuinely cannot change mid-run on a real desktop, so this is effectively safe — included for completeness, not a bug. | N/A | **LOW — legitimate** (session type is immutable for the life of a login session) |

### Portal (hotkey / paste) — already fixed today, verified resolved

| Site | Note |
|---|---|
| `service/hotkey_portal.py:_request_async`, `clipboard/portal_paste_simulator.py:_request_async` | Fully async Request/Response via `Gio.DBusProxy.call()` + signal subscription; the only `GLib.timeout_add` (`_SLOW_RESPONSE_WARNING_MS = 8000`, both files) is an explicit **diagnostic-only watchdog** that logs a WARNING and does **not** remove the subscription or give up — verified by reading `_request_async`'s `_warn()` closure in both files: it never calls `_finish()`. This is the correct "never abandon the request" pattern the maintainer directive asks for. **LOW — legitimate, by design, already fixed.** |
| `clipboard/portal_paste_simulator.py` session-ready flag (`_session_path` only set after `Start`'s Response, not after `CreateSession`'s) | Previously a real bug (ordering assumption, category D) — module docstring documents the fix and a regression test (`tests/test_portal_paste_integration.py`) is referenced. **Resolved — verify-only.** |
| `service/daemon.py:177` `_call_once_idle` | Previously a real bug (GLib treating a D-Bus method's return value as `SOURCE_CONTINUE`) — now wraps every idle_add/timeout_add hotkey/tray callback to always return `False`. **Resolved — verify-only.** Confirmed via `grep` that `ToggleRecording`/`Quit` callers (not shown in excerpts above, but referenced in the docstring and by the merge commit `5a9c711`) are not spot-checked beyond this; worth a follow-up grep for any *other* idle_add call that still passes a raw bound method without `_call_once_idle` — I did not find one in `daemon.py`, `indicator.py`, or `hotkey.py`. |

### Daemon — recording flow / shutdown

| Site | Cat | Assumption | Failure scenario | Event that could replace it | Severity |
|---|---|---|---|---|---|
| `service/daemon.py:692` `StartRecording`, `time.sleep(rf.pre_pause_delay_ms/1000.0)` after `self._mpris.pause_all_playing()` | A/D | Assumes that issuing MPRIS `Pause()` (a synchronous D-Bus call via pydbus) means the player's audio output has actually gone silent by the time the configured `pre_pause_delay_ms` elapses. **Default is `0`** (`RecordingFlowConfig.pre_pause_delay_ms: int = 0`), so by default there is *no* wait at all — recording starts the instant `Pause()`'s D-Bus call returns, which (for PipeWire/PulseAudio-backed players) can precede the actual stream teardown by tens to hundreds of ms. | Recording picks up a tail of media audio (music/video) that bleeds into the transcript, even though `pause_media=True` is supposed to prevent exactly this. | Subscribe to the player's `org.freedesktop.DBus.Properties.PropertiesChanged` for `PlaybackStatus` and wait for it to actually report `"Paused"` before returning from `pause_all_playing()`, instead of a fixed delay (which is 0 by default and a blind guess otherwise). | **MEDIUM** (real, but mitigated by `can_pause`-only targeting and the fact only mic input is captured, not system audio by default) |
| `service/media_control.py:86-105` `MprisController.resume_ours(delay_ms)` | A/D | Same pattern in reverse: sleeps `post_resume_delay_ms` (default 0) then checks `PlaybackStatus == "Paused"` before resuming — at least this one re-checks the actual property after the delay rather than assuming blindly, so it degrades gracefully (skips resuming players the user already resumed), but the *timing* of when to check is still a fixed sleep rather than a property-changed subscription. | Low risk: worst case is a resume decision based on a slightly stale `PlaybackStatus` read. | Subscribe to `PropertiesChanged` instead of `sleep`-then-poll. | **LOW** (self-correcting via the re-check; not a hidden assumption, just not event-driven) |
| `service/daemon.py:1876` `download_thread.join(timeout=10.0)` in `_cleanup()` | A | Explicitly documented bounded guard: if the model-download thread doesn't exit within 10s of `Quit()` cancelling it, shutdown proceeds anyway (thread is `daemon=True`). | None — this is correct degrade-and-proceed behavior, documented in the comment at `daemon.py:1868-1875` referencing WHISP-68 audit HIGH #3. | N/A | **LOW — legitimate**, explicitly justified in-code |
| `audio/recorder.py:395` `session.thread.join(timeout=self.chunk_worker_join_timeout_s)` | A | Same pattern: bounded join, documented "zombie thread" handling via `session.stopped` event, a monkeypatchable constant for tests. | None — correct degrade-and-proceed, with a `stopped` Event preventing the lingering thread from calling the hook again. | N/A | **LOW — legitimate** |
| `audio/recorder.py:123` `self._thread.join(timeout=1.0)`, `_FakeInputStream.close()` | A | Test-fixture-only code path (`_FakeInputStream`), not production. | N/A | N/A | **LOW — legitimate (test fixture)** |
| `service/daemon.py:1427,1447` `GLib.timeout_add(100, ...)` level meter, `PARTIAL_TRANSCRIPT_INTERVAL_MS` partial-transcript poll | A | Fixed-interval UI/telemetry polling (10Hz level meter, partial-transcript poll) — classic periodic-meter pattern, not a deadline/settle-wait. | N/A | N/A | **LOW — legitimate** (UI animation/meter tick) |
| `scripts/install-daemon.sh:18` `sleep 2` before probing D-Bus with `gdbus call ... Ping` | A | Fixed 2s wait after `systemctl --user enable --now`, then a one-shot probe; on failure it only prints a warning (does not fail the script), so it's advisory, not a hard gate — but it can produce a false "⚠ D-Bus probe failed" message on a slow machine/cold model-cache even though the daemon is fine. | Confusing install-time warning on slower hardware (not a correctness bug, since nothing downstream depends on the probe's result). | `gdbus wait --session --activate org.fede.whisperaloud` (blocks until the name appears, bounded by its own internal timeout) or a short polling loop (`for i in 1..10; do gdbus call ...; done`) instead of one fixed sleep + one fixed probe. | **LOW-MEDIUM** (only affects install-time UX, not daemon correctness) |

### Hotkey (local, non-portal) — libkeybinder3 / env-based backend selection

| Site | Cat | Assumption | Failure scenario | Event that could replace it | Severity |
|---|---|---|---|---|---|
| `service/hotkey.py:_is_wayland()` | F | Session type decided by `XDG_SESSION_TYPE`/`WAYLAND_DISPLAY` env vars, used as a *pre-filter* gating whether the portal backend is even attempted (`_try_import_portal`) — but the actual portal presence is then verified with a real D-Bus probe (`PortalHotkeys.portal_available()`), so the env check only ever causes a false negative (skip a potentially-working portal on a misreported env), never a false positive. | A niche XWayland/nested-compositor setup with `XDG_SESSION_TYPE=x11` but a working `GlobalShortcuts` portal would be skipped in favor of libkeybinder3 even though the portal path is preferred/more correct. | Just try `portal_available()` unconditionally (drop the `_is_wayland()` gate) — the real D-Bus probe already exists and is authoritative; the env check adds nothing but a potential false negative. | **LOW** (mitigated by the real probe underneath; worth simplifying but not a live bug) |
| `service/hotkey.py.detect_backend()` | F | Backend chosen once at `HotkeyManager.__init__` (daemon startup) and never re-evaluated. | Same class of issue as `WindowManager._detect_backend` above: if the compositor/portal implementation becomes available mid-session (e.g. GNOME Shell extension enabled, or portal service installed later), the daemon keeps using whatever it detected at startup until restarted. | Re-probe on hotkey-registration failure, or watch the portal bus name via `NameOwnerChanged`. | **MEDIUM** |

### Tray / indicator

| Site | Cat | Assumption | Failure scenario | Severity |
|---|---|---|---|---|
| `service/indicator.py:24-42` `_is_gnome_no_appindicator()` | F/G | `subprocess.run(["gsettings", "get", ...], timeout=3)` wrapped in broad `except Exception: return False` — a gsettings failure (schema missing, dbus hiccup) is silently treated as "extension IS installed" (no nudge shown), the opposite assumption of "err on the side of nagging." | On a GNOME system where `gsettings` genuinely fails transiently, the user gets no explanation for a missing tray icon and no retry. | Low real-world impact (gsettings essentially never fails on a live GNOME session); still an assumption-by-exception-swallowing. | **LOW** |
| `service/indicator.py:53-62` `_show_gnome_tray_nudge()` `subprocess.Popen([...notify-send...])`, bare `except: pass` | C | Fire-and-forget notification; never checks the child exited 0 or that `notify-send` even exists beyond catching `Exception`. | `notify-send` missing/fails → the one-time nudge is silently lost, user never learns why the tray icon is absent. Purely cosmetic (the comment says "degrade silently"), explicitly intended behavior. | **LOW — legitimate**, deliberate degrade documented in the comment | |

### GUI daemon-connection lifecycle — already fixed, verified resolved

| Site | Note |
|---|---|
| `ui/daemon_connect_logic.py`, `service/client.py:_is_starting_error`/`connect_with_activation_wait` | Pure, well-tested backoff/retry decision logic (`decide_after_attempt`, `backoff_delay_seconds`) distinguishes "daemon genuinely not there" (bounded `DEFAULT_MAX_AUTO_RETRIES=3` exponential backoff, capped `DEFAULT_MAX_DELAY_SECONDS=8.0`) from "daemon mid-D-Bus-activation" (`DEFAULT_STARTING_MAX_AUTO_RETRIES=None` — unlimited retries while `is_starting` stays true, driven by the real `"does not export any interfaces"` pydbus error, not a guess). This is a textbook example of the directive done right: retries are bounded only where "genuinely absent" is the correct terminal state, and unbounded (event-gated by `is_starting`) where activation could still be in progress. **LOW — legitimate, explicitly reasoned in the module's own docstring (lines 1-40).** |
| `service/client.py:150-166` `connect_with_activation_wait` | `sleep`/`clock` are injectable (tests don't need a real wall clock); the loop condition is `is_starting AND before deadline`, i.e. it stops retrying the instant the *observed* condition (no-longer-starting) changes, not only on the deadline. Legitimate bounded total budget (CLI needs a yes/no answer) layered on top of an event-driven inner condition. **LOW — legitimate.** |

### STT / model loading

| Site | Cat | Assumption | Note | Severity |
|---|---|---|---|---|
| `transcriber.py:_load_model`/`_try_load_model`, `stt/faster_whisper_engine.py:capabilities.devices=["cpu","cuda"]` | F | GPU/CUDA availability is **not** guessed from env/file presence — `device="auto"`/`"cuda"` is resolved by actually attempting `WhisperModel(device=...)` and catching the real load error (`_is_cuda_library_error`), falling back to CPU only on an observed failure. This is the correct pattern (probe by doing, not by guessing). | N/A | **LOW — legitimate**, no finding |

### Packaging / data files

| Site | Cat | Assumption | Note | Severity |
|---|---|---|---|---|
| `data/whisper-aloud.service` `SuccessExitStatus=3`/`RestartPreventExitStatus=3` | B | Documented, bounded: exit code 3 means "another instance already owns the bus name," treated as clean stop rather than retried forever — a real race-outcome signal (`EXIT_ALREADY_RUNNING`), not a blind retry budget. | N/A | **LOW — legitimate** |
| `data/whisper-aloud.service` `StartLimitIntervalSec=60`/`StartLimitBurst=5` | B | Standard systemd restart-storm guard, explicitly commented as such. | N/A | **LOW — legitimate** |
| `data/org.fede.whisperaloud.service` `SystemdService=`/`Exec=` dual activation | — | Not an assumption — documented fallback across systemd/non-systemd setups, verified against `docs/dbus-activation.md` per the comment. | N/A | — |

## Recommended fix order (HIGH first, grouped into independently dispatchable tickets)

1. **TICKET: Copy→paste ordering (clipboard race), severity HIGH** — covers both:
   - `clipboard/clipboard_manager.py:95-113` (`_copy_wayland` returns before `wl-copy` has clipboard ownership)
   - `service/daemon.py:1821` (`_do_paste`'s fixed `paste_delay_ms` sleep assumes both clipboard-ownership and window-activation have landed)
   This is already an open branch (`fix/copy-paste-order`, currently empty) — the map above gives the two concrete sites and the specific events (`wl-paste` echo / Wayland selection-owner signal; window-manager focus confirmation) that should replace the fixed sleeps.

2. **TICKET: Window-activation confirmation, severity MEDIUM, dispatchable independently of #1** —
   - `clipboard/window_manager.py` `_activate_gnome`/`_activate_xdotool` report success from the issuing call, not from confirmed focus change. Fixing this removes the need for part of #1's fixed delay.

3. **TICKET: MPRIS pause is not confirmed before recording starts, severity MEDIUM** —
   - `service/daemon.py:692` + `service/media_control.py:86-105` — replace fixed `pre_pause_delay_ms`/`post_resume_delay_ms` sleeps with `PropertiesChanged` subscriptions on `PlaybackStatus`. Independent of clipboard/paste work.

4. **TICKET: Stale capability-detection caches, severity MEDIUM, can be split into two independent small fixes** —
   - `clipboard/window_manager.py` `_detect_backend` (cached once at daemon startup)
   - `service/hotkey.py` `detect_backend` (cached once at daemon startup)
   Both could be re-probed on first failure or on a bus-name-owner-changed watch rather than requiring a daemon restart.

5. **TICKET: install-daemon.sh fixed-sleep-then-probe, severity LOW-MEDIUM, trivial, independent** —
   - `scripts/install-daemon.sh:18` — replace `sleep 2` + single probe with a short bounded polling loop or `gdbus wait --activate`.

6. **No ticket needed (verify-only, already resolved this cycle):** `service/hotkey_portal.py`, `clipboard/portal_paste_simulator.py` async Request/Response machinery; `service/daemon.py:177` `_call_once_idle`; `ui/daemon_connect_logic.py` + `service/client.py` connect/retry logic. All confirmed event-driven/correctly-bounded by reading the code, not speculation.

## Coverage gaps (orchestrator note)

This first pass was read-only and focused on clipboard/paste, portals,
daemon lifecycle and packaging. It did not exhaustively cover:

- **Audio pipeline** (`audio/recorder.py`, `audio/device_manager.py`,
  `audio/processing*`): device choice (`default`), fixed 16 kHz/mono,
  chunk sizes, NoiseGate/AGC/VAD thresholds. Field evidence: a 6 s
  recording reported "Recording complete: 1.45s (processed)", and a 7 s one
  0.24 s, inside the Flatpak. Needs its own audit.
- **STT chunking and model constants** (`stt/chunking.py`, engine
  parameters) and the GUI (`ui/`) beyond the idle/timeout callbacks.
- Category E (hidden magic constants) is counted once in the summary but
  has no explicit row above; a second pass should enumerate them.

A second audit pass covering these areas is recommended before 0.3.0 is
tagged.
