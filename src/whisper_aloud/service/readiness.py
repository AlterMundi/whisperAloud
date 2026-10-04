"""Readiness facts for GetStatus (WHISP-87): is the global hotkey actually bound?

Kept out of hotkey.py on purpose: it only *reads* the HotkeyManager, and
must never raise into GetStatus. A future public ``HotkeyManager.is_bound()``
is used as soon as it exists; until then the backend's own state is read:

- ``portal``: the GlobalShortcuts session reported the "toggle" shortcut
  bound (BindShortcuts Response or ShortcutsChanged). False while GNOME's
  consent dialog is still open, or after the session was closed.
- ``keybinder``: the accelerator was grabbed.
- anything else (``none``, test mode): False.
"""

import logging

logger = logging.getLogger(__name__)

TOGGLE_SHORTCUT_ID = "toggle"


def hotkey_bound(manager) -> bool:
    if manager is None:
        return False
    try:
        # Looked up on the class so a MagicMock's auto-attributes don't count.
        public = getattr(type(manager), "is_bound", None)
        if callable(public):
            return manager.is_bound() is True
        backend = getattr(manager, "backend", "none")
        if backend == "portal":
            portal = getattr(manager, "_portal_hotkeys", None)
            check = getattr(type(portal), "is_bound", None) if portal is not None else None
            return bool(callable(check) and portal.is_bound(TOGGLE_SHORTCUT_ID) is True)
        if backend == "keybinder":
            accels = getattr(manager, "_registered_accels", None)
            return isinstance(accels, list) and len(accels) > 0
    except Exception:
        logger.debug("hotkey_bound check failed", exc_info=True)
    return False
