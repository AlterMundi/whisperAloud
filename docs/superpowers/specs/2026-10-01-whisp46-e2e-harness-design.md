# WHISP-46 — E2E Harness Design (real daemon, fake audio, fake STT)

- **Lattice:** WHISP-46 (design), unblocks WHISP-47 (scenarios), WHISP-49 (CI job)
- **Date:** 2026-10-01
- **Status:** proposed design, pre-implementation — **this ticket makes no production or test
  code changes**; it is a spec + plan only.
- **Builds on:** WHISP-50 (STT catalog as single source of truth), WHISP-48 (D-Bus contract
  snapshot test), the `f839796` test-isolation conftest (private XDG dirs + poisoned
  `DBUS_SESSION_BUS_ADDRESS` so the unit suite never touches the developer's real session).

## 1. Purpose and scope

Today the daemon (`service/daemon.py`) is only exercised through heavily-mocked unit tests
(`tests/test_daemon.py` patches `SessionBus` itself; `tests/test_recording_flow_integration.py`
is an empty placeholder). Nothing starts the *real* `WhisperAloudService` process, publishes it
on a *real* D-Bus connection, and drives it from a *real* `WhisperAloudClient`/CLI. This design
defines the harness that will let WHISP-47 write such tests safely and deterministically:

- Real daemon subprocess, real pydbus publish/introspection, real `service/client.py`.
- A private, disposable D-Bus session bus — never the user's real
  `org.fede.whisperaloud` name, never their real `~/.config` or `~/.local/share`.
- Fake audio (a WAV file stands in for the microphone) and a fake, deterministic STT engine
  (no model download, no GPU/CPU inference cost) for the default profile; an opt-in
  `@pytest.mark.real_model` profile for the handful of tests that want the real model.

**Non-goals (this ticket):** writing the actual scenario tests (WHISP-47), wiring CI (WHISP-49),
and any change to daemon.py/config.py/stt/*.py source. Those are called out below as **required
production hooks** for the implementer of WHISP-47 to land, but are not implemented here.

## 2. Key finding from reading the code: two real hermeticity hazards

Reading `service/daemon.py`, `service/media_control.py`, `clipboard.py` and `config.py` surfaced
two hazards that are *not* hypothetical — they are reachable by simply constructing a
`WhisperAloudService()` with default config, which is exactly what an e2e daemon fixture does:

1. **`GainController.restore_from_crash_file()` runs unconditionally** in
   `WhisperAloudService._init_components()`, regardless of `recording_flow.raise_mic_gain`. It
   reads `/run/user/<uid>/whisperaloud-gain.json` — a path keyed by the **real** `os.getuid()`,
   not redirectable via `XDG_*`/`HOME` — and if present, shells out to the **real** `wpctl
   set-volume` against `@DEFAULT_AUDIO_SOURCE@`, i.e. the developer's real microphone. This file
   is also written by the user's real production daemon, so an e2e run can read *and* race with
   it. `recording_flow.raise_mic_gain = True` is additionally the **default**, so even without
   the crash-file path, `StartRecording()` would call `wpctl set-volume` for real unless
   overridden.
2. **`clipboard.auto_paste` and `clipboard.paste_to_captured_window` default to `True`.**
   `PasteSimulator` shells out to `ydotool`/`xdotool`/`wl-copy` depending on session type.
   `ydotool` in particular talks to `ydotoold` over a `uinput`-backed socket that is **not**
   scoped to `DISPLAY`/`WAYLAND_DISPLAY`/the D-Bus session — if `ydotoold` happens to be running
   on the host, a fake "transcription completed" could inject a real paste into whatever window
   has real focus on the developer's real desktop.

Both are addressed below under "Hermeticity." Neither requires new code *per se* — both have
existing `WHISPER_ALOUD_*` env overrides — except the crash-file restore, which has no override
today and is the one mandatory, minimal production hook this design requires.

## 3. Decision 1 — Process model

**Decision:** the **whole e2e pytest invocation** runs under a single `dbus-run-session`, and
each test spawns the daemon as a **subprocess** via `subprocess.Popen`, stopped through its own
`Quit` D-Bus method with `proc.terminate()`/`proc.kill()` as a fallback. No `dbus-daemon --fork`,
no manual PID bookkeeping, no `pkill`/`kill -f` anywhere in the harness.

```
dbus-run-session -- python -m pytest -m e2e tests/e2e
│
└── private bus B, address exported as $DBUS_SESSION_BUS_ADDRESS for the whole pytest process
    └── test function
        └── Popen([sys.executable, "-m", "whisper_aloud", "--daemon"], env={...inherits B...})
            └── publishes org.fede.whisperaloud on B (not the user's real bus)
        └── WhisperAloudClient() / raw pydbus proxy, also on B
        └── teardown: client.quit_daemon() → wait(timeout) → proc.terminate() → wait → proc.kill()
```

`dbus-run-session` starts a private bus, execs the given command with
`DBUS_SESSION_BUS_ADDRESS` pointed at it, and **tears the bus down itself when that command
exits** — there is never a bus PID for the harness to manage or kill. This is the same shape
already mandated by this ticket's safety rules, and it sidesteps the exact failure mode that
crashed a previous agent's desktop (manually forking/killing a session bus by pattern).

### Alternatives considered

- **One `dbus-run-session` per test** (`dbus-run-session -- python -m pytest tests/e2e/test_x.py::test_y`
  invoked as a subprocess *of* the outer pytest run). Rejected as the default: it means pytest
  cannot orchestrate fixtures/teardown across the boundary cleanly (the inner pytest is a whole
  separate process pytest has to shell out to and parse output from), and it multiplies
  bus-startup overhead by the number of tests. It is still available as a documented fallback
  for a single stubborn test that must not share a bus with its neighbors (see "Isolation
  granularity" below) — that test can be run with
  `dbus-run-session -- python -m pytest tests/e2e/test_x.py::test_y -m e2e` in CI as its own
  step, not as the suite's default shape.
- **`dbus-daemon --session --fork --print-address --print-pid`, manually killing the recorded
  PID in a fixture.** This is what the incident report says a previous run got wrong in the
  broader sense (even though that run used a pattern-kill, not a recorded PID). It is explicitly
  allowed by this run's SAFETY rules as a *last resort* probe technique, but `dbus-run-session`
  removes the need for any kill logic at all for the common case, so it is preferred everywhere
  it fits. It remains a documented option only for a scenario that needs the bus to outlive a
  single `dbus-run-session`-wrapped command (none identified in §7).
- **In-process daemon** (import `WhisperAloudService`, call `.run()` on a background thread
  inside the test process). Rejected: the whole point of WHISP-46 is to catch real pydbus
  publish/introspection/signal-wiring bugs and real process-lifecycle bugs (SIGTERM handling,
  startup races — see WHISP-63) that an in-process call hides. It also can't exercise "second
  instance already running" (the daemon needs to be a *different* OS process to meaningfully
  collide on the bus name).

### Isolation granularity: one bus for the whole run, one daemon subprocess per test

The bus is shared for the whole e2e pytest invocation (cheap, and bus identity isn't part of
what's under test); the **daemon process is function-scoped** — a fresh `Popen` per test,
stopped in teardown before the next test's daemon tries to own `org.fede.whisperaloud` on the
same bus. This trades a small amount of startup latency (model-free "fake" engine startup is
expected to be sub-second — see §8 runtime budget) for strong isolation: no test can leak
recorder/transcriber/history state into the next one, and a hung daemon in one test can't wedge
the rest of the file (it's `terminate()`-then-`kill()`-then-fail-loud in teardown, not shared).

### Stopping the daemon

1. Call `Quit()` over D-Bus (graceful: unregisters hotkeys, drains the executor, calls
   `transcriber.unload_model()`).
2. Wait on `proc.wait(timeout=5)`.
3. If still alive, `proc.terminate()` (SIGTERM — the daemon's own `GLib.unix_signal_add` handler
   runs the same graceful path) and wait again with a shorter timeout.
4. If still alive, `proc.kill()` and fail the test loudly (this indicates a real shutdown bug,
   not a harness bug — surface it, don't hide it).

This is "kill only a PID we started ourselves," per SAFETY — `proc` is the `Popen` object the
fixture itself created.

## 4. Decision 2 — Hermeticity: the `WHISPER_ALOUD_TEST_MODE` hook

**Decision:** one new env var, `WHISPER_ALOUD_TEST_MODE=1`, checked in exactly two places in
production code (both small, both additive, both no-ops when unset):

| Component | Without the hook | With `WHISPER_ALOUD_TEST_MODE=1` |
|---|---|---|
| `GainController.__post_init__` (`service/media_control.py`) | finds real `wpctl`, later calls `restore_from_crash_file()` against the real recovery file and real mic | forces `self._wpctl = None` unconditionally, before the `shutil.which("wpctl")` probe — every method already treats "no wpctl" as the disabled/no-op path, including `restore_from_crash_file()`'s `if not self._wpctl: return` guard. No new branches needed inside the class, just skip the probe. |
| `stt/catalog.py` module body | `_ENTRIES`/`ENGINES` are exactly the user-facing catalog | appends one hidden `ModelEntry(engine="fake", ...)` and `ENGINES["fake"] = "Fake (test only)"` **in-process, at import time** |

Everything else needed for hermeticity already has a `WHISPER_ALOUD_*` env override and needs
**no new code** — the harness just has to remember to set them when spawning the daemon:

| Risk | Existing override | Harness default |
|---|---|---|
| Real mic gain raise on `StartRecording` | `WHISPER_ALOUD_RAISE_MIC_GAIN` | `0` |
| Real MPRIS pause/resume (harmless on the private bus — no real player is on it — but set for determinism/clarity) | `WHISPER_ALOUD_PAUSE_MEDIA` | `0` |
| Real `ydotool`/`xdotool` paste | `WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE` | `0` |
| Real desktop notifications (harmless on the private bus — no notification daemon owns `org.freedesktop.Notifications` there — set anyway) | `WHISPER_ALOUD_NOTIFICATIONS_ENABLED` | `0` |
| Real history DB / audio archive under the developer's `~/.local/share` | `WHISPER_ALOUD_DB_PATH`, `WHISPER_ALOUD_AUDIO_ARCHIVE` | per-test temp dir |
| Tray indicator (AyatanaAppIndicator3/GTK3) | none needed — already gated on `DISPLAY`/`WAYLAND_DISPLAY` being set | unset both (as the private-bus env already does) |
| Hotkeys (portal/libkeybinder) | none needed — portal path requires `WAYLAND_DISPLAY`/`XDG_SESSION_TYPE=wayland` *and* the portal D-Bus name owned on the bus in use (never true on a fresh private bus); keybinder requires a usable X11 `DISPLAY` | unset `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_SESSION_TYPE` |

So the "minimal production hook" from the brief is genuinely minimal: **one env var, two
read-sites, both inert unless `WHISPER_ALOUD_TEST_MODE=1`.** Everything else is config, not code.

The harness's standard daemon-subprocess environment (the `daemon()` fixture builds this, see
§6) is therefore:

```
WHISPER_ALOUD_TEST_MODE=1
WHISPER_ALOUD_MODEL_ENGINE=fake
WHISPER_ALOUD_RAISE_MIC_GAIN=0
WHISPER_ALOUD_PAUSE_MEDIA=0
WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE=0
WHISPER_ALOUD_NOTIFICATIONS_ENABLED=0
WHISPER_ALOUD_DB_PATH=<tmp>/history.sqlite3
HOME=<tmp_home>              # XDG_* derive from this the same way conftest.py does today
XDG_CONFIG_HOME=<tmp_home>/config
XDG_DATA_HOME=<tmp_home>/data
XDG_CACHE_HOME=<tmp_home>/cache
XDG_STATE_HOME=<tmp_home>/state
DBUS_SESSION_BUS_ADDRESS=<inherited from dbus-run-session>
# DISPLAY / WAYLAND_DISPLAY / XDG_SESSION_TYPE intentionally absent
WHISPER_ALOUD_FAKE_AUDIO_WAV=<tmp>/fixtures/<name>.wav   # set per-test, see §5
```

plus whatever `config_overrides` the test passes (see `daemon(config_overrides=...)` in §6),
applied as additional `WHISPER_ALOUD_*` vars or a generated `config.json` under the temp
`XDG_CONFIG_HOME` — the fixture prefers env overrides where one already exists (simpler, no file
I/O) and falls back to writing `config.json` for fields that have no env override today.

### Conftest interaction (why `tests/e2e/` needs its own bus handling)

The root `conftest.py` unconditionally does
`os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/..."` at **import time**, to
keep the unit suite off the developer's real bus. If the *whole* pytest process is launched via
`dbus-run-session -- python -m pytest ...`, that line clobbers the perfectly good private-bus
address `dbus-run-session` just gave the process, before any test runs.

**Required production-adjacent change (test infra, in WHISP-47, not this ticket):** capture the
real value *before* overwriting it:

```python
# conftest.py, replacing the unconditional overwrite
_real_dbus_address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/whisper-aloud-tests-bus"
os.environ["_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS"] = _real_dbus_address or ""
```

`tests/e2e/conftest.py` restores it for the e2e fixtures only:

```python
os.environ["DBUS_SESSION_BUS_ADDRESS"] = os.environ["_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS"]
```

This keeps the unit suite's existing guarantee (import `conftest.py` anywhere, get poisoned
address by default) while letting `tests/e2e/` opt back into the one private bus that
`dbus-run-session` set up for this run. If the real address is empty (someone ran `pytest`
*without* `dbus-run-session`), `tests/e2e/conftest.py` should skip the whole e2e session with a
clear message rather than silently trying `nonexistent` — see `scripts/test_e2e.sh` in §8, which
is the only sanctioned entry point and always wraps pytest in `dbus-run-session`.

## 5. Decision 3 — Fake audio

**Decision:** inject at the `sounddevice.InputStream` boundary inside `AudioRecorder`, driven by
one env var, `WHISPER_ALOUD_FAKE_AUDIO_WAV=<path>` (only honored when
`WHISPER_ALOUD_TEST_MODE=1`, defense in depth). When set, `AudioRecorder.start()`:

- skips `DeviceManager.validate_device(...)` entirely (no real PortAudio device needs to exist —
  satisfies the SAFETY rule of no real microphone, and makes the harness portable to CI
  containers with no audio hardware at all),
- reads the WAV fully into memory (mono float32, resampled to `config.sample_rate` if the
  fixture's native rate differs — reuse `AudioProcessor.resample`/`stereo_to_mono`, already used
  by `AudioRecorder.stop()`, so fixture WAVs don't have to be authored at exactly 16 kHz mono),
- feeds it to the **existing** `self._audio_callback(indata, frames, time_info, status)` from a
  background thread, chunked at `config.chunk_duration` seconds per call (the same blocksize
  production uses), so the per-session `AudioPipeline`/`LevelMeter` inside `AudioRecorder` see
  the identical call shape they see from a real `sd.InputStream` — no special-casing downstream
  of the callback,
- pads with trailing silence rather than looping at EOF, so a test that wants to exercise
  `max_recording_duration` auto-stop against a short fixture file can still reach that duration
  deterministically (looping would make "how much silence was processed" depend on timing).

This is a **new, small internal class** (`_FakeInputStream` in `audio/recorder.py`, or a sibling
module `audio/fake_stream.py` — left to the implementer, no interface beyond
`.start()`/`.stop()`/`.close()` is needed since that's all `AudioRecorder` calls on `self._stream`
today), substituted for `sd.InputStream` only in the `WHISPER_ALOUD_FAKE_AUDIO_WAV` branch.
Production's default path (`sd.InputStream`) is untouched.

### Why not a virtual ALSA/PipeWire loopback device

An `snd-aloop` loopback or a PipeWire null-sink is heavier (kernel module or PipeWire session
state that may not exist in a minimal CI container), is multi-process by nature (something has
to *play* the WAV into the loopback while the recorder captures from the other end, racing on
timing), and does not let a test assert "this exact chunking/padding happened" the way an
in-process fake stream does. It's a legitimate idea for a *later*, more hardware-realistic test
tier, but is unnecessary for WHISP-47's scenarios and adds real CI fragility for no benefit here.

### Respecting the per-session `AudioPipeline`

Nothing about this seam touches `AudioPipeline`/`NoiseGate`/`AGC` — they are constructed fresh in
`AudioRecorder.__init__` (one instance per `AudioRecorder`, and the daemon only ever constructs
one `AudioRecorder` per config lifetime — see `_apply_config_changes`), and `stop()` already
calls `self._pipeline.process(audio, sample_rate=16000)` on whatever accumulated in `_frames`,
regardless of how those frames got there. The fake stream's only job is to populate `_frames` via
the real callback, so the gate/AGC/denoise/limiter chain runs exactly as it does in production.
Tests that want to assert "the pipeline didn't mangle my deterministic fixture" should disable
the processing stages via `AudioProcessingConfig` overrides (already configurable, no new code)
rather than fighting the pipeline.

## 6. Decision 4 — Fake STT engine

**Decision:** a new `FakeSpeechEngine` (`src/whisper_aloud/stt/fake_engine.py`) implementing the
`SpeechEngine` Protocol from `stt/engine.py`, registered as `engine == "fake"` in
`create_engine()`, and **registered in the catalog only when `WHISPER_ALOUD_TEST_MODE=1`** (see
§4's second hook) so it can never appear in `whisper-aloud models`, the GUI engine picker, or
`config.validate()`'s error-message "Valid: ..." list for a normal user install.

```python
# stt/catalog.py — only at import time, only in test mode
if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
    _ENTRIES = _ENTRIES + (
        ModelEntry(
            engine="fake", name="fake", label="Fake (test only)",
            size_mb=0, languages=None, streaming=False, license="N/A",
            devices=("cpu",), tier="fast", recommended=False,
            notes="Deterministic test double; never shown to real users.",
        ),
    )
    ENGINES["fake"] = "Fake (test only)"
```

Because registration is conditional on the env var rather than on a `hidden` flag checked by
`entries()`/`engines()`/`models_for()`, a production install — where `WHISPER_ALOUD_TEST_MODE` is
never set — never has `"fake"` in `ENGINE_MODELS` at all: `config.validate()` rejects it exactly
like any other unknown engine, even if an attacker-controlled `config.json` sets
`model.engine = "fake"`. (A `hidden: bool` field on `ModelEntry`, filtered by `entries()`/
`engines()`/`models_for()` but *not* by `engine_models()`, was considered and rejected — it would
mean `"fake"` validates successfully even outside test mode, which is a strictly worse default.)

### Determinism: sidecar `.txt`, not config plumbing

`FakeSpeechEngine.transcribe_numpy(audio, sample_rate=16000, **kwargs)` reads
`os.environ["WHISPER_ALOUD_FAKE_AUDIO_WAV"]` (the **same** env var the recorder used — read
directly, not threaded through the numpy call or through `SetConfig`) and returns the contents
of `Path(wav_path).with_suffix(".txt")` as `TranscriptionResult.text`. This was chosen over two
alternatives:

- **Hashing the audio content** to look up an expected transcript in a manifest: rejected
  because `AudioRecorder.stop()` runs the audio through resampling, VAD trim, and the processing
  pipeline before the engine ever sees it, so the bytes the engine receives are not stable
  against pipeline/config changes — a content hash would silently stop matching whenever a test
  tweaks `AudioProcessingConfig`.
- **Threading a path through `transcribe_numpy(..., audio_source_path=...)` via `SetConfig`
  round-trips, with the daemon refreshing `self.transcriber.config` on every config change**:
  works, but only by also committing to a `_apply_config_changes` behavior change and new
  `to_dict()`/`from_dict()`/`GetConfig()` fields (today's `to_dict()` is a hand-maintained
  explicit dict, not a generic dataclass dump — adding fields there means they also appear over
  `GetConfig()` to every client, which is more test-shaped surface in the production D-Bus
  contract than this ticket wants). Reading the **same env var already used for the audio
  fixture** needs zero `SetConfig`/`GetConfig`/`to_dict` changes and zero cross-object staleness,
  at the cost of requiring a fresh daemon subprocess per fixture (already the default — see §3's
  isolation granularity, chosen independently of this constraint but now also load-bearing for
  it).

If no `WHISPER_ALOUD_FAKE_AUDIO_WAV` sidecar `.txt` exists, `FakeSpeechEngine` raises
(`ConfigurationError`-style, loud) rather than silently returning `""` — a missing sidecar is a
fixture-authoring bug the test should fail on, not a false "empty transcript" the assertion might
misattribute to recording/pipeline behavior.

### Latency and error injection

Two more env vars, read at call time (so no construction-time caching issue, and consistent with
the "fresh daemon process per fixture" model):

- `WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S=<float>` — `transcribe_numpy` sleeps this long before
  returning, letting tests assert the `transcribing` → signal-emission ordering under a
  controlled delay instead of a race against real model inference time.
- `WHISPER_ALOUD_FAKE_ENGINE_ERROR=<message>` — `transcribe_numpy` raises
  `TranscriptionError(message)` instead of returning a result, exercising the daemon's
  `_transcribe_and_emit` error path (`Error` signal, `StatusChanged("idle")`, no history write)
  without needing a real model failure mode.

### `@pytest.mark.real_model`

A new pytest marker, registered in `pyproject.toml`'s `markers` alongside the existing
`requires_audio_hw`/`requires_display`/`requires_dbus`. `tests/e2e/conftest.py` adds a
`pytest_collection_modifyitems` hook (or an autouse fixture — implementer's choice) that skips
any `real_model`-marked item unless the configured model is already cached — reusing the cache
probe the codebase already needs for `stt/model_download.py` (HF cache dir for faster-whisper,
sherpa-onnx cache dir for parakeet) rather than inventing a new detection mechanism. These tests
never run in the default `-m e2e` CI profile; they are for local runs where the developer has
already downloaded a model, or a dedicated nightly job (out of scope here — WHISP-49 decides CI
wiring).

## 7. Decision 5 — Fixtures API

```python
# tests/e2e/conftest.py

@dataclasses.dataclass
class DaemonHandle:
    proc: subprocess.Popen
    xdg_home: Path
    log_path: Path          # combined stdout+stderr of the daemon subprocess
    bus_name: str = "org.fede.whisperaloud"

    def client(self) -> WhisperAloudClient: ...
    def stop(self, timeout: float = 5.0) -> None: ...  # Quit() → terminate() → kill()


@pytest.fixture
def daemon(tmp_path, request) -> Callable[..., DaemonHandle]:
    """Factory fixture: call to spawn the real daemon as a subprocess on this
    session's private bus. Returns a DaemonHandle; teardown (stop()) is
    registered automatically via request.addfinalizer.

    config_overrides: WHISPER_ALOUD_* env var names (without prefix stripped,
    i.e. pass e.g. {"RAISE_MIC_GAIN": "0"}) layered on top of the harness
    defaults from §4; or a dict written to a temp config.json for fields with
    no env override.
    """
    def _start(
        config_overrides: dict[str, str] | None = None,
        *,
        fake_audio: str | None = None,       # fixture name, resolved via audio_fixture()
        ready_timeout: float = 5.0,
    ) -> DaemonHandle: ...
    return _start


@pytest.fixture
def client(daemon) -> Iterator[WhisperAloudClient]:
    """Convenience: start a daemon with harness defaults and return a connected
    client. Most scenario tests want this; tests that need custom
    config_overrides call `daemon(...)` directly instead."""
    handle = daemon()
    c = handle.client()
    yield c
    c.disconnect()


@dataclasses.dataclass
class AudioFixture:
    name: str
    wav_path: Path
    transcript_path: Path
    expected_text: str


@pytest.fixture
def audio_fixture(request) -> Callable[[str], AudioFixture]:
    """Resolve a fixture name to its WAV + sidecar .txt under
    tests/e2e/fixtures/audio/. Fails loudly if the sidecar is missing."""
    def _get(name: str) -> AudioFixture: ...
    return _get


class SignalCapture:
    """Subscribes to one D-Bus signal on a proxy and lets a test block until
    it fires, with a timeout and an optional predicate on the signal args."""

    def __init__(self, proxy: Any, signal_name: str): ...

    def wait(
        self,
        timeout: float = 5.0,
        predicate: Callable[..., bool] | None = None,
    ) -> tuple: ...
    """Pumps the GLib main context until the signal fires (and predicate(*args)
    is True, if given) or timeout elapses. Raises AssertionError with the
    signal name + timeout + a tail of the daemon log on timeout (see below)."""


@pytest.fixture
def signal_capture(client) -> Callable[[str], SignalCapture]:
    def _capture(signal_name: str) -> SignalCapture: ...
    return _capture


@pytest.fixture(autouse=True)
def _attach_daemon_log_on_failure(request) -> Iterator[None]:
    """On test failure, read back the DaemonHandle(s) created during this test
    (tracked via a module-level registry populated by the `daemon` fixture)
    and attach their log tails to the failure via
    `request.node.add_report_section` / pytest-html style, or simply print()
    under `-s`/`--capture=no` conviction: minimally, write
    `<log_path>` to the terminal reporter so `pytest -rA` surfaces it."""
    yield
    ...
```

Design notes:

- `daemon` is a **factory** fixture (`daemon()` to start, not injected pre-started) so tests that
  need non-default `config_overrides` or a pre-staged `fake_audio` fixture can do so before the
  process spawns — env vars must be set at `Popen` time, not patched into a running process.
- `client` is the common case built on top of `daemon()` with zero arguments, covering most of
  WHISP-47's scenario list.
- `audio_fixture()` returns both the resolved paths *and* the expected text (read from the
  sidecar once, at fixture-resolution time) so a test's assertion
  (`assert result_text == fixture.expected_text`) doesn't re-read the sidecar itself.
- `SignalCapture.wait()` pumps `GLib.MainContext.default().iteration(True)` in a loop bounded by
  the timeout — the same pattern the feasibility probe below validated — rather than
  `time.sleep()`-polling, so a signal that fires in 5 ms doesn't cost the test 100 ms.
- Daemon log capture on failure is a flakiness-control, not a nicety: when a signal-wait times
  out, "what was the daemon actually doing" is the first question, and re-running by hand to find
  out defeats CI.

## 8. Decision 5 continued — layout, markers, runtime budget, CI

- **Layout:** `tests/e2e/` package with its own `conftest.py` (bus restoration, `daemon`/
  `client`/`audio_fixture`/`signal_capture` fixtures, `real_model` skip logic) and
  `tests/e2e/fixtures/audio/*.wav` + matching `*.txt` sidecars (a handful of short, synthetic —
  e.g. `numpy`-generated sine/silence, not real speech — WAVs are enough; what's under test is
  daemon plumbing, not STT accuracy).
- **Marker:** `e2e`, added to `pyproject.toml`'s `markers` list next to the existing
  `requires_dbus` (which already exists for exactly this category but has no runner wired up
  today — `e2e` supersedes it for this suite; `requires_dbus` tests outside `tests/e2e/`, if any
  exist, are out of scope for this ticket). Also register `real_model`.
- **Entry point:** `scripts/test_e2e.sh`, mirroring `scripts/test_ci.sh`'s shape:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] || [ "${_WHISPER_ALOUD_UNDER_DBUS_RUN_SESSION:-}" != "1" ]; then
    exec dbus-run-session --              env _WHISPER_ALOUD_UNDER_DBUS_RUN_SESSION=1          "$0" "$@"
  fi
  python -m pytest -m e2e tests/e2e "$@"
  ```
  (the self-re-exec guard means `./scripts/test_e2e.sh` is correct whether or not the caller
  already wrapped it in `dbus-run-session`, and sets the marker `tests/e2e/conftest.py` checks
  instead of guessing from `DBUS_SESSION_BUS_ADDRESS` alone, which could coincidentally be set by
  a developer's real desktop session).
- **Not part of `scripts/test_ci.sh`** — that script stays the fast, no-D-Bus-no-audio-HW unit
  profile. WHISP-49 adds a dedicated `e2e` job to `.github/workflows/ci.yml` (`needs: fast`,
  installing `dbus-x11` defensively alongside the `full-deps` job's `libportaudio2`/
  `portaudio19-dev`/`python3-gi`/`gir1.2-gtk-4.0` — audio/gi libraries are still *imported* even
  though no real device is opened).
- **Runtime budget:** target well under a minute locally for the default (`fake` engine,
  `real_model` excluded) profile — one daemon subprocess spawn is expected in the tens of
  milliseconds once Python/gi import cost is paid (no model load, no real audio device probe),
  so the dominant cost is process-spawn count × scenario count. Budget ~2 s/test ceiling as a
  CI timeout guard (`pytest-timeout`, optional dep, or a manual per-test `signal_capture`
  timeout as the primary mechanism with a global `--timeout` as backstop).
- **Flakiness controls:** (a) daemon-ready detection via a short bounded retry loop calling
  `bus.get("org.fede.whisperaloud")` (catching the "not owned yet" exception) rather than a fixed
  `time.sleep()`; (b) `SignalCapture.wait()` timeouts as described in §7; (c) fresh daemon
  subprocess per test (§3) so no test depends on another's recorder/history state; (d) the daemon
  log attached on failure (§7) so a flake is diagnosable from the first CI failure instead of
  requiring local reproduction.

## 9. Scenario list (for WHISP-47)

| # | Scenario | Fixtures used | Expected observable outcome |
|---|---|---|---|
| 1 | Start → Stop → transcribe | `client`, `audio_fixture("hello")` | `RecordingStarted` then `StatusChanged("recording")`; `StopRecording()` returns `"transcribing"`; `RecordingStopped`, `StatusChanged("transcribing")`, then `TranscriptionReady(text, meta)` with `text == fixture.expected_text` and `meta["history_id"] >= 0` |
| 2 | Toggle twice | `client`, `audio_fixture` | first `ToggleRecording()` → `"recording"` + `RecordingStarted`; second → stops and transcribes like scenario 1 |
| 3 | Cancel mid-recording | `client`, `audio_fixture` | `CancelRecording()` returns `True`; no `TranscriptionReady`/`Error` signal fires within a short window; `GetStatus()["state"] == "idle"`; no history row added |
| 4 | Cancel mid-transcription | `client`, `audio_fixture`, `WHISPER_ALOUD_FAKE_ENGINE_LATENCY_S` set high | `StopRecording()` then `CancelRecording()` before the fake engine's artificial delay elapses → transcription cancelled, no `TranscriptionReady`, `StatusChanged("idle")` |
| 5 | Auto-stop at `max_recording_duration` | `client`, `config_overrides={"MAX_RECORDING_DURATION": "0.3"}`, a WAV fixture longer than that | without an explicit `StopRecording()` call, `RecordingStopped`/`StatusChanged("transcribing")`/`TranscriptionReady` fire on their own once the fake stream has pushed ≥0.3 s of audio |
| 6 | Level signals | `client`, `audio_fixture` with non-silent content | `LevelUpdate` fires repeatedly (~10 Hz) during recording with `level > 0.0` for at least one emission; stops firing after `StopRecording()` |
| 7 | State signal sequence | `client`, `audio_fixture` | `StatusChanged` observed in order `recording → transcribing → idle` across one full start/stop/transcribe cycle (no skipped/out-of-order states) |
| 8 | History CRUD | `client` | `GetHistory`/`SearchHistory`/`GetFavoriteHistory`/`ToggleHistoryFavorite`/`DeleteHistoryEntry`/`UpdateHistoryEntry` round-trip correctly against the temp SQLite DB (`WHISPER_ALOUD_DB_PATH`) after a transcription has been recorded |
| 9 | FTS search | `client`, two+ `audio_fixture`s with distinct expected text | `SearchHistory("distinctive-word", limit)` returns only the matching entry/entries |
| 10 | History export | `client` | whatever export surface `history_client.py`/CLI exposes (confirm exact method name while implementing WHISP-47 — not fully enumerated in this design's source reading) round-trips a known set of entries to disk |
| 11 | Config reload | `client` | `SetConfig({"transcription.language": "en"})` → `True` + `ConfigChanged`; `GetConfig()` reflects it; `ReloadConfig()` after editing the on-disk `config.json` directly picks up the file change |
| 12 | Engine switch | `client`, two `audio_fixture`s | `SetConfig({"model.engine": "fake"})` when already `"fake"` is a no-op reload path (`requires_model_reload` true since `engine` is in `MODEL_RELOAD_FIELDS`, but `create_engine` + `load_model` on `FakeSpeechEngine` must be cheap/instant); a real engine-switch assertion (`fake` → `faster-whisper` or vice versa) is `real_model`-marked since it needs a real engine to load |
| 13 | Auto-paste via stub | `client`, `config_overrides={"CLIPBOARD_AUTO_PASTE": "1"}`, a stub `PasteSimulator`/`ydotool` replacement injected via `PATH` (a fake `ydotool` shell script in a temp dir prepended to the daemon subprocess's `PATH`, recording its invocation to a file) | after `TranscriptionReady`, the stub records exactly one invocation with the expected text on the clipboard — proves the *trigger* path end-to-end without ever risking a real paste |
| 14 | Hotkeys disabled | `client` | `GetStatus()["hotkey_backend"] == "none"` under the harness's default env (no `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_SESSION_TYPE`) — asserts the natural degradation path rather than a test-mode override, since that's what real headless/CI hosts will actually see |
| 15 | CLI against the daemon | `daemon()` (no `client` — invoke `python -m whisper_aloud status`/`start`/`stop` as a subprocess with the same env) | CLI subcommands exit 0 and print the expected status/state against a real running daemon, exercising `__main__.py`'s `check_service_running`/`call_service_method` for real |
| 16 | Second-instance exit code | `daemon()` twice against the same bus, second `Popen` started while the first is still up | **today** (no WHISP-63 commits exist yet — see §10): assert only that the second process exits non-zero and does not corrupt the first instance's state; once WHISP-63 lands its specific exit code, tighten the assertion to that exact code — written as a `pytest.param` with a `xfail(strict=False)`-style note rather than hard-coding a code this branch doesn't define yet |

## 10. WHISP-63 branch status (checked, not depended on)

`git log master..fix/WHISP-63-daemon-startup-race` is **empty** — the branch exists but its tip
is `f839796`, identical to where it forked from `master`; no "already running" exit-code work has
actually landed yet. Scenario 16 above is written defensively against *today's* behavior
(`handle_daemon_command` wraps `service.run()` in a bare `except Exception` → `_error(...)` →
exit code `1` for *any* startup failure, including a name already owned on the bus) and flagged
to be tightened once WHISP-63 defines its specific code. WHISP-47 should not block on WHISP-63.

## 11. Feasibility probe

Ran (and deleted afterward — no load-bearing script left behind):

```
timeout 30 dbus-run-session -- /home/fede/.venvs/whisper_aloud/bin/python <probe.py>
```

where `<probe.py>` published a dummy pydbus service (`org.fede.whisperaloud.test.dummy`) with one
method and one signal on a background thread, and a client in the main thread connected via
`SessionBus()`, called the method, and pumped `GLib.MainContext.default()` waiting for the
signal. Result:

```
REPLY: pong
SIGNAL_RECEIVED: True
PROBE_OK
EXIT:0
```

`dbus-run-session` (version 1.16.2, already on `PATH` in this dev environment) correctly starts
a private bus, `pydbus` publish/call/signal round-trips work inside it end-to-end, and the whole
thing exits cleanly under a 30 s `timeout` with no leftover process. This validates the core of
Decision 1 (§3) before any implementation code is written. CI base-image availability of
`dbus-run-session` itself (it ships in the `dbus` package, commonly preinstalled, or `dbus-x11`)
is WHISP-49's concern, not re-verified here.

## 12. Risks / open questions for WHISP-47

1. **Must land the `WHISPER_ALOUD_TEST_MODE` hook (§4) before any scenario test that constructs
   a real daemon**, even ones that don't care about mic gain — `_init_components()` reaches
   `GainController.restore_from_crash_file()` unconditionally. Treat this as a blocking
   prerequisite task, not an optional hardening pass.
2. History export (scenario 10) wasn't fully traced to a concrete D-Bus method/CLI command in
   this reading pass — WHISP-47 should confirm the exact surface (`history_client.py`/CLI) before
   writing that test.
3. The `fake` engine's determinism relies on a fresh daemon subprocess per test (one env var set
   at spawn, not changed at runtime). If a future ticket wants a session-scoped daemon for speed,
   it will need to revisit §6's env-var-based wiring (most likely moving to the `SetConfig`-based
   alternative this design rejected, accepting its `to_dict()`/`GetConfig()` surface cost).
4. `ydotool`/`wl-copy`/`xdotool` availability on the CI runner is unknown; scenario 13's stub
   approach (`PATH` injection) sidesteps needing any of them installed, but should be double
   checked against whichever binary `clipboard.py`'s `PasteSimulator` actually shells out to by
   name for the session type the harness presents (no `DISPLAY`/`WAYLAND_DISPLAY` → confirm which
   code path `_session_type` resolves to and that the stub matches that binary name).
