"""Paste backend selection (WHISP-59, WHISP-119).

Pure decision logic for how auto-paste puts the transcription into the
focused app (the text is always copied to the clipboard first):

- ``extension``: the GNOME Shell extension's ``TextInput.InsertText``
  (``clipboard/text_insertion.py``) -- IM commit or an in-Shell paste
  chord; no portal, no indicator. Selected first whenever the extension is
  present and its ``GetCapabilities`` reports ``insert_text``.
- ``keystroke``: ydotool/xdotool (PasteSimulator). Native installs only --
  inside a Flatpak sandbox these tools are unavailable.
- ``remote-desktop``: the xdg RemoteDesktop portal (PortalPasteSimulator),
  the 0.3.0 method. Opt-in only (``clipboard.paste_backend =
  "remote-desktop"``, the ADR 0001 rollback switch): GNOME shows a
  permanent "remote access" indicator while its session is open.
- ``clipboard``: no paste; the user is told to press Ctrl+V.

``clipboard.paste_backend = "auto"`` (default) means extension, else
keystroke natively / clipboard in Flatpak -- never remote-desktop, and
then no RemoteDesktop session is ever opened (no prewarm either).

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

EXTENSION = "extension"
KEYSTROKE = "keystroke"
REMOTE_DESKTOP = "remote-desktop"
CLIPBOARD = "clipboard"

_portal_simulator: Optional[PortalPasteSimulator] = None


def select_paste_backend(
    paste_backend: str,
    *,
    is_sandboxed: bool,
    extension_can_insert: bool,
) -> str:
    """Return EXTENSION, KEYSTROKE, REMOTE_DESKTOP or CLIPBOARD.

    ``paste_backend`` is ``ClipboardConfig.paste_backend``; an unknown value
    (validation normally prevents it) is treated as "auto".
    ``extension_can_insert``: the extension owns its bus name and its
    ``GetCapabilities`` reported ``insert_text`` (see
    ``ExtensionTextInserter.can_insert``).
    """
    if paste_backend == REMOTE_DESKTOP:
        return REMOTE_DESKTOP
    if paste_backend == CLIPBOARD:
        return CLIPBOARD
    if extension_can_insert:
        return EXTENSION
    if paste_backend == EXTENSION or is_sandboxed:
        return CLIPBOARD
    return KEYSTROKE


def _get_portal_simulator() -> PortalPasteSimulator:
    global _portal_simulator
    if _portal_simulator is None:
        _portal_simulator = PortalPasteSimulator()
    return _portal_simulator


def reset_portal_simulator_for_tests() -> None:
    """Clear the cached PortalPasteSimulator singleton. Tests only."""
    global _portal_simulator
    _portal_simulator = None


def prewarm_portal_paste_backend(config: ClipboardConfig) -> None:
    """Kick off RemoteDesktop session setup now, if (and only if) the
    portal paste backend was explicitly opted in
    (``config.paste_backend == "remote-desktop"``, WHISP-119) and a
    persisted restore_token already exists, so setup completes without
    popping a consent dialog. With any other setting no PortalPasteSimulator
    is created and no RemoteDesktop session is opened, so GNOME never shows
    its remote-access indicator because of us.

    Called once by service/daemon.py's run(), via GLib.idle_add right
    after the main loop starts (field bug: the first auto-paste of every
    daemon run was lost to clipboard-only fallback because session setup
    previously only ever started lazily on that first paste). Safe to call
    unconditionally -- a no-op unless opted in, or when no restore_token
    is on disk yet (see PortalPasteSimulator.prewarm()).
    """
    if getattr(config, "paste_backend", "auto") != REMOTE_DESKTOP:
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
    backend: Optional[str] = None,
):
    """Return the keystroke-sending simulator for a paste backend, or None
    when that backend does not use one (EXTENSION, CLIPBOARD).

    Args:
        session_type: 'wayland', 'x11', or 'unknown' (passed through to
            PasteSimulator).
        config: clipboard configuration (paste_shortcut, timeouts, etc).
        is_sandboxed: override for Flatpak sandbox detection, for tests.
            Defaults to `sandbox.is_flatpak_sandbox()`. Only used when
            ``backend`` is not given.
        backend: a `select_paste_backend()` result. When omitted it is
            derived from ``config.paste_backend`` as if the extension were
            unavailable.
    """
    if backend is None:
        if is_sandboxed is None:
            is_sandboxed = is_flatpak_sandbox()
        backend = select_paste_backend(
            getattr(config, "paste_backend", "auto"),
            is_sandboxed=is_sandboxed,
            extension_can_insert=False,
        )

    if backend == REMOTE_DESKTOP:
        return _PortalPasteAdapter(_get_portal_simulator(), config)
    if backend == KEYSTROKE:
        return PasteSimulator(session_type, config)
    return None
