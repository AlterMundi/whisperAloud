"""Decide whether tests may use D-Bus, without ever connecting to a bus.

Tests that need a real bus (e2e harness, fake-portal integration tests) must
only ever run on a throwaway bus started by ``dbus-run-session``. The check is
purely static on purpose: probing a bus to see whether it is safe would
itself touch the user's desktop session when the answer is "no".

A bus is considered private only when BOTH hold:

* the test entry point opted in by exporting ``WHISPER_ALOUD_PRIVATE_BUS=1``
  inside ``dbus-run-session`` (see ``scripts/test_private_bus.sh``), and
* the original session address does not point at the user's login bus under
  ``$XDG_RUNTIME_DIR`` (ignoring any ``,guid=...`` suffix).
"""

import os

# Root conftest.py stores the session address here before it points
# DBUS_SESSION_BUS_ADDRESS at a dead socket for every other test.
ORIGINAL_ADDRESS_ENV = "WHISPER_ALOUD_TEST_ORIGINAL_DBUS_ADDRESS"
OPT_IN_ENV = "WHISPER_ALOUD_PRIVATE_BUS"


def _paths(address: str) -> list[str]:
    """Socket paths named by a D-Bus address list (``;``-separated)."""
    paths = []
    for entry in address.split(";"):
        _transport, _, params = entry.partition(":")
        for param in params.split(","):
            key, _, value = param.partition("=")
            if key in ("path", "abstract") and value:
                paths.append(value)
    return paths


def private_bus_address() -> str | None:
    """Return the private bus address tests may use, or None if unsafe."""
    if os.environ.get(OPT_IN_ENV) != "1":
        return None
    address = os.environ.get(ORIGINAL_ADDRESS_ENV, "")
    if not address:
        return None
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    login_bus = os.path.join(runtime_dir, "bus")
    if any(p == login_bus or p.startswith(runtime_dir + os.sep) for p in _paths(address)):
        return None
    return address
