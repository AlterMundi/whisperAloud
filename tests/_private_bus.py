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


# ─── Fail-closed activation guard (incident follow-up) ──────────────────────
#
# Even a bus that passes private_bus_address() above could still have been
# started with the wrong dbus-daemon config (e.g. a bare `dbus-run-session`
# that fell back to the system default session.conf's
# <standard_session_servicedirs/>). On a machine with the real WhisperAloud
# Flatpak installed, that makes org.fede.whisperaloud *activatable* on the
# "private" bus via ~/.local/share/flatpak/exports/.../org.fede.whisperaloud.service
# -- so the harness's own first bus.get(BUS_NAME) would D-Bus-activate the
# real, maintainer's installed app instead of (or racing) the daemon
# subprocess the harness itself spawns via Popen.
#
# These two checks are the fail-closed guard: both are pure functions over
# data the caller already fetched (ListActivatableNames()'s result list, and
# /proc text), so they are unit-testable without ever touching a real bus or
# process, and the conftest.py call sites do the actual (read-only) I/O.


class UnsafeBusActivationError(RuntimeError):
    """Raised when a bus the harness is about to use is not as isolated as
    it must be -- either a name is activatable that should never be, or a
    process answering for that name looks like it is running inside
    flatpak/bwrap rather than being the worktree's own `python -m
    whisper_aloud --daemon` subprocess."""


def assert_not_activatable(activatable_names, bus_name: str) -> None:
    """Raise unless ``bus_name`` is absent from ``activatable_names``.

    The e2e/dbus_private harness never relies on D-Bus activation of
    org.fede.whisperaloud on the outer private bus -- every daemon it talks
    to is spawned directly via subprocess.Popen (see tests/e2e/conftest.py's
    `daemon` fixture) -- so the invariant is simply "never activatable",
    not "only activatable from a harness-registered servicedir".
    """
    if bus_name in activatable_names:
        raise UnsafeBusActivationError(
            f"{bus_name!r} is activatable on this bus (ListActivatableNames "
            f"returned it), but this harness never registers it for "
            f"activation -- the bus was likely started without "
            f"tests/dbus/session-test.conf (see scripts/test_private_bus.sh) "
            f"and picked up a real exported .service file (e.g. a Flatpak "
            f"install) via XDG_DATA_DIRS instead. Refusing to proceed."
        )


def is_flatpak_or_bwrap_process(exe_path: str, cmdline_text: str = "") -> bool:
    """Pure heuristic: does a process with this ``/proc/<pid>/exe`` realpath
    and ``/proc/<pid>/cmdline`` text look like it is flatpak/bwrap rather
    than a plain interpreter invocation?"""
    lowered_exe = (exe_path or "").lower()
    lowered_cmd = (cmdline_text or "").lower()
    return (
        "bwrap" in lowered_exe
        or "/flatpak" in lowered_exe
        or lowered_exe.endswith("/flatpak")
        or "bwrap" in lowered_cmd
        or "flatpak run" in lowered_cmd
    )


def assert_process_is_not_flatpak(pid: int) -> None:
    """Read (real, but read-only) /proc/<pid>/exe and /proc/<pid>/cmdline
    for ``pid`` and raise :class:`UnsafeBusActivationError` if it looks like
    flatpak/bwrap. Never signals or otherwise touches the process."""
    exe_path = ""
    try:
        exe_path = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    cmdline_text = ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline_text = f.read().replace(b"\x00", b" ").decode(errors="replace")
    except OSError:
        pass
    if is_flatpak_or_bwrap_process(exe_path, cmdline_text):
        raise UnsafeBusActivationError(
            f"pid {pid} answering for the harness's bus name looks like a "
            f"flatpak/bwrap process (exe={exe_path!r}, cmdline={cmdline_text!r}); "
            f"expected the worktree's own python -m whisper_aloud --daemon. "
            f"Refusing to treat it as the test daemon."
        )
