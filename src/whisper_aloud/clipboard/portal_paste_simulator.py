"""RemoteDesktop portal text insertion for Flatpak sandboxes (WHISP-59).

Inside a Flatpak sandbox there is no ydotool/xdotool: the only sanctioned
way to synthesize a keypress is the ``org.freedesktop.portal.RemoteDesktop``
portal. This mirrors the Request/Response + bounded-timeout pattern already
used by ``service/hotkey_portal.py`` for GlobalShortcuts (see WHISP-64).

Portal call sequence (xdg-desktop-portal RemoteDesktop interface):

1. ``CreateSession(options)`` -- Request/Response, returns a session handle.
2. ``SelectDevices(session_handle, options)`` -- Request/Response. We ask
   for ``types=DEVICE_KEYBOARD`` only (never pointer/touchscreen -- we only
   need to send a paste shortcut) and ``persist_mode=PERSIST_PERSISTENT``
   so the user is asked for consent once; a ``restore_token`` from a prior
   run is passed back in to skip the prompt entirely when the portal
   still recognizes it.
3. ``Start(session_handle, parent_window, options)`` -- Request/Response.
   On success, the Response results may contain a *new* ``restore_token``,
   which we persist for the next run.
4. ``NotifyKeyboardKeysym(session_handle, options, keysym, state)`` -- a
   plain (non-Request) method call, fire-and-forget, one call per key
   press/release in the sequence from ``portal_keysyms``.

The session (steps 1-3) is created lazily on the first paste and kept for
the rest of the daemon's lifetime -- never recreated speculatively, since
every CreateSession/SelectDevices/Start can pop a consent dialog. If any
of those three ever fails, the simulator marks itself permanently
unavailable for this process (never retries session creation) and callers
must fall back to clipboard-only. A later NotifyKeyboardKeysym failure
does not pop a dialog, so it does not poison the session -- only that one
paste attempt fails.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Callable, Optional

from .portal_keysyms import keysym_sequence_for_shortcut

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_IFACE_RD = "org.freedesktop.portal.RemoteDesktop"
_IFACE_REQ = "org.freedesktop.portal.Request"
_IFACE_REGISTRY = "org.freedesktop.host.portal.Registry"

# RemoteDesktop DeviceType bitmask (portal spec).
DEVICE_KEYBOARD = 1

# RemoteDesktop persist_mode values (portal spec, v2+).
PERSIST_MODE_NONE = 0
PERSIST_MODE_APPLICATION = 1
PERSIST_MODE_PERSISTENT = 2

_RESTORE_TOKEN_FILENAME = "portal_restore_token"


def _restore_token_path() -> Path:
    """XDG_DATA_HOME/whisper_aloud/portal_restore_token.

    XDG_DATA_HOME is read at call time (not import time) so tests that set
    it via monkeypatch/conftest isolation are always honored.
    """
    xdg_data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg_data_home) / "whisper_aloud" / _RESTORE_TOKEN_FILENAME


def load_restore_token() -> Optional[str]:
    """Return the persisted restore_token, or None if absent/unreadable."""
    try:
        path = _restore_token_path()
        if not path.exists():
            return None
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    except OSError as e:
        logger.debug("Could not read portal restore_token: %s", e)
        return None


def save_restore_token(token: str) -> None:
    """Persist `token` so the next daemon run skips the consent prompt."""
    if not token:
        return
    try:
        path = _restore_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to persist portal restore_token: %s", e)


class PortalPasteSimulator:
    """Pastes via the RemoteDesktop portal's keyboard emulation.

    One session is created lazily on the first `simulate_paste()` call and
    reused for the rest of this object's lifetime (intended to be one
    instance per daemon lifetime -- see `clipboard.paste_selection`).
    """

    def __init__(
        self,
        app_id: str = "org.fede.whisperaloud",
        timeout_ms: int = 5000,
        on_fallback: Optional[Callable[[str], None]] = None,
        restore_token_loader: Callable[[], Optional[str]] = load_restore_token,
        restore_token_saver: Callable[[str], None] = save_restore_token,
    ):
        """Construction never touches D-Bus -- the portal session is
        created lazily on the first `simulate_paste()` call, not here."""
        self._app_id = app_id
        self._timeout_ms = timeout_ms
        self._on_fallback = on_fallback
        self._load_restore_token = restore_token_loader
        self._save_restore_token = restore_token_saver

        self._Gio = None
        self._GLib = None
        self._bus = None
        self._proxy = None
        self._session_path = None
        self._session_failed = False
        self._pending_handle_token: Optional[str] = None

    @staticmethod
    def _new_token(prefix: str) -> str:
        return f"wa_{prefix}_{uuid.uuid4().hex}"

    def _connect(self) -> None:
        from gi.repository import Gio, GLib  # noqa: F401 -- lazy/guarded import

        self._Gio = Gio
        self._GLib = GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        # Must happen before any other portal call on this connection (same
        # handshake as service.hotkey_portal.PortalHotkeys._register_app_id,
        # see WHISP-64): associates this connection with a stable app_id so
        # persist_mode/restore_token actually survive across daemon
        # restarts instead of being scoped to a one-off systemd scope name.
        # Safe to fail: portals older than 1.19 simply lack this interface.
        try:
            self._bus.call_sync(
                _PORTAL_BUS,
                _PORTAL_PATH,
                _IFACE_REGISTRY,
                "Register",
                GLib.Variant.new_tuple(
                    GLib.Variant("s", self._app_id),
                    GLib.Variant("a{sv}", {}),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
        except Exception as e:
            logger.debug("Registry.Register unavailable or failed (pre-1.19 portal?): %s", e)

        self._proxy = Gio.DBusProxy.new_sync(
            self._bus,
            Gio.DBusProxyFlags.NONE,
            None,
            _PORTAL_BUS,
            _PORTAL_PATH,
            _IFACE_RD,
            None,
        )

    def _call_request(self, method_name: str, args, timeout_ms: Optional[int] = None):
        """Invoke a Request-pattern portal method and block for its Response.

        Same bounded-timeout Request/Response pattern as
        `service.hotkey_portal.PortalHotkeys._call_request`. Never raises
        on timeout -- returns (None, None).
        """
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806
        timeout_ms = self._timeout_ms if timeout_ms is None else timeout_ms

        handle_token = self._pending_handle_token
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        expected_req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

        outcome = {"response": None, "results": None}
        loop = GLib.MainLoop()
        sub_id = [None]

        def _on_response(_conn, _sender, _path, _iface, _signal, params):
            try:
                response, results = params.unpack()
                outcome["response"] = response
                outcome["results"] = results
            finally:
                if sub_id[0] is not None:
                    self._bus.signal_unsubscribe(sub_id[0])
                    sub_id[0] = None
                loop.quit()

        sub_id[0] = self._bus.signal_subscribe(
            None,
            _IFACE_REQ,
            "Response",
            expected_req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_response,
        )

        self._proxy.call_sync(method_name, args, Gio.DBusCallFlags.NONE, timeout_ms, None)

        timeout_fired = [False]

        def _timeout():
            timeout_fired[0] = True
            logger.warning(
                "%s timed out waiting for Response on %s", method_name, expected_req_path
            )
            loop.quit()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add(timeout_ms, _timeout)
        loop.run()
        if not timeout_fired[0]:
            GLib.source_remove(timeout_id)
        elif sub_id[0] is not None:
            self._bus.signal_unsubscribe(sub_id[0])
            sub_id[0] = None

        return outcome["response"], outcome["results"]

    def _create_session(self) -> None:
        GLib = self._GLib  # noqa: N806

        session_token = self._new_token("session")
        handle_token = self._new_token("req")
        self._pending_handle_token = handle_token

        opts = GLib.Variant(
            "a{sv}",
            {
                "session_handle_token": GLib.Variant("s", session_token),
                "handle_token": GLib.Variant("s", handle_token),
            },
        )
        response, results = self._call_request("CreateSession", GLib.Variant.new_tuple(opts))

        session = None
        if response == 0 and isinstance(results, dict):
            v = results.get("session_handle")
            if v is not None:
                session = v.unpack() if hasattr(v, "unpack") else v

        if response != 0 or not session:
            raise RuntimeError(f"RemoteDesktop CreateSession failed (response={response})")
        self._session_path = session

    def _select_devices(self, restore_token: Optional[str]) -> None:
        GLib = self._GLib  # noqa: N806

        handle_token = self._new_token("req")
        self._pending_handle_token = handle_token

        opts_dict = {
            "handle_token": GLib.Variant("s", handle_token),
            "types": GLib.Variant("u", DEVICE_KEYBOARD),
            "persist_mode": GLib.Variant("u", PERSIST_MODE_PERSISTENT),
        }
        if restore_token:
            opts_dict["restore_token"] = GLib.Variant("s", restore_token)
        opts = GLib.Variant("a{sv}", opts_dict)

        response, _results = self._call_request(
            "SelectDevices",
            GLib.Variant.new_tuple(GLib.Variant("o", self._session_path), opts),
        )
        if response != 0:
            raise RuntimeError(f"RemoteDesktop SelectDevices failed (response={response})")

    def _start(self) -> Optional[str]:
        """Returns the new restore_token, if the portal issued one."""
        GLib = self._GLib  # noqa: N806

        handle_token = self._new_token("req")
        self._pending_handle_token = handle_token
        opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})

        response, results = self._call_request(
            "Start",
            GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_path),
                GLib.Variant("s", ""),  # parent_window
                opts,
            ),
        )
        if response != 0:
            raise RuntimeError(f"RemoteDesktop Start failed (response={response})")

        new_token = None
        if isinstance(results, dict):
            v = results.get("restore_token")
            if v is not None:
                new_token = v.unpack() if hasattr(v, "unpack") else v
        return new_token

    def _ensure_session(self) -> bool:
        if self._session_path is not None:
            return True
        if self._session_failed:
            return False

        try:
            self._connect()
            self._create_session()
            self._select_devices(self._load_restore_token())
            new_token = self._start()
            if new_token:
                self._save_restore_token(new_token)
            logger.info("RemoteDesktop portal session ready for paste")
            return True
        except Exception as e:
            logger.warning("RemoteDesktop portal session setup failed: %s", e)
            self._session_failed = True
            self._session_path = None
            self._emit_fallback(str(e))
            return False

    def _notify_keysym(self, keysym: int, state: int) -> None:
        GLib = self._GLib  # noqa: N806
        Gio = self._Gio  # noqa: N806
        opts = GLib.Variant("a{sv}", {})
        self._proxy.call_sync(
            "NotifyKeyboardKeysym",
            GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_path),
                opts,
                GLib.Variant("i", keysym),
                GLib.Variant("u", state),
            ),
            Gio.DBusCallFlags.NONE,
            self._timeout_ms,
            None,
        )

    def _emit_fallback(self, reason: str) -> None:
        logger.warning("Falling back to clipboard-only paste: %s", reason)
        if callable(self._on_fallback):
            try:
                self._on_fallback(reason)
            except Exception:
                logger.exception("on_fallback callback raised")

    def simulate_paste(self, paste_shortcut: Optional[str] = "ctrl+v") -> bool:
        """Send the paste shortcut via NotifyKeyboardKeysym.

        Returns True on success, False on any failure (never raises). A
        session-setup failure is permanent for this instance's lifetime; a
        NotifyKeyboardKeysym failure only fails this one call.
        """
        if not self._ensure_session():
            return False

        try:
            for keysym, state in keysym_sequence_for_shortcut(paste_shortcut):
                self._notify_keysym(keysym, state)
            return True
        except Exception as e:
            logger.warning("RemoteDesktop NotifyKeyboardKeysym failed: %s", e)
            self._emit_fallback(str(e))
            return False
