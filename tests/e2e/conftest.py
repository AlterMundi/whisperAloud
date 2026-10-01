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
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

import pytest

from tests._private_bus import private_bus_address

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

from whisper_aloud.service.client import WhisperAloudClient  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "audio"
BUS_NAME = "org.fede.whisperaloud"

# The dev venv's whisper_aloud install may be editable against a *different*
# checkout (e.g. the main repo, not this worktree) -- pytest itself only
# sees the worktree's src/ because the root conftest.py inserts it into
# sys.path for THIS process. The daemon subprocess is a brand new
# interpreter that does not inherit sys.path, only env vars, so it must be
# told explicitly via PYTHONPATH or it will run the wrong code.
_WORKTREE_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")


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

            bus = pydbus.connect(_PRIVATE_BUS_ADDRESS)
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
    """Harness defaults from the design doc's env table (section 4): fake
    engine, every real-world side effect disabled, a fresh HOME/XDG tree, and
    the private bus address this whole pytest session is running on."""
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        _WORKTREE_SRC if not existing_pythonpath else f"{_WORKTREE_SRC}{os.pathsep}{existing_pythonpath}"
    )
    env.update(
        {
            "WHISPER_ALOUD_TEST_MODE": "1",
            "WHISPER_ALOUD_MODEL_ENGINE": "fake",
            "WHISPER_ALOUD_MODEL_NAME": "fake",
            "WHISPER_ALOUD_RAISE_MIC_GAIN": "0",
            "WHISPER_ALOUD_PAUSE_MEDIA": "0",
            "WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE": "0",
            "WHISPER_ALOUD_NOTIFICATIONS_ENABLED": "0",
            "WHISPER_ALOUD_DB_PATH": str(xdg_home / "data" / "history.sqlite3"),
            "HOME": str(xdg_home),
            "XDG_CONFIG_HOME": str(xdg_home / "config"),
            "XDG_DATA_HOME": str(xdg_home / "data"),
            "XDG_CACHE_HOME": str(xdg_home / "cache"),
            "XDG_STATE_HOME": str(xdg_home / "state"),
            "DBUS_SESSION_BUS_ADDRESS": _PRIVATE_BUS_ADDRESS,
        }
    )
    # DISPLAY / WAYLAND_DISPLAY / XDG_SESSION_TYPE intentionally absent --
    # the daemon's natural degradation path for hotkeys/indicator.
    for var in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
        env.pop(var, None)
    for key, value in (config_overrides or {}).items():
        env[f"WHISPER_ALOUD_{key}"] = str(value)
    return env


def _wait_for_daemon_ready(proc: subprocess.Popen, timeout: float) -> None:
    """Bounded retry loop for bus.get(BUS_NAME) -- no fixed sleep, no polling
    a PID file; just "is the name owned yet" (WHISP-46 design doc section 8:
    flakiness control (a))."""
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
            bus = pydbus.connect(_PRIVATE_BUS_ADDRESS)
            bus.get(BUS_NAME)
            return
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
        )
        handle = DaemonHandle(proc=proc, xdg_home=xdg_home, log_path=log_path)
        spawned.append(handle)

        def _finalize():
            handle.stop()
            log_file.close()

        request.addfinalizer(_finalize)

        try:
            _wait_for_daemon_ready(proc, ready_timeout)
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
        subscribe = getattr(client, f"on_{_to_snake(signal_name)}", None)
        if subscribe is None:
            raise AttributeError(f"WhisperAloudClient has no subscriber for {signal_name!r}")
        subscribe(lambda *args: self._received.append(args))

    def wait(self, timeout: float = 5.0, predicate: Optional[Callable[..., bool]] = None) -> tuple:
        from gi.repository import GLib

        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        checked = 0
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            while checked < len(self._received):
                args = self._received[checked]
                checked += 1
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
