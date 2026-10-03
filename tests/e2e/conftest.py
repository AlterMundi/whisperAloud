"""E2E harness fixtures: real daemon subprocess, real D-Bus, fake audio/STT.

See docs/superpowers/specs/2026-10-01-whisp46-e2e-harness-design.md. This
whole directory only runs under scripts/test_e2e.sh (a thin wrapper around
the repo's shared scripts/test_private_bus.sh, WHISP-64), which launches
pytest inside `dbus-run-session` with WHISPER_ALOUD_PRIVATE_BUS=1 set. A
plain `pytest -q` (no `-m e2e tests/e2e`, and without that env/bus) must
never reach a daemon-spawning fixture here -- the guard below aborts the
whole session first.
"""
from __future__ import annotations

import dataclasses
import os
import signal as signal_module
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

import pytest

from tests._private_bus import (
    UnsafeBusActivationError,
    assert_not_activatable,
    assert_process_is_not_flatpak,
    private_bus_address,
)
from tests.e2e.env_builder import build_daemon_env


# ─── Orphan-proofing: PR_SET_PDEATHSIG (Linux only) ────────────────────────
#
# Incident: a pytest run killed by an external `timeout` (SIGKILL/SIGTERM on
# the pytest process itself) never runs fixture teardown, so a daemon
# subprocess spawned by `daemon()` can outlive both pytest and the private
# bus it was talking to -- reparented to init/systemd, holding
# WHISPER_ALOUD_TEST_MODE=1 and a dead DBUS_SESSION_BUS_ADDRESS forever. This
# makes the daemon ask the kernel to SIGTERM *it* the moment its parent
# (pytest) exits for any reason, including an external kill -- no polling,
# no reliance on fixture teardown ever running.
def _death_pact_with_parent():
    """preexec_fn for subprocess.Popen: calls prctl(PR_SET_PDEATHSIG, SIGTERM)
    in the child after fork(), before exec(). Linux-only; a no-op import
    failure (e.g. non-glibc libc) degrades silently -- fixture teardown via
    Quit()/terminate()/kill() remains the primary mechanism, this is defense
    in depth only."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        pr_set_pdeathsig = 1
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(pr_set_pdeathsig, signal_module.SIGTERM)
    except Exception:
        pass


_DEATH_PACT = _death_pact_with_parent if sys.platform.startswith("linux") else None


# ─── Per-test hard watchdog (pytest-timeout is not an installed dependency) ─
#
# Bounds the wall-clock time of each e2e test's body independent of any
# individual SignalCapture.wait(timeout=...) call, so a bug that makes a
# test spin forever (e.g. a wait() that never raises) can't hang the whole
# suite -- and, combined with the PDEATHSIG above, can't leak an orphaned
# daemon either, since raising here unwinds normally through the test
# function and still runs every fixture's finalizer (including
# DaemonHandle.stop()) exactly as a normal assertion failure would.
_HARD_TEST_TIMEOUT_S = 20.0


@pytest.fixture(autouse=True)
def _hard_test_timeout():
    if not hasattr(signal_module, "SIGALRM"):
        yield
        return

    def _on_alarm(signum, frame):
        raise AssertionError(
            f"test exceeded the e2e harness's hard per-test watchdog "
            f"({_HARD_TEST_TIMEOUT_S}s) -- likely a hung wait(); see WHISP-47"
        )

    previous_handler = signal_module.signal(signal_module.SIGALRM, _on_alarm)
    signal_module.setitimer(signal_module.ITIMER_REAL, _HARD_TEST_TIMEOUT_S)
    try:
        yield
    finally:
        signal_module.setitimer(signal_module.ITIMER_REAL, 0)
        signal_module.signal(signal_module.SIGALRM, previous_handler)


# ─── Session guard: refuse to run anywhere but a private dbus-run-session bus ──
#
# private_bus_address() never connects to a bus -- it only inspects env vars
# the root conftest.py already captured (WHISPER_ALOUD_TEST_ORIGINAL_DBUS_ADDRESS)
# plus an explicit opt-in (WHISPER_ALOUD_PRIVATE_BUS=1) that only
# scripts/test_private_bus.sh sets. If it returns None, no daemon has been
# spawned yet and none ever will be -- abort collection entirely rather than
# silently skipping test-by-test.
_PRIVATE_BUS_ADDRESS = private_bus_address()
if _PRIVATE_BUS_ADDRESS is None:
    pytest.exit(
        "tests/e2e requires a private dbus-run-session bus -- "
        "run via scripts/test_e2e.sh, not pytest directly",
        returncode=1,
    )

# Every fixture below (and the daemon subprocess env it builds) must point at
# this address, not whatever the root conftest poisoned DBUS_SESSION_BUS_ADDRESS
# to for unit-test isolation.
os.environ["DBUS_SESSION_BUS_ADDRESS"] = _PRIVATE_BUS_ADDRESS

# ─── Fail-closed activation guard (incident follow-up) ─────────────────────
#
# private_bus_address() above only checks that this is *some* throwaway
# dbus-run-session bus, not that it was started with the dedicated,
# servicedir-free config (tests/dbus/session-test.conf). A bus started
# without --config-file falls back to the system default session.conf's
# <standard_session_servicedirs/>, which, on a machine with the real
# WhisperAloud Flatpak installed, makes org.fede.whisperaloud activatable via
# the Flatpak's exported .service file. Before any fixture below ever does a
# bus.get(BUS_NAME) (which is exactly what would trigger that activation),
# ask the bus itself -- read-only, via ListActivatableNames -- whether that
# name is activatable at all, and abort the whole session if so. This harness
# never registers org.fede.whisperaloud for activation on the outer bus (the
# `daemon` fixture below spawns it directly via subprocess.Popen), so "not
# activatable" is the correct invariant unconditionally.
# Incident (WHISP-hang follow-up): this guard originally opened its own raw
# connection via pydbus.connect(_PRIVATE_BUS_ADDRESS) (Gio.DBusConnection.
# new_for_address_sync), kept alive for the whole session via a module
# global. That is a second, independent GDBusConnection to the same socket,
# separate from the one *every* WhisperAloudClient() in this session shares
# (pydbus.SessionBus() -> Gio.bus_get_sync(), which GLib caches as a single
# per-process singleton once DBUS_SESSION_BUS_ADDRESS is set -- see right
# above). Two live GDBusConnection objects to the same bus, each with its
# own GDBus worker thread, made tests/e2e/test_config.py's SignalCapture
# (which subscribes a signal on the *cached* SessionBus() connection) hang
# intermittently inside Gio's signal_subscribe -- reproduced via
# -o faulthandler_timeout=60 (2 of 5 full-suite runs hung; subsets never
# did). Using the same cached SessionBus() connection here instead -- which
# the rest of this module, DaemonHandle.stop(), and _wait_for_daemon_ready
# below all do too -- means the guard creates zero extra connections: by the
# time any fixture below runs, there is exactly one GDBusConnection for the
# whole e2e session, exactly as there was before this guard existed.
_BUS_NAME_FOR_GUARD = "org.fede.whisperaloud"  # same value as BUS_NAME, defined below
try:
    import pydbus as _pydbus_guard

    _guard_bus = _pydbus_guard.SessionBus()
    _activatable = list(_guard_bus.dbus.ListActivatableNames())
    assert_not_activatable(_activatable, _BUS_NAME_FOR_GUARD)
except UnsafeBusActivationError as _exc:
    pytest.exit(f"tests/e2e refusing to run: {_exc}", returncode=1)
except Exception as _exc:  # pragma: no cover - defensive: fail closed, not open
    pytest.exit(
        f"tests/e2e could not verify the private bus is safe to use "
        f"(ListActivatableNames failed: {_exc!r}); refusing to proceed",
        returncode=1,
    )

from whisper_aloud.service.client import WhisperAloudClient  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "audio"
BUS_NAME = "org.fede.whisperaloud"


# ─── DaemonHandle: owns one daemon subprocess, stopped only via its Popen ──────


@dataclasses.dataclass
class DaemonHandle:
    proc: subprocess.Popen
    xdg_home: Path
    log_path: Path
    bus_name: str = BUS_NAME

    def client(self) -> WhisperAloudClient:
        return WhisperAloudClient()

    def stop(self, timeout: float = 5.0) -> None:
        """Quit() over D-Bus, then proc.terminate(), then proc.kill() -- the
        Popen object this fixture itself created, never a PID looked up by
        name or pattern (see tests/test_e2e_harness_safety.py)."""
        if self.proc.poll() is not None:
            return

        try:
            import pydbus

            # SessionBus() (Gio.bus_get_sync, process-wide cached singleton)
            # rather than pydbus.connect(_PRIVATE_BUS_ADDRESS) (a brand new
            # GDBusConnection each call): see the module-level guard's
            # comment above for why a second, independent connection to this
            # bus caused intermittent hangs elsewhere in the suite.
            bus = pydbus.SessionBus()
            proxy = bus.get(self.bus_name)
            proxy.Quit()
        except Exception:
            pass  # daemon may already be gone, or never finished starting

        try:
            self.proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass

        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout / 2)
            return
        except subprocess.TimeoutExpired:
            pass

        self.proc.kill()
        self.proc.wait(timeout=timeout)
        pytest.fail(
            f"daemon subprocess (pid {self.proc.pid}) did not exit after Quit()+terminate(); "
            f"had to kill() it -- see {self.log_path}"
        )


def _build_daemon_env(xdg_home: Path, config_overrides: Optional[dict] = None) -> dict:
    """Thin wrapper around env_builder.build_daemon_env binding this
    session's private bus address and creating the fresh runtime dir it
    references (kept a separate, pure, importable function so it can be
    unit-tested without a real bus -- see tests/test_e2e_env_builder.py)."""
    runtime_dir = xdg_home / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o700)
    return build_daemon_env(xdg_home, _PRIVATE_BUS_ADDRESS, config_overrides)


def _wait_for_daemon_ready(proc: subprocess.Popen, timeout: float) -> None:
    """Bounded retry loop for bus.get(BUS_NAME) -- no fixed sleep, no polling
    a PID file; just "is the name owned yet" (WHISP-46 design doc section 8:
    flakiness control (a)).

    Once the name is owned, verifies (read-only, via
    GetConnectionUnixProcessID) that the owner is *this* subprocess
    (``proc.pid``) and, defense in depth, that it does not look like a
    flatpak/bwrap process -- see the module-level activation guard above for
    why that distinction matters: a misconfigured bus could let a *different*
    process (the real, installed Flatpak app) win the name instead of the
    daemon this fixture itself spawned.

    Uses pydbus.SessionBus() (the same cached, process-wide GDBusConnection
    singleton every other call site in this module uses), not
    pydbus.connect(_PRIVATE_BUS_ADDRESS) -- the latter opened a brand new
    GDBusConnection on every 0.1s retry, which (combined with the
    module-level guard's own separate connection) caused intermittent hangs
    elsewhere in the suite; see the guard's comment above.
    """
    import pydbus

    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"daemon subprocess exited early (code {proc.returncode}) before "
                f"publishing {BUS_NAME}"
            )
        try:
            bus = pydbus.SessionBus()
            bus.get(BUS_NAME)
            owner_pid = int(bus.dbus.GetConnectionUnixProcessID(BUS_NAME))
            if owner_pid != proc.pid:
                raise UnsafeBusActivationError(
                    f"{BUS_NAME} is owned by pid {owner_pid}, not the daemon "
                    f"subprocess this fixture spawned (pid {proc.pid}); refusing "
                    f"to use it -- something else (possibly a real installed "
                    f"instance) won the bus name race."
                )
            assert_process_is_not_flatpak(owner_pid)
            return
        except UnsafeBusActivationError:
            raise
        except Exception as e:
            last_error = e
            time.sleep(0.1)
    raise RuntimeError(f"daemon did not publish {BUS_NAME} within {timeout}s: {last_error}")


@pytest.fixture
def daemon(tmp_path, request) -> Callable[..., DaemonHandle]:
    """Factory fixture: call to spawn the real daemon as a subprocess on this
    session's private bus. Teardown (stop()) is registered automatically."""

    spawned: list[DaemonHandle] = []

    def _start(
        config_overrides: Optional[dict] = None,
        *,
        fake_audio: Optional[str] = None,
        ready_timeout: float = 5.0,
    ) -> DaemonHandle:
        xdg_home = tmp_path / f"home-{len(spawned)}"
        xdg_home.mkdir(parents=True, exist_ok=True)
        env = _build_daemon_env(xdg_home, config_overrides)
        if fake_audio:
            fixture = _resolve_audio_fixture(fake_audio)
            env["WHISPER_ALOUD_FAKE_AUDIO_WAV"] = str(fixture.wav_path)

        log_path = xdg_home / "daemon.log"
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            [sys.executable, "-m", "whisper_aloud", "--daemon"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=_DEATH_PACT,
        )
        handle = DaemonHandle(proc=proc, xdg_home=xdg_home, log_path=log_path)
        spawned.append(handle)

        def _finalize():
            handle.stop()
            log_file.close()

        request.addfinalizer(_finalize)

        try:
            _wait_for_daemon_ready(proc, ready_timeout)
        except UnsafeBusActivationError as exc:
            # Something other than this fixture's own subprocess answered for
            # BUS_NAME -- possibly a real, installed instance won a bus-name
            # race. Abort the whole session rather than merely failing this
            # test: continuing could spawn more daemons against an unsafe bus.
            pytest.exit(f"tests/e2e refusing to continue: {exc}", returncode=1)
        except RuntimeError:
            tail = ""
            try:
                tail = log_path.read_text()[-4000:]
            except Exception:
                pass
            pytest.fail(f"daemon failed to become ready -- log tail:\n{tail}")

        return handle

    return _start


@pytest.fixture
def client(daemon) -> Iterator[WhisperAloudClient]:
    """Convenience: start a daemon with harness defaults and return a
    connected client. Most scenario tests want this."""
    handle = daemon()
    c = handle.client()
    yield c
    c.disconnect()


# ─── Audio fixtures ─────────────────────────────────────────────────────────


@dataclasses.dataclass
class AudioFixture:
    name: str
    wav_path: Path
    transcript_path: Path
    expected_text: str


def _resolve_audio_fixture(name: str) -> AudioFixture:
    wav_path = FIXTURES_DIR / f"{name}.wav"
    transcript_path = FIXTURES_DIR / f"{name}.txt"
    if not wav_path.exists():
        pytest.fail(f"no such audio fixture: {wav_path}")
    if not transcript_path.exists():
        pytest.fail(f"audio fixture {name!r} is missing its sidecar transcript: {transcript_path}")
    return AudioFixture(
        name=name,
        wav_path=wav_path,
        transcript_path=transcript_path,
        expected_text=transcript_path.read_text().strip(),
    )


@pytest.fixture
def audio_fixture() -> Callable[[str], AudioFixture]:
    """Resolve a fixture name to its WAV + sidecar .txt under
    tests/e2e/fixtures/audio/. Fails loudly if the sidecar is missing."""
    return _resolve_audio_fixture


# ─── Signal capture ─────────────────────────────────────────────────────────


class SignalCapture:
    """Subscribes to one D-Bus signal on a client and lets a test block until
    it fires, with a timeout and an optional predicate on the signal args."""

    def __init__(self, client: WhisperAloudClient, signal_name: str):
        self._received: list[tuple] = []
        # Cursor into _received, persisted ACROSS wait() calls (bugfix: this
        # used to be a local reset to 0 on every call, which made a second
        # wait() on the same capture always re-match the first signal ever
        # seen instead of advancing to the next one -- harmless for a single
        # wait() per capture, but an effectively infinite busy-loop for any
        # test polling "no further matching signal arrives", since a
        # matching predicate against stale data returns instantly, forever).
        self._checked = 0
        subscribe = getattr(client, f"on_{_to_snake(signal_name)}", None)
        if subscribe is None:
            raise AttributeError(f"WhisperAloudClient has no subscriber for {signal_name!r}")
        subscribe(lambda *args: self._received.append(args))

    def wait(self, timeout: float = 5.0, predicate: Optional[Callable[..., bool]] = None) -> tuple:
        from gi.repository import GLib

        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            while self._checked < len(self._received):
                args = self._received[self._checked]
                self._checked += 1
                if predicate is None or predicate(*args):
                    return args
            time.sleep(0.01)
        raise AssertionError(f"timed out after {timeout}s waiting for a matching signal")


def _to_snake(signal_name: str) -> str:
    out = []
    for i, ch in enumerate(signal_name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


@pytest.fixture
def signal_capture(client) -> Callable[[str], SignalCapture]:
    def _capture(signal_name: str) -> SignalCapture:
        return SignalCapture(client, signal_name)

    return _capture


# ─── real_model marker: skip unless the configured model is already cached ──


def pytest_collection_modifyitems(config, items):
    try:
        from whisper_aloud.config import WhisperAloudConfig
        from whisper_aloud.stt.model_download import _is_present, model_dir

        cfg = WhisperAloudConfig.load()
        cached = _is_present(model_dir(cfg.model.name))
    except Exception:
        cached = False

    if cached:
        return

    skip_real_model = pytest.mark.skip(
        reason="real_model: configured model is not cached locally (run after downloading it)"
    )
    for item in items:
        if "real_model" in item.keywords:
            item.add_marker(skip_real_model)
