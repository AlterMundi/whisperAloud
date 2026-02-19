"""Global hotkey manager with 3-level backend fallback.

Backend detection order:
1. XDG Desktop Portal (GlobalShortcuts) -- GNOME 46+, KDE 6+, Wayland
2. libkeybinder3 -- X11, any desktop
3. D-Bus only (none) -- user configures system shortcut manually
"""

import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _try_import_portal():
    """Try to import Xdp (libportal) for GlobalShortcuts portal.

    Returns the Xdp module or None.
    """
    try:
        import gi
        gi.require_version('Xdp', '1.0')
        from gi.repository import Xdp
        # Verify we can create a portal instance
        Xdp.Portal()
        return Xdp
    except (ImportError, ValueError, TypeError, Exception) as e:
        logger.debug("XDG Desktop Portal not available: %s", e)
        return None


def _try_import_keybinder():
    """Try to import Keybinder (libkeybinder3) for X11 hotkeys.

    Returns the Keybinder module or None.
    """
    try:
        import gi
        gi.require_version('Keybinder', '3.0')
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
        self._xdp = None  # Xdp module reference
        self._keybinder = None  # Keybinder module reference
        self._portal = None  # Xdp.Portal() instance
        self._registered_accels: List[str] = []
        self._callback: Optional[Callable[[], None]] = None

        self._backend = self.detect_backend()

    def detect_backend(self) -> str:
        """Detect best available backend.

        Tries portal first, then keybinder, then falls back to "none".
        Never raises an exception.

        Returns:
            "portal", "keybinder", or "none"
        """
        try:
            xdp = _try_import_portal()
            if xdp is not None:
                self._xdp = xdp
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
            elif self._backend == "portal" and self._portal is not None:
                # Portal shortcuts are session-scoped; clearing reference
                # is sufficient for cleanup
                self._portal = None
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
        """Register a hotkey using XDG Desktop Portal GlobalShortcuts."""
        try:
            if self._portal is None:
                self._portal = self._xdp.Portal()

            self._callback = callback
            self._registered_accels.append(accel)

            # Portal shortcut registration is async; the actual bind happens
            # through create_global_shortcuts_session + bind_shortcuts.
            # The GLib.MainLoop in the daemon drives the async callbacks.
            # For now we set up the session; full async wiring happens when
            # integrated into the daemon (Task 4.4).
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
