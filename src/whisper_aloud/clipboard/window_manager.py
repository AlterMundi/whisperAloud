"""Window management for targeted paste operations."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import Gio for typed D-Bus calls (GNOME backend)
try:
    from gi.repository import Gio, GLib
    _HAS_GIO = True
except ImportError:
    _HAS_GIO = False


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
        result. Never on a timer."""
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
        except Exception:
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
