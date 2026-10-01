"""Pure builder for the e2e daemon subprocess's environment (WHISP-46).

Deliberately separate from conftest.py -- conftest.py aborts the whole
session (pytest.exit) unless it detects a private dbus-run-session bus, so
it cannot be imported from the default unit suite to unit-test env-building
logic in isolation. This module has no such guard and no side effects at
import time, so tests/test_e2e_env_builder.py can inspect the dict it
builds without ever touching a real bus or spawning anything.

Incident (WHISP-46): an e2e run overwrote the operator's real Wayland
clipboard. Investigation found two gaps: (1) ClipboardManager itself was not
test-mode-gated (fixed separately, see clipboard/clipboard_manager.py's
TestClipboardStub), and (2) unsetting WAYLAND_DISPLAY/DISPLAY alone does not
isolate the daemon subprocess from the real desktop session -- PipeWire,
PulseAudio and wl-clipboard all have their own fallback discovery that does
not go through those two variables. This module adds the barrier: a fresh,
empty XDG_RUNTIME_DIR (so no real wayland-*/pipewire-*/bus-* sockets are
discoverable there), PULSE_SERVER unset, and PIPEWIRE_REMOTE pointed at a
path that cannot exist, so any code path that still tries to open a real
audio/display/clipboard backend fails closed instead of silently reaching
the operator's real session.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# The dev venv's whisper_aloud install may be editable against a *different*
# checkout (e.g. the main repo, not this worktree) -- pytest itself only
# sees the worktree's src/ because the root conftest.py inserts it into
# sys.path for THIS process. The daemon subprocess is a brand new
# interpreter that does not inherit sys.path, only env vars, so it must be
# told explicitly via PYTHONPATH or it will run the wrong code.
WORKTREE_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")

# Env vars that must never leak from the pytest process (running on the
# operator's real desktop session) into the daemon subprocess.
_REAL_SESSION_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "PULSE_SERVER")


def build_daemon_env(
    xdg_home: Path,
    dbus_address: str,
    config_overrides: Optional[dict] = None,
    base_env: Optional[dict] = None,
) -> dict:
    """Harness defaults from the design doc's env table (section 4), plus the
    WHISP-46 incident's isolation barrier. `base_env` defaults to
    os.environ (injectable for tests, which must never mutate the real
    process env)."""
    env = dict(base_env if base_env is not None else os.environ)

    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        WORKTREE_SRC if not existing_pythonpath else f"{WORKTREE_SRC}{os.pathsep}{existing_pythonpath}"
    )

    runtime_dir = xdg_home / "runtime"
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
            # Fresh, empty, per-daemon runtime dir: no real wayland-*,
            # pipewire-*, or bus-* socket is discoverable under it, unlike
            # the real $XDG_RUNTIME_DIR this pytest process inherited.
            "XDG_RUNTIME_DIR": str(runtime_dir),
            # PIPEWIRE_REMOTE pointed at a path that cannot exist: any code
            # path that still tries to open a real audio device fails
            # closed instead of silently reaching the real PipeWire server.
            "PIPEWIRE_REMOTE": str(xdg_home / "no-such-pipewire-remote"),
            # The private dbus-run-session bus address is explicit (an
            # absolute unix:path=... / unix:abstract=... string), not
            # derived from XDG_RUNTIME_DIR, so changing XDG_RUNTIME_DIR
            # above does not affect reachability of this bus.
            "DBUS_SESSION_BUS_ADDRESS": dbus_address,
        }
    )
    for var in _REAL_SESSION_VARS:
        env.pop(var, None)
    for key, value in (config_overrides or {}).items():
        env[f"WHISPER_ALOUD_{key}"] = str(value)
    return env
