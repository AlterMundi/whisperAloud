"""XDG Portal GlobalShortcuts backend for Wayland global hotkeys.

Talks directly to org.freedesktop.portal.GlobalShortcuts over D-Bus
using Gio.DBusProxy — no dependency on libportal GI bindings.

Supported compositors (as of 2026): KDE Plasma 6+, GNOME (partial),
wlroots-based compositors (partial). Always falls back gracefully.
"""

import logging

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
        """Call CreateSession on the portal and store the resulting session path."""
        GLib = self._GLib  # noqa: N806
        opts = GLib.Variant("a{sv}", {"app_id": GLib.Variant("s", self._app_id)})
        result = self._proxy.call_sync(
            "CreateSession",
            GLib.Variant.new_tuple(opts),
            self._Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        self._session_path = result.unpack()[0]
        logger.info("Portal session created: %s", self._session_path)

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
            vb.add_value(GLib.Variant("(sa{sv})", (sc["id"], s_opts)))

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
            else:
                logger.warning("Portal shortcut binding failed/cancelled (response=%s)", response)
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

        result = self._proxy.call_sync(
            "BindShortcuts",
            GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_path),
                GLib.Variant("s", ""),  # no parent window token
                vb.end(),
                GLib.Variant("a{sv}", {}),
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        req_path = result.unpack()[0]
        self._req_sub_id = self._bus.signal_subscribe(
            _PORTAL_BUS,
            _IFACE_REQ,
            "Response",
            None,
            req_path,
            Gio.DBusSignalFlags.NO_MATCH_RULE,
            _on_response,
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
                    self._GLib.Variant.new_tuple(self._GLib.Variant("o", self._session_path)),
                    self._Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            except Exception:
                pass
            self._session_path = None
