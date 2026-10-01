"""Safety gate: refuse to run the e2e harness against the user's real D-Bus
session bus.

This module is deliberately pure/import-light (no daemon spawning, no pytest
dependency beyond this docstring's context) so it can be unit-tested with no
real bus involved at all -- see tests/test_e2e_bus_guard.py, which must stay
green before anything in tests/e2e/ spawns a daemon subprocess.

WHISP-46 orchestrator amendment 1: tests/e2e/conftest.py must abort the whole
e2e session (pytest.exit, not skip) unless *all* of these hold:
  1. DBUS_SESSION_BUS_ADDRESS (captured before the root conftest poisoned it,
     see conftest.py / real_session_bus_address()) is set.
  2. It differs from unix:path=$XDG_RUNTIME_DIR/bus -- the systemd
     user-session default bus path a real desktop session always uses.
  3. org.freedesktop.systemd1 is NOT owned on that bus -- a dbus-run-session
     bus never owns this name; the user's real session bus always does.
"""
from __future__ import annotations

import os
from typing import Callable


class UnsafeBusError(RuntimeError):
    """Raised when the e2e harness would run against a bus that looks like
    the user's real session bus instead of a private dbus-run-session bus."""


def real_session_bus_address() -> str:
    """Return the DBUS_SESSION_BUS_ADDRESS this process started with, before
    the root conftest.py poisoned it for unit-test isolation. Empty string if
    pytest was never launched under dbus-run-session (see conftest.py)."""
    return os.environ.get("_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS", "")


def is_systemd1_owned(address: str) -> bool:
    """True if org.freedesktop.systemd1 is owned on the bus at `address`.

    A private dbus-run-session bus never owns this name (nothing activates
    it there); the user's real session bus always does. Any failure to
    connect or resolve the name is treated as "not owned" -- the caller
    (assert_private_e2e_bus) only uses this to make an *already-plausible*
    private bus even more certain, never to green-light an address that
    failed the cheap path check first.
    """
    try:
        import pydbus

        bus = pydbus.connect(address) if address else pydbus.SessionBus()
        bus.get("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
        return True
    except Exception:
        return False


def assert_private_e2e_bus(
    address: str,
    systemd1_owned_check: Callable[[str], bool] = is_systemd1_owned,
) -> None:
    """Raise UnsafeBusError unless `address` looks like a private
    dbus-run-session bus, never the user's real session bus.

    Cheap checks run first and short-circuit before any D-Bus connection is
    attempted, so an obviously-unsafe address (empty, or the systemd
    user-session default path) never reaches `systemd1_owned_check`.
    """
    if not address:
        raise UnsafeBusError(
            "tests/e2e requires running under dbus-run-session "
            "(use scripts/test_e2e.sh, not pytest directly)"
        )

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    real_bus_path = f"unix:path={runtime_dir}/bus" if runtime_dir else None
    if real_bus_path and address == real_bus_path:
        raise UnsafeBusError(
            f"refusing to run e2e tests against the real session bus ({address}); "
            "use scripts/test_e2e.sh, which runs under dbus-run-session"
        )

    if systemd1_owned_check(address):
        raise UnsafeBusError(
            f"refusing to run e2e tests: org.freedesktop.systemd1 is owned on {address} "
            "(this looks like the real session bus, not a private dbus-run-session bus)"
        )
