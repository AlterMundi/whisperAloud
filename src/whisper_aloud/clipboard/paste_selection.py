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
