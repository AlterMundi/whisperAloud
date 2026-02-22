"""XDG Portal GlobalShortcuts backend for Wayland global hotkeys.

Talks directly to org.freedesktop.portal.GlobalShortcuts over D-Bus
using Gio.DBusProxy — no dependency on libportal GI bindings.

Supported compositors (as of 2026): KDE Plasma 6+, GNOME (partial),
wlroots-based compositors (partial). Always falls back gracefully.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_IFACE_GS = "org.freedesktop.portal.GlobalShortcuts"
_IFACE_REQ = "org.freedesktop.portal.Request"


class PortalHotkeys:
    def __init__(self, app_id: str = "org.fede.whisperaloud"):
        from gi.repository import Gio, GLib  # noqa: F401 — imported here for lazy/guarded load

        self._Gio = Gio
        self._GLib = GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._proxy = Gio.DBusProxy.new_sync(
            self._bus,
            Gio.DBusProxyFlags.NONE,
            None,
            _PORTAL_BUS,
            _PORTAL_PATH,
            _IFACE_GS,
            None,
        )
        self._app_id = app_id
        self._session_path = None
        self._activated_cb = None
        self._req_sub_id = None
        self._act_sub_id = None

    @staticmethod
    def _new_token(prefix: str) -> str:
        return f"wa_{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def portal_available() -> bool:
        """Return True if the GlobalShortcuts portal interface is available."""
        try:
            from gi.repository import Gio, GLib

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            conn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant.new_tuple(GLib.Variant("s", _PORTAL_BUS)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            return True
        except Exception:
            return False

    def create_session(self) -> None:
        """Create a GlobalShortcuts session via Request::Response flow."""
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806

        session_token = self._new_token("session")
        handle_token = self._new_token("req")
        done = {"ok": False}
        loop = GLib.MainLoop()

        # Predict the request path from our sender name + handle_token
        # so we can subscribe BEFORE the call (the portal may respond instantly).
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        expected_req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

        def _on_response(_conn, _sender, _path, _iface, _signal, params):
            try:
                response, results = params.unpack()
                session = None
                if isinstance(results, dict):
                    for key in ("session_handle", "session"):
                        v = results.get(key)
                        if v is not None:
                            session = v.unpack() if hasattr(v, "unpack") else v
                            break
                if response == 0 and session:
                    self._session_path = session
                    done["ok"] = True
                    logger.info("Portal session created: %s", self._session_path)
                else:
                    logger.warning(
                        "CreateSession failed/cancelled (response=%s)", response
                    )
            finally:
                if self._req_sub_id is not None:
                    self._bus.signal_unsubscribe(self._req_sub_id)
                    self._req_sub_id = None
                loop.quit()

        # Subscribe BEFORE calling CreateSession to avoid race
        self._req_sub_id = self._bus.signal_subscribe(
            None,
            _IFACE_REQ,
            "Response",
            expected_req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_response,
        )

        opts = GLib.Variant(
            "a{sv}",
            {
                "app_id": GLib.Variant("s", self._app_id),
                "session_handle_token": GLib.Variant("s", session_token),
                "handle_token": GLib.Variant("s", handle_token),
            },
        )
        self._proxy.call_sync(
            "CreateSession",
            GLib.Variant.new_tuple(opts),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

        # Timeout after 5s to avoid blocking forever
        timeout_fired = [False]

        def _timeout():
            timeout_fired[0] = True
            logger.warning("CreateSession timed out waiting for Response on %s", expected_req_path)
            loop.quit()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add(5000, _timeout)
        logger.debug("Waiting for CreateSession Response on %s", expected_req_path)
        loop.run()
        if not timeout_fired[0]:
            GLib.source_remove(timeout_id)

        if not done["ok"]:
            raise RuntimeError("XDG Portal CreateSession failed or timed out")

    def bind_shortcuts(self, shortcuts: list, on_activated) -> None:
        """Bind shortcuts and subscribe to activations.

        shortcuts: [{"id": str, "description": str, "accelerators": [str]}]
        on_activated: callable(shortcut_id: str) -> None
        """
        assert self._session_path, "call create_session() first"
        self._activated_cb = on_activated

        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806

        vb = GLib.VariantBuilder(GLib.VariantType("a(sa{sv})"))
        for sc in shortcuts:
            s_opts = GLib.Variant(
                "a{sv}",
                {
                    "description": GLib.Variant("s", sc.get("description", "")),
                    "preferred": GLib.Variant("as", sc.get("accelerators", [])),
                    "user_configurable": GLib.Variant("b", True),
                },
            )
            entry = GLib.Variant.new_tuple(GLib.Variant("s", sc["id"]), s_opts)
            vb.add_value(entry)

        def _on_response(conn, sender, path, iface, signal, params):
            response, _results = params.unpack()
            if response == 0:
                logger.info("Portal shortcuts bound successfully")
                if self._act_sub_id is None:
                    self._act_sub_id = self._bus.signal_subscribe(
                        _PORTAL_BUS,
                        _IFACE_GS,
                        "Activated",
                        None,
                        self._session_path,
                        Gio.DBusSignalFlags.NO_MATCH_RULE,
                        _on_activated,
                    )
                # Try to enable shortcuts (no-op if unsupported)
                try:
                    self._proxy.call_sync(
                        "EnableShortcuts",
                        GLib.Variant.new_tuple(
                            GLib.Variant("o", self._session_path),
                            GLib.Variant("s", ""),
                            GLib.Variant("b", True),
                            GLib.Variant(
                                "a{sv}",
                                {
                                    "handle_token": GLib.Variant(
                                        "s", self._new_token("enable")
                                    ),
                                },
                            ),
                        ),
                        Gio.DBusCallFlags.NONE,
                        -1,
                        None,
                    )
                except Exception as e:
                    logger.debug("EnableShortcuts skipped/failed: %s", e)
            else:
                logger.warning(
                    "Portal shortcut binding failed/cancelled (response=%s)", response
                )
            if self._req_sub_id is not None:
                self._bus.signal_unsubscribe(self._req_sub_id)
                self._req_sub_id = None

        def _on_activated(conn, sender, path, iface, signal, params):
            try:
                _sid, sc_id, _ts, _details = params.unpack()
            except Exception:
                try:
                    sc_id, _ts, _details = params.unpack()
                except Exception:
                    logger.exception("Unknown Activated() signature: %r", params)
                    return
            logger.debug("Portal shortcut activated: %s", sc_id)
            if callable(self._activated_cb):
                self._activated_cb(sc_id)

        handle_token = self._new_token("bind")
        bind_opts = GLib.Variant(
            "a{sv}",
            {"handle_token": GLib.Variant("s", handle_token)},
        )

        # Predict request path and subscribe BEFORE calling BindShortcuts
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        expected_req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

        self._req_sub_id = self._bus.signal_subscribe(
            None,
            _IFACE_REQ,
            "Response",
            expected_req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_response,
        )

        self._proxy.call_sync(
            "BindShortcuts",
            GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_path),
                vb.end(),
                GLib.Variant("s", ""),  # parent_window
                bind_opts,
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

    def close(self) -> None:
        """Unsubscribe from signals and end the portal session."""
        for sub_id in [self._act_sub_id, self._req_sub_id]:
            if sub_id is not None:
                self._bus.signal_unsubscribe(sub_id)
        self._act_sub_id = self._req_sub_id = None
        if self._session_path:
            try:
                self._proxy.call_sync(
                    "EndSession",
                    self._GLib.Variant.new_tuple(
                        self._GLib.Variant("o", self._session_path)
                    ),
                    self._Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            except Exception:
                pass
            self._session_path = None
