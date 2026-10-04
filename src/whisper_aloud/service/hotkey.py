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

# Shortcut id used for the single toggle-recording accelerator (matches
# readiness.py's own copy of this constant -- kept duplicated rather than
# imported to avoid a cross-module dependency for one string literal).
TOGGLE_SHORTCUT_ID = "toggle"


def _is_wayland() -> bool:
    """Return True if the current session appears to be Wayland."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    return session_type == "wayland" or bool(wayland_display)


def _is_sandboxed() -> bool:
    """Return True inside a Flatpak sandbox (WHISP-59).

    Named/importable separately (like `_try_import_portal`) so tests can
    patch it without touching real env vars/files.
    """
    from whisper_aloud.sandbox import is_flatpak_sandbox

    return is_flatpak_sandbox()


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

    def __init__(
        self, on_state_change: Optional[Callable[[bool, str, str], None]] = None
    ) -> None:
        """Args:
            on_state_change: optional callback invoked with
                ``(bound, trigger, backend)`` whenever the binding state
                actually changes (WHISP-99) -- portal bind result (including
                GNOME's response=2-with-shortcuts quirk), ShortcutsChanged
                recovery, keybinder register/unregister, a backend re-probe
                (WHISP-90), or `unregister()` at shutdown/config change.
                Never called twice in a row with the same tuple. Must never
                raise -- any exception it raises is logged and swallowed, so
                a caller's bug can never break hotkey registration itself.
        """
        self._backend: str = "none"
        self._xdp = None  # portal sentinel / PortalHotkeys class (or mock in tests)
        self._keybinder = None  # Keybinder module reference
        self._portal_hotkeys = None  # PortalHotkeys instance (live session)
        self._registered_accels: List[str] = []
        self._callback: Optional[Callable[[], None]] = None
        self._on_state_change = on_state_change
        self._last_notified_state: Optional[tuple] = None

        self._backend = self.detect_backend()
        # Notify once up front (WHISP-99) so a caller that passed
        # on_state_change always learns the initial state even if
        # `available` is False and register() is therefore never called.
        self._notify_state_change()

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
            if _is_sandboxed():
                logger.info(
                    "Running inside a Flatpak sandbox: portal unavailable and "
                    "libkeybinder3 is not permitted; no automatic hotkey backend"
                )
                return "none"
        except Exception as e:
            logger.debug("Sandbox detection failed: %s", e)

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
        if self._backend == "none":
            logger.warning(
                "No hotkey backend available. Cannot register '%s'. "
                "Please configure a system keyboard shortcut manually.",
                accel,
            )
            return False

        ok = self._register_with_current_backend(accel, callback)
        if not ok:
            # WHISP-90: a registration failure on a backend that was
            # detected as available at __init__/last re-probe is an
            # observable sign the environment changed since then (portal
            # service restarted differently, libkeybinder3 became
            # unusable, ...) -- re-probe once and retry, instead of
            # caching the stale backend for the rest of the daemon's life.
            self._reprobe("registration failed")
            if self._backend != "none":
                ok = self._register_with_current_backend(accel, callback)
        self._notify_state_change()
        return ok

    def _register_with_current_backend(
        self, accel: str, callback: Callable[[], None]
    ) -> bool:
        if self._backend == "portal":
            return self._register_portal(accel, callback)
        if self._backend == "keybinder":
            return self._register_keybinder(accel, callback)
        return False

    def _reprobe(self, reason: str) -> None:
        """Re-detect the backend (WHISP-90) instead of trusting the one
        chosen once at `__init__` for the daemon's entire lifetime -- the
        portal service or libkeybinder3 availability can change while the
        daemon keeps running. Called when `register()` fails on the
        currently selected backend. Never on a timer."""
        old = self._backend
        new = self.detect_backend()
        self._backend = new
        if new != old:
            logger.info("Hotkey backend changed (%s): %s -> %s", reason, old, new)
        else:
            logger.debug("Hotkey backend re-probed (%s): still %s", reason, new)
        self._notify_state_change()

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
        self._notify_state_change()

    @property
    def backend(self) -> str:
        """Currently active backend name."""
        return self._backend

    @property
    def available(self) -> bool:
        """Whether any automatic hotkey backend is available."""
        return self._backend in ("portal", "keybinder")

    def get_bound_trigger(self, shortcut_id: str = "toggle") -> Optional[str]:
        """Human-readable trigger the portal actually bound (e.g.
        'Press <Control>space'), or None if unknown/not using the portal
        backend/not bound yet.

        Only meaningful for the "portal" backend -- the user may have
        picked a different accelerator than our configured preference in
        GNOME's "Add Keyboard Shortcuts" dialog, and GetStatus should
        reflect the real one rather than the configured preference.
        """
        if self._backend != "portal" or self._portal_hotkeys is None:
            return None
        try:
            return self._portal_hotkeys.get_trigger_description(shortcut_id)
        except Exception as e:
            logger.debug("get_bound_trigger failed: %s", e)
            return None

    def is_bound(self, shortcut_id: str = TOGGLE_SHORTCUT_ID) -> bool:
        """Whether the toggle shortcut is actually bound right now.

        The single source of truth `readiness.hotkey_bound()` prefers (see
        that module's docstring: "a future public HotkeyManager.is_bound()
        is used as soon as it exists"). Never raises.
        """
        try:
            if self._backend == "portal":
                portal = self._portal_hotkeys
                return portal is not None and portal.is_bound(shortcut_id) is True
            if self._backend == "keybinder":
                return len(self._registered_accels) > 0
        except Exception:
            logger.debug("is_bound check failed", exc_info=True)
        return False

    def _notify_state_change(self) -> None:
        """Invoke `on_state_change(bound, trigger, backend)` (WHISP-99), but
        only when the tuple actually changed since the last notification --
        so e.g. two no-op `unregister()` calls in a row, or a `register()`
        that merely starts an async portal flow without changing anything
        yet observable, never emit a duplicate signal.

        Called from register()/unregister()/_reprobe() (always on the
        thread that called them -- __init__, SetConfig, or _cleanup, all on
        the daemon's main thread) and from the portal's on_bound callback
        (already dispatched on the main loop by GDBus's own signal
        delivery -- see hotkey_portal.py's module docstring). Never raises:
        this must not break hotkey registration itself if a caller's
        callback misbehaves.
        """
        if self._on_state_change is None:
            return
        try:
            state = (self.is_bound(), self.get_bound_trigger() or "", self._backend)
        except Exception:
            logger.debug("Failed to compute hotkey state for notification", exc_info=True)
            return
        if state == self._last_notified_state:
            return
        self._last_notified_state = state
        try:
            self._on_state_change(*state)
        except Exception:
            logger.debug("on_state_change callback raised", exc_info=True)

    # -- Private backend methods --

    def _register_portal(self, accel: str, callback: Callable[[], None]) -> bool:
        """Register a hotkey using XDG Desktop Portal GlobalShortcuts.

        Instantiates PortalHotkeys (imported from hotkey_portal) and starts
        its async CreateSession -> ListShortcuts -> BindShortcuts-if-needed
        flow (see PortalHotkeys.bind()). Wraps construction in try/except so
        failures fall through cleanly; the flow itself never raises (it
        reports success/failure via on_bound, logged only -- see that
        method's docstring for why this call can't return the real outcome
        synchronously).

        Returns True as soon as the portal backend was selected and the
        (non-blocking) flow was started -- not once binding actually
        finished. BindShortcuts can show an interactive consent dialog and
        now waits indefinitely for the user's response (no nested loop, no
        thread -- see hotkey_portal.py's module docstring), so this must
        never block the caller -- HotkeyManager() is constructed from
        WhisperAloudService.__init__, before the daemon's D-Bus object is
        published (field bug 1/2).
        """
        try:
            from whisper_aloud.service.hotkey_portal import PortalHotkeys

            portal = PortalHotkeys(app_id="org.fede.whisperaloud")

            def _on_activated(shortcut_id: str) -> None:
                if shortcut_id == "toggle":
                    callback()

            def _on_bound(success: bool) -> None:
                if success:
                    logger.info("Portal hotkey bound: %s", accel)
                else:
                    logger.warning("Portal hotkey binding did not succeed for: %s", accel)
                # WHISP-99: this callback also fires for a later
                # ShortcutsChanged recovery (same on_bound, see
                # hotkey_portal.py's bind() docstring), so this is the
                # single place that must notify for both the initial
                # BindShortcuts outcome and any delayed recovery.
                self._notify_state_change()

            portal.bind(
                shortcuts=[
                    {
                        "id": "toggle",
                        "description": "Toggle WhisperAloud recording",
                        "accelerators": [accel],
                    }
                ],
                on_activated=_on_activated,
                on_bound=_on_bound,
            )

            self._portal_hotkeys = portal
            self._callback = callback
            self._registered_accels.append(accel)
            logger.info("Portal hotkey registration started: %s", accel)
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
