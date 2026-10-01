"""Pure builder for the e2e daemon subprocess's environment (WHISP-46).

Deliberately separate from conftest.py -- conftest.py aborts the whole
session (pytest.exit) unless it detects a private dbus-run-session bus, so
it cannot be imported from the default unit suite to unit-test env-building
logic in isolation. This module has no such guard and no side effects at
import time, so tests/test_e2e_env_builder.py can inspect the dict it
builds without ever touching a real bus or spawning anything.

Incident (WHISP-46): an e2e run overwrote the operator's real Wayland
clipboard, and a follow-up audit found that starting from `dict(os.environ)`
and only removing a handful of variables leaked XAUTHORITY, SSH_AUTH_SOCK,
GNOME_KEYRING_*, and -- worst of all -- AT_SPI_BUS_ADDRESS, a *second* real
D-Bus address that bypasses the DBUS_SESSION_BUS_ADDRESS isolation entirely.
Blocklisting individual dangerous variables is a losing game (there is no
bound on what the operator's desktop session might export); this builds the
daemon subprocess's env as an ALLOWLIST instead: a small, fixed set of
locale/terminal variables inherited unchanged if present, plus every
HOME/XDG/WHISPER_ALOUD_*/bus variable this module itself sets. Nothing else
from the real process environment is ever inherited.
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

# The only variables inherited from the real process environment, and only
# if present there -- plain locale/terminal plumbing, nothing session- or
# bus-related. LC_* is a prefix (LC_ALL, LC_CTYPE, LC_TIME, ... are all
# independently settable).
_INHERITED_EXACT = ("PATH", "LANG", "LANGUAGE", "TERM", "TZ")
_INHERITED_PREFIXES = ("LC_",)


def _is_inherited(key: str) -> bool:
    return key in _INHERITED_EXACT or key.startswith(_INHERITED_PREFIXES)


def build_daemon_env(
    xdg_home: Path,
    dbus_address: str,
    config_overrides: Optional[dict] = None,
    base_env: Optional[dict] = None,
) -> dict:
    """Build the daemon subprocess's environment from an allowlist, not a
    blocklist. `base_env` defaults to os.environ (injectable for tests,
    which must never mutate the real process env)."""
    source = base_env if base_env is not None else os.environ

    env = {key: value for key, value in source.items() if _is_inherited(key)}

    runtime_dir = xdg_home / "runtime"
    env.update(
        {
            # Forced, never inherited from the real process's PYTHONPATH.
            "PYTHONPATH": WORKTREE_SRC,
            "HOME": str(xdg_home),
            "XDG_CONFIG_HOME": str(xdg_home / "config"),
            "XDG_DATA_HOME": str(xdg_home / "data"),
            "XDG_CACHE_HOME": str(xdg_home / "cache"),
            "XDG_STATE_HOME": str(xdg_home / "state"),
            # Fresh, empty, per-daemon runtime dir: no real wayland-*,
            # pipewire-*, bus, or at-spi socket is discoverable under it,
            # unlike the real $XDG_RUNTIME_DIR the pytest process inherited
            # (which this builder never copies from in the first place).
            "XDG_RUNTIME_DIR": str(runtime_dir),
            # PIPEWIRE_REMOTE pointed at a path that cannot exist: any code
            # path that still tries to open a real audio device fails
            # closed instead of silently reaching a real PipeWire server.
            "PIPEWIRE_REMOTE": str(xdg_home / "no-such-pipewire-remote"),
            # Disable the AT-SPI D-Bus registration path entirely -- GTK/Gio
            # otherwise resolve a second real bus address
            # (AT_SPI_BUS_ADDRESS, or a lookup through the real session bus)
            # independently of DBUS_SESSION_BUS_ADDRESS.
            "NO_AT_BRIDGE": "1",
            # The private dbus-run-session bus address is explicit (an
            # absolute unix:path=... / unix:abstract=... string), not
            # derived from XDG_RUNTIME_DIR, so the new XDG_RUNTIME_DIR above
            # does not affect reachability of this bus.
            "DBUS_SESSION_BUS_ADDRESS": dbus_address,
            "WHISPER_ALOUD_TEST_MODE": "1",
            "WHISPER_ALOUD_MODEL_ENGINE": "fake",
            "WHISPER_ALOUD_MODEL_NAME": "fake",
            "WHISPER_ALOUD_RAISE_MIC_GAIN": "0",
            "WHISPER_ALOUD_PAUSE_MEDIA": "0",
            "WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE": "0",
            "WHISPER_ALOUD_NOTIFICATIONS_ENABLED": "0",
            "WHISPER_ALOUD_DB_PATH": str(xdg_home / "data" / "history.sqlite3"),
        }
    )
    for key, value in (config_overrides or {}).items():
        env[f"WHISPER_ALOUD_{key}"] = str(value)
    return env
