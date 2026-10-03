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

Design note (maintainer directive, field bug 2 second pass): every
Request-pattern call (CreateSession, ListShortcuts, BindShortcuts) is
fully asynchronous and continuation-based -- there is no nested
GLib.MainLoop anywhere in this module, and no background thread either.
``_request_async()`` subscribes to the predicted Request::Response path,
issues the method call via ``Gio.DBusProxy.call()`` (the async, callback-
based API -- never ``call_sync``), and returns immediately; the state
machine (``bind()``'s CreateSession -> ListShortcuts -> BindShortcuts-if-
needed -> subscribe Activated chain) resumes from the Response signal
callback, dispatched by the daemon's own single main loop whenever the
portal actually answers. BindShortcuts therefore has no deadline at all
(there is no correct fixed "the user should have clicked by now"), and the
daemon's main loop is never blocked waiting for a human.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_IFACE_GS = "org.freedesktop.portal.GlobalShortcuts"
_IFACE_REQ = "org.freedesktop.portal.Request"
_IFACE_REGISTRY = "org.freedesktop.host.portal.Registry"
_IFACE_SESSION = "org.freedesktop.portal.Session"

# Portal Request::Response codes (xdg-desktop-portal spec).
_RESPONSE_SUCCESS = 0
_RESPONSE_CANCELLED_BY_USER = 1
_RESPONSE_OTHER = 2  # e.g. ended without a user response at all

# Non-interactive Request calls (CreateSession, ListShortcuts) never show a
# dialog, so the portal normally answers in well under a second. A
# WARNING-only watchdog after this long is a *diagnostic*, not a deadline:
# the Response subscription stays alive and a late answer is still
# processed in full (maintainer directive: never abandon the request).
_SLOW_RESPONSE_WARNING_MS = 8000


