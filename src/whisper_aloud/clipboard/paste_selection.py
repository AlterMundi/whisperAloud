"""Sandbox-aware paste backend selection (WHISP-59).

Pure decision logic: inside a Flatpak sandbox there is no ydotool/xdotool
available (and using them would violate the sandbox), so paste must go
through the RemoteDesktop portal; outside the sandbox, today's
ydotool/xdotool-based PasteSimulator is unchanged.

The PortalPasteSimulator instance is cached at module scope so the
RemoteDesktop session it owns is created at most once per daemon
lifetime, as required by WHISP-59 -- not re-created on every paste.
"""

import logging
from typing import Optional

from ..config import ClipboardConfig
from ..sandbox import is_flatpak_sandbox
from .paste_simulator import PasteSimulator
from .portal_paste_simulator import PortalPasteSimulator

logger = logging.getLogger(__name__)

_portal_simulator: Optional[PortalPasteSimulator] = None


def _get_portal_simulator() -> PortalPasteSimulator:
    global _portal_simulator
    if _portal_simulator is None:
        _portal_simulator = PortalPasteSimulator()
    return _portal_simulator


def reset_portal_simulator_for_tests() -> None:
    """Clear the cached PortalPasteSimulator singleton. Tests only."""
    global _portal_simulator
    _portal_simulator = None


def prewarm_portal_paste_backend() -> None:
    """Kick off RemoteDesktop session setup now, if (and only if) the
    portal paste backend is the one that will actually be used (Flatpak
    sandbox) and a persisted restore_token already exists, so setup
    completes without popping a consent dialog.

    Called once by service/daemon.py's run(), via GLib.idle_add right
    after the main loop starts (field bug: the first auto-paste of every
    daemon run was lost to clipboard-only fallback because session setup
    previously only ever started lazily on that first paste). Safe to call
    unconditionally -- a no-op outside the sandbox, or when no
    restore_token is on disk yet (see PortalPasteSimulator.prewarm()).
    """
    if not is_flatpak_sandbox():
        return
    _get_portal_simulator().prewarm()


def close_portal_simulator() -> None:
    """Close the cached PortalPasteSimulator's RemoteDesktop session, if one
    was ever created, and drop the singleton. Called on daemon shutdown
    (service/daemon.py's _cleanup()) so an open session doesn't dangle --
    see PortalPasteSimulator.close() for why that matters. A no-op (never
    raises) if no portal simulator was ever instantiated (the common case
    outside a Flatpak sandbox)."""
    global _portal_simulator
    if _portal_simulator is not None:
        try:
            _portal_simulator.close()
        except Exception:
            logger.debug("close_portal_simulator: close() failed", exc_info=True)
        _portal_simulator = None


class _PortalPasteAdapter:
    """Adapts PortalPasteSimulator to PasteSimulator's public shape
    (`simulate_paste() -> bool`, `check_availability() -> dict`) so
    callers (daemon.py, ClipboardManager) don't need to special-case the
    Flatpak path."""

    def __init__(self, portal: PortalPasteSimulator, config: ClipboardConfig):
        self._portal = portal
        self.config = config
        self.session_type = "portal"

    def simulate_paste(self) -> bool:
        return self._portal.simulate_paste(self.config.paste_shortcut)

    def check_availability(self) -> dict:
        # The portal only prompts for consent on first use; we can't know
        # in advance whether the user will grant it, so report available
        # and let simulate_paste()'s own fallback handle a refusal.
        return {"available": True, "reason": "", "fix": ""}


def build_paste_simulator(
    session_type: str,
    config: ClipboardConfig,
    *,
    is_sandboxed: Optional[bool] = None,
):
    """Return the paste backend appropriate for the current environment.

    Args:
        session_type: 'wayland', 'x11', or 'unknown' (passed through to
            PasteSimulator outside the sandbox).
        config: clipboard configuration (paste_shortcut, timeouts, etc).
        is_sandboxed: override for Flatpak sandbox detection, for tests.
            Defaults to `sandbox.is_flatpak_sandbox()`.
    """
    if is_sandboxed is None:
        is_sandboxed = is_flatpak_sandbox()

    if is_sandboxed:
        return _PortalPasteAdapter(_get_portal_simulator(), config)
    return PasteSimulator(session_type, config)
