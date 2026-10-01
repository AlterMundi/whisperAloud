"""XDG Portal GlobalShortcuts backend for Wayland global hotkeys.

Talks directly to org.freedesktop.portal.GlobalShortcuts over D-Bus
using Gio.DBusProxy — no dependency on libportal GI bindings.

Supported compositors (as of 2026): KDE Plasma 6+, GNOME (partial),
wlroots-based compositors (partial). Always falls back gracefully.

Portal semantics this module relies on (see
docs/superpowers/specs/2026-10-01-whisp64-portal-findings.md for the full
writeup with sources):

- Since xdg-desktop-portal 1.19, a non-sandboxed ("host") app must call
  ``org.freedesktop.host.portal.Registry.Register(app_id, options)`` once,
  before any other portal call on the connection, so the portal can
  associate this D-Bus connection with a stable app_id. Without it, some
  compositors fall back to deriving an identity from the systemd scope
  (``app-<id>-<pid>.scope``), which does not work for a plain daemon
  process and makes every CreateSession/BindShortcuts look like a brand
  new, never-before-seen app — hence the "Add Keyboard Shortcuts" dialog
  reappearing on every start.
- ``ListShortcuts(session_handle, options)`` (an async Request/Response
  call like CreateSession/BindShortcuts) returns the shortcuts already
  bound for *this app_id* in a previous session, even though the handle
  passed in is for the session just created. ``BindShortcuts`` only needs
  to be called when our shortcut id is missing from that list — calling
  it again when already bound is what re-triggers the consent dialog.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_IFACE_GS = "org.freedesktop.portal.GlobalShortcuts"
_IFACE_REQ = "org.freedesktop.portal.Request"
_IFACE_REGISTRY = "org.freedesktop.host.portal.Registry"


class PortalHotkeys:
    def __init__(self, app_id: str = "org.fede.whisperaloud"):
        from gi.repository import Gio, GLib  # noqa: F401 — imported here for lazy/guarded load

        self._Gio = Gio
        self._GLib = GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._app_id = app_id

        # Must happen before any other portal call on this connection (see
        # module docstring). Safe to fail: portals older than 1.19 simply
        # don't have this interface/method.
        self._register_app_id()

        self._proxy = Gio.DBusProxy.new_sync(
            self._bus,
            Gio.DBusProxyFlags.NONE,
            None,
            _PORTAL_BUS,
            _PORTAL_PATH,
            _IFACE_GS,
            None,
        )
        self._session_path = None
        self._activated_cb = None
        self._act_sub_id = None
        self._pending_handle_token = None

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

    def _register_app_id(self) -> None:
        """Register this D-Bus connection with a stable app_id.

        Calls ``org.freedesktop.host.portal.Registry.Register(app_id, {})``
        once. Per the xdg-desktop-portal docs this must happen before any
        other portal call on the connection, and may only be called once
        per connection. Ignored (debug-logged) on older portals that lack
        the Registry interface/method, or if it's somehow called twice.
        """
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806
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
            logger.info("Registered app_id '%s' with portal host registry", self._app_id)
        except Exception as e:
            logger.debug("Registry.Register unavailable or failed (pre-1.19 portal?): %s", e)

    def _call_request(self, method_name: str, args, timeout_ms: int = 5000):
        """Invoke a Request-pattern portal method and block for its Response.

        `args` must already be a complete GLib.Variant tuple matching the
        method's signature, with a unique "handle_token" entry in its
        trailing options dict (the caller picks the token so it can predict
        the resulting request object path and subscribe before calling, per
        the portal docs -- the portal may respond before we would otherwise
        subscribe).

        Returns (response_code, results_dict) on success, or (None, None)
        on timeout. Never raises.
        """
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806

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

        self._proxy.call_sync(
            method_name,
            args,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )

        timeout_fired = [False]

        def _timeout():
            timeout_fired[0] = True
            logger.warning(
                "%s timed out waiting for Response on %s", method_name, expected_req_path
            )
            loop.quit()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add(timeout_ms, _timeout)
        logger.debug("Waiting for %s Response on %s", method_name, expected_req_path)
        loop.run()
        if not timeout_fired[0]:
            GLib.source_remove(timeout_id)
        elif sub_id[0] is not None:
            self._bus.signal_unsubscribe(sub_id[0])
            sub_id[0] = None

        return outcome["response"], outcome["results"]

    def create_session(self) -> None:
        """Create a GlobalShortcuts session via Request::Response flow."""
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
            for key in ("session_handle", "session"):
                v = results.get(key)
                if v is not None:
                    session = v.unpack() if hasattr(v, "unpack") else v
                    break

        if response == 0 and session:
            self._session_path = session
            logger.info("Portal session created: %s", self._session_path)
        else:
            logger.warning("CreateSession failed/cancelled/timed out (response=%s)", response)
            raise RuntimeError("XDG Portal CreateSession failed or timed out")

    def list_shortcuts(self) -> set:
        """Return the set of shortcut ids already bound for our app_id.

        Must be called after create_session(). Returns an empty set on any
        failure (timeout, cancelled, unsupported) so callers fall back to
        calling bind_shortcuts() -- never raises.
        """
        assert self._session_path, "call create_session() first"
        GLib = self._GLib  # noqa: N806

        handle_token = self._new_token("req")
        self._pending_handle_token = handle_token
        opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})

        try:
            response, results = self._call_request(
                "ListShortcuts",
                GLib.Variant.new_tuple(
                    GLib.Variant("o", self._session_path),
                    opts,
                ),
            )
        except Exception as e:
            logger.debug("ListShortcuts failed: %s", e)
            return set()

        ids = set()
        if response == 0 and isinstance(results, dict):
            shortcuts = results.get("shortcuts")
            if shortcuts is not None:
                shortcuts = shortcuts.unpack() if hasattr(shortcuts, "unpack") else shortcuts
                for entry in shortcuts:
                    try:
                        ids.add(entry[0])
                    except Exception:
                        continue
        else:
            logger.debug("ListShortcuts returned no usable results (response=%s)", response)
        return ids

    def bind_shortcuts(self, shortcuts: list, on_activated) -> None:
        """Bind shortcuts and subscribe to activations.

        shortcuts: [{"id": str, "description": str, "accelerators": [str]}]
        on_activated: callable(shortcut_id: str) -> None

        Calling this unconditionally re-prompts the user even when the
        shortcuts are already bound for our app_id -- callers should prefer
        bind_shortcuts_if_needed(), which only calls this when needed.
        """
        assert self._session_path, "call create_session() first"
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

        handle_token = self._new_token("bind")
        self._pending_handle_token = handle_token
        bind_opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})

        response, _results = self._call_request(
            "BindShortcuts",
            GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_path),
                vb.end(),
                GLib.Variant("s", ""),  # parent_window
                bind_opts,
            ),
        )

        if response == 0:
            logger.info("Portal shortcuts bound successfully")
            self._finish_binding(on_activated)
        else:
            logger.warning("Portal shortcut binding failed/cancelled (response=%s)", response)

    def bind_shortcuts_if_needed(self, shortcuts: list, on_activated) -> None:
        """Bind `shortcuts` only if not already bound for our app_id.

        Calls list_shortcuts() first; if every requested shortcut id is
        already present, skips BindShortcuts entirely (no re-prompt) and
        just subscribes to Activated. Otherwise falls back to
        bind_shortcuts(), which does prompt.
        """
        assert self._session_path, "call create_session() first"
        existing_ids = self.list_shortcuts()
        required_ids = {sc["id"] for sc in shortcuts}

        if required_ids and required_ids.issubset(existing_ids):
            logger.info(
                "Shortcuts already bound for app_id '%s' (%s); skipping BindShortcuts",
                self._app_id,
                sorted(required_ids),
            )
            self._finish_binding(on_activated)
        else:
            self.bind_shortcuts(shortcuts, on_activated)

    def _finish_binding(self, on_activated) -> None:
        """Subscribe to Activated and best-effort EnableShortcuts.

        Shared by the "just bound" and "already bound, skipped Bind" paths.
        """
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806
        self._activated_cb = on_activated

        if self._act_sub_id is None:
            self._act_sub_id = self._bus.signal_subscribe(
                _PORTAL_BUS,
                _IFACE_GS,
                "Activated",
                None,
                self._session_path,
                Gio.DBusSignalFlags.NO_MATCH_RULE,
                self._on_activated,
            )
        try:
            self._proxy.call_sync(
                "EnableShortcuts",
                GLib.Variant.new_tuple(
                    GLib.Variant("o", self._session_path),
                    GLib.Variant("s", ""),
                    GLib.Variant("b", True),
                    GLib.Variant(
                        "a{sv}", {"handle_token": GLib.Variant("s", self._new_token("enable"))}
                    ),
                ),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except Exception as e:
            logger.debug("EnableShortcuts skipped/failed: %s", e)

    def _on_activated(self, conn, sender, path, iface, signal, params):
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

    def close(self) -> None:
        """Unsubscribe from signals and end the portal session."""
        if self._act_sub_id is not None:
            self._bus.signal_unsubscribe(self._act_sub_id)
        self._act_sub_id = None
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