class PortalHotkeys:
    def __init__(self, app_id: str = "org.fede.whisperaloud"):
        from gi.repository import Gio, GLib  # noqa: F401 — imported here for lazy/guarded load

        self._Gio = Gio
        self._GLib = GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._app_id = app_id

        # Must happen before any other portal call on this connection (see
        # module docstring). Safe to fail: portals older than 1.19 simply
        # don't have this interface/method. A plain, non-Request method
        # call with an immediate reply -- not part of the Request/Response
        # flow this module makes asynchronous, so call_sync here is fine
        # (bounded, non-interactive, never shows a dialog).
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
        # Guards against two interactive BindShortcuts requests in flight at
        # once (see bind()'s docstring) -- this is what produced an observed
        # response=2 on an abandoned prior request.
        self._bind_pending = False
        # Set by close(): once True, any in-flight Response callback is a
        # no-op (the flow it belongs to is no longer wanted).
        self._closed = False

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
            # Also covers a second Register() on the same connection (e.g. a future
            # caller re-using this instance): the portal rejects that, we just log it.
            logger.debug("Registry.Register unavailable or failed (pre-1.19 portal?): %s", e)

    # ─── Async Request/Response core ───────────────────────────────────────

    def _request_async(
        self, method_name: str, build_args, on_response, *, interactive: bool
    ) -> None:
        """Issue a Request-pattern portal method call without blocking.

        `build_args(handle_token) -> GLib.Variant` builds the method's
        argument tuple, given the handle_token this call picked -- every
        Request-pattern method's trailing options dict must carry this same
        token (per the portal docs) so the resulting request object path is
        predictable and we can subscribe to it before the portal even sees
        the call.

        Subscribes to ``Request::Response`` on that predicted path *first*,
        then calls ``self._proxy.call()`` (Gio's async API: schedules the
        call and returns immediately; `_on_call_done` below only reports
        whether the method call itself reached the portal, never the
        eventual Response).

        `on_response(response_code_or_None, results_dict_or_None)` is
        invoked exactly once, from the Response signal callback -- normal
        GLib signal dispatch on the daemon's own main loop, not a nested
        loop or another thread. If the method call itself fails (e.g. the
        portal interface is gone), `on_response(None, None)` is invoked
        immediately instead, since no Request will ever answer.

        `interactive=True` (BindShortcuts) never arms the slow-response
        watchdog: there is no correct deadline for a human. `interactive=
        False` (CreateSession, ListShortcuts) arms a one-shot WARNING after
        `_SLOW_RESPONSE_WARNING_MS` if no Response has arrived yet -- purely
        diagnostic; the subscription is never torn down because of it, so a
        late Response is still delivered to `on_response` in full.
        """
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806

        handle_token = self._new_token("req")
        args = build_args(handle_token)
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        expected_req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

        state = {"sub_id": None, "warn_id": None, "responded": False}

        def _finish(response, results):
            if state["responded"]:
                return
            state["responded"] = True
            if state["sub_id"] is not None:
                self._bus.signal_unsubscribe(state["sub_id"])
                state["sub_id"] = None
            if state["warn_id"] is not None:
                GLib.source_remove(state["warn_id"])
                state["warn_id"] = None
            if self._closed:
                logger.debug(
                    "%s Response arrived after close(); ignoring", method_name
                )
                return
            on_response(response, results)

        def _on_signal(_conn, _sender, _path, _iface, _signal, params):
            try:
                response, results = params.unpack()
            except Exception:
                logger.exception("Unparseable %s Response: %r", method_name, params)
                response, results = None, None
            _finish(response, results)

        state["sub_id"] = self._bus.signal_subscribe(
            None,
            _IFACE_REQ,
            "Response",
            expected_req_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_signal,
        )

        def _on_call_done(proxy, result, _user_data=None):
            try:
                proxy.call_finish(result)
            except Exception as e:
                logger.warning("%s call failed (no Request was created): %s", method_name, e)
                _finish(None, None)
            # On success, nothing to do here -- the real answer is the
            # Response signal, handled by _on_signal above.

        self._proxy.call(
            method_name,
            args,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            _on_call_done,
        )

        if not interactive:

            def _warn():
                if not state["responded"]:
                    logger.warning(
                        "%s: no Response after %dms yet on %s -- still waiting, not giving up",
                        method_name,
                        _SLOW_RESPONSE_WARNING_MS,
                        expected_req_path,
                    )
                return GLib.SOURCE_REMOVE  # one-shot

            state["warn_id"] = GLib.timeout_add(_SLOW_RESPONSE_WARNING_MS, _warn)

        logger.debug("Issued %s, awaiting Response on %s", method_name, expected_req_path)

    # ─── Public entry point: the whole CreateSession -> bind flow ─────────

    def bind(self, shortcuts: list, on_activated, on_bound=None) -> None:
        """Create a session and bind `shortcuts` if not already bound, async.

        shortcuts: [{"id": str, "description": str, "accelerators": [str]}]
        on_activated: callable(shortcut_id: str) -> None, called whenever
            the portal reports a shortcut activation.
        on_bound: optional callable(success: bool) -> None, called once the
            flow settles (CreateSession/ListShortcuts/BindShortcuts all
            happened, or one of them failed).

        Non-blocking: returns immediately. The actual CreateSession ->
        ListShortcuts -> BindShortcuts-if-needed -> Activated-subscribe
        chain runs entirely from Response callbacks on the main loop.
        """
        self._create_session_async(
            on_done=lambda ok: self._on_session_created(ok, shortcuts, on_activated, on_bound)
        )

    def _create_session_async(self, on_done) -> None:
        GLib = self._GLib  # noqa: N806

        session_token = self._new_token("session")

        def _build_args(handle_token):
            opts = GLib.Variant(
                "a{sv}",
                {
                    "session_handle_token": GLib.Variant("s", session_token),
                    "handle_token": GLib.Variant("s", handle_token),
                },
            )
            return GLib.Variant.new_tuple(opts)

        def _on_response(response, results):
            session = None
            if response == _RESPONSE_SUCCESS and isinstance(results, dict):
                for key in ("session_handle", "session"):
                    v = results.get(key)
                    if v is not None:
                        session = v.unpack() if hasattr(v, "unpack") else v
                        break

            if response == _RESPONSE_SUCCESS and session:
                self._session_path = session
                logger.info("CreateSession succeeded: session=%s", self._session_path)
                on_done(True)
            else:
                logger.warning("CreateSession failed/cancelled (response=%s)", response)
                on_done(False)

        self._request_async("CreateSession", _build_args, _on_response, interactive=False)

    def _on_session_created(self, ok, shortcuts, on_activated, on_bound) -> None:
        if not ok:
            if on_bound:
                on_bound(False)
            return
        self._list_shortcuts_async(
            on_done=lambda existing_ids: self._on_shortcuts_listed(
                existing_ids, shortcuts, on_activated, on_bound
            )
        )

    def _list_shortcuts_async(self, on_done) -> None:
        """Call on_done(set_of_bound_ids) -- always, even on failure (empty
        set), matching the module's "recoverable, never fatal" policy for
        this non-interactive call."""
        assert self._session_path, "create a session first"
        GLib = self._GLib  # noqa: N806

        def _build_args(handle_token):
            opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})
            return GLib.Variant.new_tuple(GLib.Variant("o", self._session_path), opts)

        def _on_response(response, results):
            ids = set()
            if response == _RESPONSE_SUCCESS and isinstance(results, dict):
                shortcuts_result = results.get("shortcuts")
                if shortcuts_result is not None:
                    shortcuts_result = (
                        shortcuts_result.unpack()
                        if hasattr(shortcuts_result, "unpack")
                        else shortcuts_result
                    )
                    for entry in shortcuts_result:
                        try:
                            ids.add(entry[0])
                        except Exception:
                            continue
                logger.info("ListShortcuts response=%s bound_ids=%s", response, sorted(ids))
            else:
                logger.info(
                    "ListShortcuts returned no usable results (response=%s); will attempt "
                    "BindShortcuts (recoverable, not fatal)",
                    response,
                )
            on_done(ids)

        self._request_async("ListShortcuts", _build_args, _on_response, interactive=False)

    def _on_shortcuts_listed(self, existing_ids, shortcuts, on_activated, on_bound) -> None:
        required_ids = {sc["id"] for sc in shortcuts}

        if required_ids and required_ids.issubset(existing_ids):
            logger.info(
                "Shortcuts already bound for app_id '%s' (%s); skipping BindShortcuts",
                self._app_id,
                sorted(required_ids),
            )
            self._finish_binding(on_activated)
            if on_bound:
                on_bound(True)
            return

        self._bind_shortcuts_async(shortcuts, on_activated, on_bound)

    def _bind_shortcuts_async(self, shortcuts: list, on_activated, on_bound) -> None:
        """Issue BindShortcuts, async, with no Response deadline.

        Guards against a second interactive request while one is already
        pending (see module docstring) -- issuing a second one while the
        first is unanswered is what produces an observed response=2 on the
        abandoned first request.
        """
        assert self._session_path, "create a session first"
        GLib = self._GLib  # noqa: N806

        if self._bind_pending:
            logger.warning(
                "BindShortcuts requested while a previous request is still pending; "
                "refusing to issue a second interactive request (this is what produces "
                "a response=2 on the abandoned one)"
            )
            if on_bound:
                on_bound(False)
            return
        self._bind_pending = True

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
        shortcuts_variant = vb.end()

        def _build_args(handle_token):
            bind_opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})
            return GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_path),
                shortcuts_variant,
                GLib.Variant("s", ""),  # parent_window
                bind_opts,
            )

        def _on_response(response, results):
            self._bind_pending = False
            if response == _RESPONSE_SUCCESS:
                bound_ids = [sc["id"] for sc in shortcuts]
                logger.info(
                    "BindShortcuts succeeded: response=%s bound=%s accelerators=%s",
                    response,
                    bound_ids,
                    {sc["id"]: sc.get("accelerators", []) for sc in shortcuts},
                )
                self._finish_binding(on_activated)
                if on_bound:
                    on_bound(True)
            else:
                if response == _RESPONSE_OTHER:
                    logger.warning(
                        "BindShortcuts returned response=2 (ended without a normal user "
                        "response). This usually means a previous request on this app_id "
                        "was abandoned (dialog closed without choosing, or the daemon "
                        "restarted mid-prompt) and the portal considers it stale. No "
                        "hotkey was bound this run; restarting the daemon and responding "
                        "promptly to the dialog next time should recover."
                    )
                else:
                    logger.warning(
                        "Portal shortcut binding failed/cancelled (response=%s)", response
                    )
                if on_bound:
                    on_bound(False)

        self._request_async("BindShortcuts", _build_args, _on_response, interactive=True)

    def _finish_binding(self, on_activated) -> None:
        """Subscribe to Activated and best-effort EnableShortcuts.

        Shared by the "just bound" and "already bound, skipped Bind" paths.
        Neither Activated-subscribe nor EnableShortcuts is a Request-pattern
        call (no handle_token/Response signal in the portal spec for
        either), so a short bounded call_sync for EnableShortcuts remains
        appropriate here -- it is a plain method call with a direct, fast
        reply, never a dialog.
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
            logger.info(
                "Subscribed to Activated for session=%s (sub_id=%s)",
                self._session_path,
                self._act_sub_id,
            )
        try:
            self._proxy.call_sync(
                "EnableShortcuts",
                GLib.Variant.new_tuple(
                    GLib.Variant("o", self._session_path),
                    GLib.Variant("s", ""),
                    GLib.Variant("b", True),
                    GLib.Variant("a{sv}", {}),
                ),
                Gio.DBusCallFlags.NONE,
                5000,
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
        """Unsubscribe from signals and end the portal session.

        Marks any in-flight Response callback a no-op (see
        `_request_async`'s `_finish`) so a flow still waiting on a Response
        when shutdown happens never touches state after close().

        Sessions are closed via ``org.freedesktop.portal.Session.Close()``
        on the session's own object path (the generic Session interface
        every portal session object implements), not a GlobalShortcuts
        method -- there is no ``GlobalShortcuts.EndSession`` in the portal
        spec. Calling a nonexistent method here previously failed silently
        (caught below) on every daemon shutdown, meaning sessions were never
        actually closed; an abandoned/left-open session is one plausible
        contributor to a later stale request getting response=2 from the
        portal. Best-effort: never raises, since this runs during shutdown.
        """
        self._closed = True
        if self._act_sub_id is not None:
            self._bus.signal_unsubscribe(self._act_sub_id)
        self._act_sub_id = None
        if self._session_path:
            try:
                self._bus.call_sync(
                    _PORTAL_BUS,
                    self._session_path,
                    _IFACE_SESSION,
                    "Close",
                    None,
                    None,
                    self._Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
                logger.info("Closed portal session %s", self._session_path)
            except Exception as e:
                logger.debug("Session.Close failed (non-fatal, shutting down anyway): %s", e)
            self._session_path = None
