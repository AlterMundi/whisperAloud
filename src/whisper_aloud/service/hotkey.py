"""Global hotkey manager with 3-level backend fallback.

Backend detection order:
1. XDG Desktop Portal (GlobalShortcuts) -- GNOME 46+, KDE 6+, Wayland
2. libkeybinder3 -- X11, any desktop
3. D-Bus only (none) -- user configures system shortcut manually
"""

import logging
import os
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


def _is_wayland() -> bool:
    """Return True if the current session appears to be Wayland."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    return session_type == "wayland" or bool(wayland_display)


def _try_import_portal():
    """Check whether the XDG Desktop Portal GlobalShortcuts backend is usable.

    Returns a truthy sentinel object (the PortalHotkeys class) when the portal
    D-Bus service is available on a Wayland session, or None otherwise.

    Keeping this as a named function allows tests to patch it.
    """
    if not _is_wayland():
        logger.debug("Not a Wayland session; skipping portal backend")
        return None
    try:
        from whisper_aloud.service.hotkey_portal import PortalHotkeys

        if PortalHotkeys.portal_available():
            return PortalHotkeys
        logger.debug("XDG Portal D-Bus name not owned; skipping portal backend")
        return None
    except Exception as e:
        logger.debug("Portal detection failed: %s", e)
        return None


def _try_import_keybinder():
    """Try to import Keybinder (libkeybinder3) for X11 hotkeys.

    Returns the Keybinder module or None.
    """
    try:
        import gi

        gi.require_version("Keybinder", "3.0")
        from gi.repository import Keybinder

        return Keybinder
    except (ImportError, ValueError, TypeError, Exception) as e:
        logger.debug("libkeybinder3 not available: %s", e)
        return None


class HotkeyManager:
    """Global hotkey manager with 3-level backend fallback.

    Provides a unified interface for registering global hotkeys using
    the best available backend:
    - "portal": XDG Desktop Portal GlobalShortcuts (Wayland-native)
    - "keybinder": libkeybinder3 (X11)
    - "none": no automatic hotkey support; user must configure manually
    """

    def __init__(self) -> None:
        self._backend: str = "none"
        self._xdp = None  # portal sentinel / PortalHotkeys class (or mock in tests)
        self._keybinder = None  # Keybinder module reference
        self._portal_hotkeys = None  # PortalHotkeys instance (live session)
        self._registered_accels: List[str] = []
        self._callback: Optional[Callable[[], None]] = None

        self._backend = self.detect_backend()

    def detect_backend(self) -> str:
        """Detect best available backend.

        Tries portal first (Wayland only), then keybinder, then falls back to "none".
        Never raises an exception.

        Returns:
            "portal", "keybinder", or "none"
        """
        try:
            portal_cls = _try_import_portal()
            if portal_cls is not None:
                self._xdp = portal_cls
                logger.info("Using XDG Desktop Portal (GlobalShortcuts) backend")
                return "portal"
        except Exception as e:
            logger.debug("Portal detection failed: %s", e)

        try:
            kb = _try_import_keybinder()
            if kb is not None:
                self._keybinder = kb
                kb.init()
                logger.info("Using libkeybinder3 (X11) backend")
                return "keybinder"
        except Exception as e:
            logger.debug("Keybinder detection failed: %s", e)

        logger.info(
            "No automatic hotkey backend available. "
            "Configure a system keyboard shortcut to send D-Bus signal "
            "to org.fede.whisperaloud.Control.ToggleRecording"
        )
        return "none"

    def register(self, accel: str, callback: Callable[[], None]) -> bool:
        """Register a global hotkey.

        Args:
            accel: GTK-style accelerator string, e.g. "<Super><Alt>r"
            callback: Function to call when the hotkey is pressed.

        Returns:
            True if the hotkey was registered successfully, False otherwise.
        """
        if self._backend == "portal":
            return self._register_portal(accel, callback)
        elif self._backend == "keybinder":
            return self._register_keybinder(accel, callback)
        else:
            logger.warning(
                "No hotkey backend available. Cannot register '%s'. "
                "Please configure a system keyboard shortcut manually.",
                accel,
            )
            return False

    def unregister(self) -> None:
        """Unregister all hotkeys. Idempotent and never raises."""
        try:
            if self._backend == "keybinder" and self._keybinder is not None:
                for accel in self._registered_accels:
                    try:
                        self._keybinder.unbind(accel)
                    except Exception as e:
                        logger.debug("Failed to unbind '%s': %s", accel, e)
            elif self._backend == "portal" and self._portal_hotkeys is not None:
                try:
                    self._portal_hotkeys.close()
                except Exception as e:
                    logger.debug("Error closing portal session: %s", e)
                self._portal_hotkeys = None
        except Exception as e:
            logger.debug("Error during unregister: %s", e)
        finally:
            self._registered_accels.clear()
            self._callback = None

    @property
    def backend(self) -> str:
        """Currently active backend name."""
        return self._backend

    @property
    def available(self) -> bool:
        """Whether any automatic hotkey backend is available."""
        return self._backend in ("portal", "keybinder")

    # -- Private backend methods --

    def _register_portal(self, accel: str, callback: Callable[[], None]) -> bool:
        """Register a hotkey using XDG Desktop Portal GlobalShortcuts.

        Instantiates PortalHotkeys (imported from hotkey_portal), creates a
        portal session, and binds the "toggle" shortcut to the given accelerator.
        Wraps everything in try/except so failures fall through cleanly.
        """
        try:
            from whisper_aloud.service.hotkey_portal import PortalHotkeys

            portal = PortalHotkeys(app_id="org.fede.whisperaloud")
            portal.create_session()

            def _on_activated(shortcut_id: str) -> None:
                if shortcut_id == "toggle":
                    callback()

            portal.bind_shortcuts(
                shortcuts=[
                    {
                        "id": "toggle",
                        "description": "Toggle WhisperAloud recording",
                        "accelerators": [accel],
                    }
                ],
                on_activated=_on_activated,
            )

            self._portal_hotkeys = portal
            self._callback = callback
            self._registered_accels.append(accel)
            logger.info("Portal hotkey registered: %s", accel)
            return True
        except Exception as e:
            logger.error("Failed to register portal hotkey '%s': %s", accel, e)
            return False

    def _register_keybinder(self, accel: str, callback: Callable[[], None]) -> bool:
        """Register a hotkey using libkeybinder3."""
        try:
            # Keybinder.bind passes (keystring, user_data) to callback;
            # wrap to match our Callable[[], None] signature.
            def _kb_callback(keystring, user_data=None):
                callback()

            self._keybinder.bind(accel, _kb_callback)
            self._registered_accels.append(accel)
            self._callback = callback
            logger.info("Keybinder hotkey registered: %s", accel)
            return True
        except Exception as e:
            logger.error("Failed to register keybinder hotkey '%s': %s", accel, e)
            return False
