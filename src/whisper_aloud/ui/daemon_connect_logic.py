"""Pure decision/backoff logic for the GUI's daemon-connection lifecycle (WHISP-60).

WhisperAloud's daemon is started through D-Bus activation: the GUI's first
D-Bus call against org.fede.whisperaloud (see service/client.py's
WhisperAloudClient._try_connect(), which calls pydbus's ``bus.get()``) is
enough to make dbus-daemon auto-start the service if it isn't already running
and an activation file is installed (see data/org.fede.whisperaloud.service).
The GUI never spawns a daemon subprocess itself.

Activation can still fail -- most commonly in a dev checkout where the
.service file isn't installed under a directory dbus-daemon scans. This
module decides, *without touching GTK, D-Bus, or threading*, what MainWindow
should do after each attempt: retry automatically with exponential backoff,
or give up and show the "daemon unavailable" state with a manual retry
button. Keeping this logic GTK-free is what makes it unit-testable (see
tests/test_daemon_connect_logic.py) independent of a display or a bus.
"""

from dataclasses import dataclass
from enum import Enum

DEFAULT_MAX_AUTO_RETRIES = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 8.0


class ConnectPhase(str, Enum):
    """Where the GUI's connection attempt stands after `decide_after_attempt`."""

    CONNECTED = "connected"
    WAITING_RETRY = "waiting_retry"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ConnectDecision:
    """What MainWindow should do next, and the attempt number it reflects."""

    phase: ConnectPhase
    attempt: int
    retry_delay_seconds: float = 0.0


def backoff_delay_seconds(
    attempt: int,
    *,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
) -> float:
    """Exponential backoff delay before the Nth automatic retry (1-indexed).

    attempt=1 -> base_delay, attempt=2 -> base_delay*2, attempt=3 -> base_delay*4,
    ... capped at max_delay so a long string of failures doesn't wait forever
    between tries.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    delay = base_delay * (2 ** (attempt - 1))
    return min(delay, max_delay)


def decide_after_attempt(
    *,
    connected: bool,
    attempt: int,
    max_auto_retries: int = DEFAULT_MAX_AUTO_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
) -> ConnectDecision:
    """Decide what the GUI should do after one daemon-connection attempt.

    `attempt` is the 1-indexed number of the attempt that just completed (1
    for the first try at startup or after a manual retry). When `connected`
    is True the phase is always CONNECTED regardless of attempt count. When
    False, attempts below `max_auto_retries` schedule another automatic try
    with an exponentially growing delay; once the budget is exhausted the
    phase is UNAVAILABLE, meaning the GUI should stop retrying on its own and
    wait for the user to click "Retry Connection".
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")

    if connected:
        return ConnectDecision(phase=ConnectPhase.CONNECTED, attempt=attempt)

    if attempt >= max_auto_retries:
        return ConnectDecision(phase=ConnectPhase.UNAVAILABLE, attempt=attempt)

    delay = backoff_delay_seconds(attempt, base_delay=base_delay, max_delay=max_delay)
    return ConnectDecision(
        phase=ConnectPhase.WAITING_RETRY, attempt=attempt, retry_delay_seconds=delay
    )
