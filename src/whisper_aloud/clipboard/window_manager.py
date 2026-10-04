"""Window management for targeted paste operations."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Try to import Gio for typed D-Bus calls (GNOME backend)
try:
    from gi.repository import Gio, GLib
    _HAS_GIO = True
except ImportError:
    _HAS_GIO = False

# org.fede.whisperaloud.Window lives under the extension's own bus name, see
# contrib/gnome-shell-extension/whisperaloud@fede/extension.js.
_EXT_BUS_NAME = "org.gnome.Shell.Extensions.whisperaloud"
_WINDOW_PATH = "/org/fede/whisperaloud/Window"
_WINDOW_IFACE = "org.fede.whisperaloud.Window"

# xdotool backend (WHISP-88): there is no focus-change signal to subscribe
# to, so confirmation falls back to a small BOUNDED number of
# getactivewindow re-queries, logged as a WARNING-level degradation -- never
# a fixed wait used as the success criterion itself. See `confirm_focus`.
_XDOTOOL_CONFIRM_MAX_ATTEMPTS = 3
_XDOTOOL_CONFIRM_INTERVAL_MS = 50


@dataclass
class WindowInfo:
    """Information about a captured window."""
    window_id: int
    title: str
    backend: str  # "gnome-extension", "xdotool", "none"


class WindowManager:
    """Multi-backend window capture and activation.

    Backend priority: gnome-extension -> xdotool -> none.
    """

    def __init__(self) -> None:
        self._gnome_proxy: Optional[object] = None
        self.backend = self._detect_backend()
        logger.info(f"WindowManager initialized with backend: {self.backend}")

    def on_shell_extension_presence_changed(self, present: bool) -> None:
        """Re-probe on a NameOwnerChanged event for the GNOME extension's
        bus name (WHISP-90), instead of only on a failed operation.

        The daemon already owns a single `ShellExtensionWatcher` (see
        `service/shell_extension_watch.py`) for hiding the tray icon --
        rather than this class opening a second, duplicate
        `Gio.bus_watch_name()` on the same name (and, worse, doing so from
        every `WindowManager()` constructed in a plain unit test, which
        would reach the real session bus), the daemon forwards that
        watcher's callback here. Safe to call even when the backend is
        unaffected (xdotool/none): `_reprobe()` only logs a transition if
        one actually happened."""
        self._reprobe(f"GNOME extension {'appeared' if present else 'vanished'}")

    def _reprobe(self, reason: str) -> None:
        """Re-detect the backend (WHISP-90).

        Previously `self.backend` was decided once at `__init__` and cached
        for the daemon's entire lifetime: a user enabling the GNOME
        "whisperaloud" shell extension (or installing xdotool) after the
        daemon started left `get_active_window()`/`activate_window()` stuck
        on whatever was detected at startup until a restart, and -- the
        other direction -- a backend that stopped working (extension
        disabled, xdotool uninstalled) kept being retried forever.

        Called from the backend-specific operations below when one of them
        actually fails (an exception from the D-Bus call or the
        subprocess) -- an unambiguous, observable signal that the
        environment changed, unlike a plain "no active window right now"
        result -- and from `on_shell_extension_presence_changed()` above.
        Never on a timer."""
        old = self.backend
        self._gnome_proxy = None
        new = self._detect_backend()
        self.backend = new
        if new != old:
            logger.info("WindowManager backend changed (%s): %s -> %s", reason, old, new)
        else:
            logger.debug("WindowManager re-probed backend (%s): still %s", reason, new)

    def _detect_backend(self) -> str:
        if self._check_gnome_extension():
            return "gnome-extension"
        if self._check_xdotool():
            return "xdotool"
        return "none"

    def _check_gnome_extension(self) -> bool:
        """Check if GNOME extension D-Bus service is available via typed proxy."""
        if not _HAS_GIO:
            return False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                "org.gnome.Shell.Extensions.whisperaloud",
                "/org/fede/whisperaloud/Window",
                "org.fede.whisperaloud.Window", None,
            )
            res = proxy.call_sync(
                "GetFocusedWindow", None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            if res is not None:
                self._gnome_proxy = proxy
                return True
            return False
        except Exception as e:
            logger.debug(f"_check_gnome_extension probe failed: {e!r}")
            return False

    def _check_xdotool(self) -> bool:
        """Probe xdotool by running actual getactivewindow (not just 'which')."""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_active_window(self) -> Optional[WindowInfo]:
        """Capture the currently focused window."""
        if self.backend == "gnome-extension":
            return self._get_active_gnome()
        elif self.backend == "xdotool":
            return self._get_active_xdotool()
        return None

    def activate_window(self, info: WindowInfo) -> bool:
        """Activate (focus) a previously captured window."""
        if info.backend == "gnome-extension":
            return self._activate_gnome(info)
        elif info.backend == "xdotool":
            return self._activate_xdotool(info)
        return False

    # -- Focus confirmation (WHISP-88) -------------------------------------
    #
    # `activate_window()` above only reports whether the issuing D-Bus
    # call/subprocess succeeded -- never whether the window actually ended
    # up focused. Callers that are about to simulate a paste MUST confirm
    # focus first via `confirm_focus()`, and must not paste at all if
    # confirmation reports False (the window manager found, or could not
    # rule out, that focus landed somewhere else).

    def confirm_focus(
        self,
        expected: WindowInfo,
        callback: Callable[[bool], None],
        timeout_ms: int = 1500,
    ) -> None:
        """Asynchronously confirm that ``expected`` actually gained focus.

        Calls ``callback(True)`` once focus on ``expected.window_id`` is
        confirmed by a real event/observed condition, or ``callback(False)``
        if it cannot be confirmed -- callers must treat ``False`` as "do not
        paste, the focused window may not be the captured one".

        gnome-extension backend: subscribes to the extension's
        ``FocusChanged(s window_id)`` D-Bus signal (emitted from
        ``global.display``'s own ``notify::focus-window``) and resolves as
        soon as an emission names ``expected.window_id`` (True) or a
        *different* id (False, immediately -- a real "focus went elsewhere"
        observation, not a guess). ``timeout_ms`` only bounds how long we
        wait for *any* signal before giving up -- it is never itself the
        success criterion.

        xdotool backend: there is no focus-change signal, so this falls
        back to a small, bounded number of ``getactivewindow`` re-queries
        (see ``_XDOTOOL_CONFIRM_MAX_ATTEMPTS``), logged as a WARNING-level
        degradation.

        Any other backend (``"none"``): nothing to confirm; reports False.
        """
        if expected.backend == "gnome-extension":
            self._confirm_focus_gnome(expected, callback, timeout_ms)
        elif expected.backend == "xdotool":
            self._confirm_focus_xdotool(expected, callback)
        else:
            callback(False)

    def _confirm_focus_gnome(
        self, expected: WindowInfo, callback: Callable[[bool], None], timeout_ms: int
    ) -> None:
        if not _HAS_GIO:
            callback(False)
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as e:
            logger.warning(f"confirm_focus: could not connect to session bus: {e}")
            callback(False)
            return

        state = {"done": False, "sub_id": None, "timer_id": None}

        def _finish(result: bool) -> None:
            if state["done"]:
                return
            state["done"] = True
            if state["sub_id"] is not None:
                try:
                    bus.signal_unsubscribe(state["sub_id"])
                except Exception:
                    pass
            if state["timer_id"] is not None:
                try:
                    GLib.source_remove(state["timer_id"])
                except Exception:
                    pass
            callback(result)

        def _on_signal(_conn, _sender, _path, _iface, _signal, params) -> None:
            try:
                (window_id_str,) = params.unpack()
                focused_id = int(window_id_str)
            except Exception:
                return
            # A signal naming a DIFFERENT window is itself a real, observed
            # "focus went elsewhere" event -- resolve False immediately
            # rather than waiting out the rest of the timeout.
            _finish(focused_id == expected.window_id)

        try:
            state["sub_id"] = bus.signal_subscribe(
                _EXT_BUS_NAME, _WINDOW_IFACE, "FocusChanged", _WINDOW_PATH, None,
                Gio.DBusSignalFlags.NONE, _on_signal,
            )
        except Exception as e:
            logger.warning(f"confirm_focus: could not subscribe to FocusChanged: {e}")
            callback(False)
            return

        def _on_timeout():
            logger.warning(
                f"WHISP-88: no FocusChanged signal confirmed window "
                f"{expected.window_id} focused within {timeout_ms}ms; "
                "degrading to clipboard-only"
            )
            _finish(False)
            return False  # GLib.SOURCE_REMOVE: fire once

        state["timer_id"] = GLib.timeout_add(timeout_ms, _on_timeout)

        # Race guard: ActivateWindow (issued just before this call) may
        # already have landed focus -- and emitted FocusChanged -- before
        # this subscription existed. Check the current state once, now that
        # we are already subscribed (so a signal racing this check is never
        # lost): if it already matches, resolve immediately instead of
        # waiting on a signal that already happened.
        if self._ensure_gnome_proxy():
            try:
                res = self._gnome_proxy.call_sync(
                    "GetFocusedWindow", None, Gio.DBusCallFlags.NONE, 2000, None,
                )
                d = res.get_child_value(0)
                wid_v = d.lookup_value("id", None)
                if wid_v is not None and int(wid_v.unpack()) == expected.window_id:
                    _finish(True)
            except Exception:
                pass

    def _confirm_focus_xdotool(
        self, expected: WindowInfo, callback: Callable[[bool], None]
    ) -> None:
        state = {"attempts": 0}

        def _check() -> bool:
            state["attempts"] += 1
            try:
                result = subprocess.run(
                    ["xdotool", "getactivewindow"], capture_output=True, timeout=2,
                )
            except Exception as e:
                logger.warning(f"xdotool getactivewindow failed during confirm: {e}")
                self._reprobe("xdotool getactivewindow failed during confirm")
                callback(False)
                return False

            if result.returncode == 0:
                try:
                    focused_id = int(result.stdout.decode().strip())
                except ValueError:
                    focused_id = None
                if focused_id == expected.window_id:
                    callback(True)
                    return False

            if state["attempts"] >= _XDOTOOL_CONFIRM_MAX_ATTEMPTS:
                logger.warning(
                    f"WHISP-88: xdotool backend has no focus-change signal; "
                    f"window {expected.window_id} not confirmed focused after "
                    f"{state['attempts']} bounded re-queries -- degrading to "
                    "clipboard-only"
                )
                callback(False)
                return False
            return True  # GLib.SOURCE_CONTINUE: keep polling, bounded above

        if not _HAS_GIO:
            # No GLib main loop available to schedule re-queries on -- do a
            # single immediate check rather than a blind fixed sleep.
            _check()
            return
        GLib.timeout_add(_XDOTOOL_CONFIRM_INTERVAL_MS, _check)

    # -- GNOME Extension backend (Gio.DBusProxy typed calls) --------------

    def _ensure_gnome_proxy(self) -> bool:
        """Ensure GNOME D-Bus proxy is connected."""
        if self._gnome_proxy is not None:
            return True
        if not _HAS_GIO:
            return False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._gnome_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                "org.gnome.Shell.Extensions.whisperaloud",
                "/org/fede/whisperaloud/Window",
                "org.fede.whisperaloud.Window", None,
            )
            return True
        except Exception:
            return False

    def _get_active_gnome(self) -> Optional[WindowInfo]:
        """Get active window via GNOME extension D-Bus typed proxy."""
        if not self._ensure_gnome_proxy():
            self._reprobe("GNOME proxy unavailable")
            return None
        try:
            res = self._gnome_proxy.call_sync(
                "GetFocusedWindow", None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            d = res.get_child_value(0)
            wid_v = d.lookup_value("id", None)
            if wid_v is None:
                return None
            title_v = d.lookup_value("title", None)
            return WindowInfo(
                window_id=int(wid_v.unpack()),
                title=title_v.unpack() if title_v else "",
                backend="gnome-extension",
            )
        except Exception as e:
            logger.warning(f"GNOME GetFocusedWindow failed: {e}")
            # WHISP-90: a real failure (not just "no focused window"), e.g.
            # the extension was disabled since __init__ -- re-probe instead
            # of retrying the same dead backend forever.
            self._reprobe("GetFocusedWindow failed")
            return None

    def _activate_gnome(self, info: WindowInfo) -> bool:
        """Activate window via GNOME extension D-Bus typed proxy."""
        if not self._ensure_gnome_proxy():
            self._reprobe("GNOME proxy unavailable")
            return False
        try:
            v = GLib.Variant("(u)", (int(info.window_id),))
            self._gnome_proxy.call_sync(
                "ActivateWindow", v,
                Gio.DBusCallFlags.NONE, 3000, None,
            )
            logger.info(f"Activated window {info.window_id} via GNOME extension")
            return True
        except Exception as e:
            logger.warning(f"GNOME ActivateWindow failed: {e}")
            self._reprobe("ActivateWindow failed")
            return False

    # -- xdotool backend --------------------------------------------------

    def _get_active_xdotool(self) -> Optional[WindowInfo]:
        """Get active window via xdotool."""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )
            if result.returncode != 0:
                return None
            window_id = int(result.stdout.decode().strip())
            title = ""
            try:
                name_result = subprocess.run(
                    ["xdotool", "getwindowname", str(window_id)],
                    capture_output=True, timeout=2,
                )
                if name_result.returncode == 0:
                    title = name_result.stdout.decode().strip()
            except Exception:
                pass
            return WindowInfo(window_id=window_id, title=title, backend="xdotool")
        except Exception as e:
            logger.warning(f"xdotool getactivewindow failed: {e}")
            # WHISP-90: an exception (e.g. FileNotFoundError -- the binary
            # was uninstalled since __init__) is a real backend failure,
            # unlike a non-zero exit (which can just mean "no active
            # window right now") -- re-probe so the next call doesn't
            # retry a backend that is actually gone.
            self._reprobe("xdotool getactivewindow failed")
            return None

    def _activate_xdotool(self, info: WindowInfo) -> bool:
        """Activate window via xdotool."""
        try:
            result = subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(info.window_id)],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                logger.info(f"Activated window {info.window_id} via xdotool")
                return True
            logger.warning(f"xdotool windowactivate failed: {result.stderr.decode()}")
            return False
        except Exception as e:
            logger.warning(f"xdotool windowactivate failed: {e}")
            self._reprobe("xdotool windowactivate failed")
            return False
