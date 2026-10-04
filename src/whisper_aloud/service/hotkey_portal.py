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
  call like CreateSession/BindShortcuts) is **not reliable for deciding
  whether to skip BindShortcuts**, at least on GNOME: a dbus-monitor
  capture of a real GNOME 49/51 session shows ListShortcuts replying with
  an *empty* list on a brand new session even when the compositor already
  has a stored binding for this app_id, and only resolves it once
  BindShortcuts itself is called (GNOME then reuses the stored trigger
  and completes without showing a dialog again). So ``BindShortcuts`` is
  now called unconditionally, every session; ListShortcuts is issued only
  as a diagnostic (logged, never gates anything).
- On GNOME, ``BindShortcuts``'s Request::Response can arrive with
  **response=2** ("ended without a normal user response") *even though
  the shortcut was actually bound* — the same capture shows
  ``results = {"shortcuts": [("toggle", {...})]}`` alongside response=2,
  both for a fresh interactive bind (dialog completed, Ctrl+Space chosen)
  and for a session that silently reused an existing stored binding. The
  authoritative signal is therefore the *contents* of the ``"shortcuts"``
  result (and/or a later ``ShortcutsChanged`` signal for the same ids),
  never the bare response code. A response code is logged for visibility
  but only treated as fatal when no shortcuts were reported bound at all.
- ``GlobalShortcuts.Activated`` and ``GlobalShortcuts.ShortcutsChanged``
  are subscribed right after ``CreateSession`` succeeds, independent of
  whatever ``BindShortcuts`` later reports -- GNOME can deliver
  ``Activated`` for a shortcut the compositor already considers bound
  before our own ``BindShortcuts`` Response arrives, and can report a
  shortcut as bound via ``ShortcutsChanged`` after an initial
  ``BindShortcuts`` failure (recoverable: no need to retry Bind).

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
        self._shortcuts_changed_sub_id = None
        # Guards against two interactive BindShortcuts requests in flight at
        # once (see bind()'s docstring) -- this is what produced an observed
        # response=2 on an abandoned prior request.
        self._bind_pending = False
        # Set by close(): once True, any in-flight Response callback is a
        # no-op (the flow it belongs to is no longer wanted).
        self._closed = False

        # Shortcut ids requested by the current/last bind() call, and the
        # state we've learned about them so far -- fed by BindShortcuts'
        # Response, ListShortcuts' Response (diagnostic only) and
        # ShortcutsChanged signals alike, since GNOME can report a shortcut
        # as bound through any of the three (see module docstring).
        self._required_ids: set = set()
        self._bound_ids: set = set()
        self._trigger_descriptions: dict = {}
        # on_bound callback for the current bind() flow, and whether it has
        # already been told "bound" once -- a later ShortcutsChanged must
        # not re-fire it once we've already reported success.
        self._on_bound_cb = None
        self._bind_notified = False

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
        """Create a session and bind `shortcuts`, async.

        shortcuts: [{"id": str, "description": str, "accelerators": [str]}]
        on_activated: callable(shortcut_id: str) -> None, called whenever
            the portal reports a shortcut activation.
        on_bound: optional callable(success: bool) -> None. Called once
            BindShortcuts' Response settles the question of whether *any*
            of our shortcut ids got bound (see `_bind_shortcuts_async` for
            exactly how that's decided -- never just the response code).
            May be called again later, with True, if a `ShortcutsChanged`
            signal reports one of our ids bound after an initial failure
            (it is never called a second time once it has already fired
            True once).

        Non-blocking: returns immediately. The actual CreateSession ->
        subscribe(Activated, ShortcutsChanged) -> ListShortcuts
        (diagnostic only) -> BindShortcuts (always) chain runs entirely
        from Response/signal callbacks on the main loop.
        """
        self._required_ids = {sc["id"] for sc in shortcuts}
        self._on_bound_cb = on_bound
        self._bind_notified = False
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
        # Subscribe to Activated/ShortcutsChanged right away, independent of
        # whatever BindShortcuts ends up reporting (see module docstring) --
        # GNOME can deliver either signal before our own BindShortcuts
        # Response arrives.
        self._subscribe_session_signals(on_activated)
        # Diagnostic only: on GNOME this reliably comes back empty for a
        # brand new session even when a binding already exists, so it must
        # never be used to decide whether to skip BindShortcuts (see module
        # docstring). We always proceed to BindShortcuts below.
        self._list_shortcuts_async(
            on_done=lambda _ids: self._bind_shortcuts_async(shortcuts, on_activated, on_bound)
        )

    @staticmethod
    def _unpack(value):
        return value.unpack() if hasattr(value, "unpack") else value

    def _extract_shortcuts_result(self, results) -> list:
        """Parse a Request result's (or ShortcutsChanged signal's) shortcuts
        array into a plain list of ``(id, options_dict)`` tuples.

        Never raises; returns ``[]`` for anything missing or malformed.
        """
        if not isinstance(results, dict):
            return []
        raw = results.get("shortcuts")
        if raw is None:
            return []
        raw = self._unpack(raw)
        out = []
        for entry in raw:
            try:
                sid, opts = entry[0], entry[1]
            except Exception:
                continue
            opts = self._unpack(opts)
            out.append((sid, opts if isinstance(opts, dict) else {}))
        return out

    def _record_bound(self, entries) -> set:
        """Update `_bound_ids`/`_trigger_descriptions` from a list of
        (id, options_dict) tuples, for the ids we actually asked to bind.

        Returns the subset of `_required_ids` newly confirmed bound by
        `entries` (i.e. present in `entries`, regardless of whether they
        were already known to be bound)."""
        matched = set()
        for sid, opts in entries:
            if sid not in self._required_ids:
                continue
            matched.add(sid)
            self._bound_ids.add(sid)
            trig = opts.get("trigger_description") if isinstance(opts, dict) else None
            if trig is not None:
                self._trigger_descriptions[sid] = self._unpack(trig)
        return matched

    def get_trigger_description(self, shortcut_id: str) -> "str | None":
        """Human-readable trigger (e.g. 'Press <Control>space') the portal
        reported for `shortcut_id`, or None if unknown/not bound yet."""
        return self._trigger_descriptions.get(shortcut_id)

    def is_bound(self, shortcut_id: str) -> bool:
        return shortcut_id in self._bound_ids

    def _list_shortcuts_async(self, on_done) -> None:
        """Call on_done(set_of_bound_ids) -- always, even on failure (empty
        set). Diagnostic only (see module docstring): the result is logged
        and folded into `_bound_ids`/`_trigger_descriptions` for whatever
        it's worth, but nothing downstream depends on it to decide whether
        to call BindShortcuts."""
        assert self._session_path, "create a session first"
        GLib = self._GLib  # noqa: N806

        def _build_args(handle_token):
            opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})
            return GLib.Variant.new_tuple(GLib.Variant("o", self._session_path), opts)

        def _on_response(response, results):
            entries = (
                self._extract_shortcuts_result(results) if response == _RESPONSE_SUCCESS else []
            )
            self._record_bound(entries)
            ids = {sid for sid, _ in entries}
            if response == _RESPONSE_SUCCESS:
                logger.info(
                    "ListShortcuts (diagnostic) response=%s bound_ids=%s", response, sorted(ids)
                )
            else:
                logger.info(
                    "ListShortcuts (diagnostic) returned no usable results (response=%s); "
                    "will call BindShortcuts regardless",
                    response,
                )
            on_done(ids)

        self._request_async("ListShortcuts", _build_args, _on_response, interactive=False)

    def _bind_shortcuts_async(self, shortcuts: list, on_activated, on_bound) -> None:
        """Issue BindShortcuts, async, with no Response deadline.

        Always called, every session -- see module docstring for why the
        old "skip Bind if ListShortcuts already lists it" optimization was
        removed (GNOME's ListShortcuts reliably comes back empty for a
        fresh session even when a binding already exists elsewhere).

        Guards against a second interactive request while one is already
        pending: issuing a second one while the first is unanswered is one
        known way to make the *first* request end up abandoned on the
        portal's side.
        """
        assert self._session_path, "create a session first"
        GLib = self._GLib  # noqa: N806

        if self._bind_pending:
            logger.warning(
                "BindShortcuts requested while a previous request on this session is "
                "still awaiting its Response; refusing to issue a second interactive "
                "request concurrently (the first one is still being tracked and will "
                "be honored whenever it answers)"
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
            entries = self._extract_shortcuts_result(results)
            bound_now = self._record_bound(entries)

            if bound_now:
                triggers = {sid: self._trigger_descriptions.get(sid) for sid in bound_now}
                if response == _RESPONSE_SUCCESS:
                    logger.info(
                        "BindShortcuts succeeded: bound=%s triggers=%s",
                        sorted(bound_now),
                        triggers,
                    )
                else:
                    # GNOME quirk (see module docstring): response!=0 does
                    # NOT mean the shortcut wasn't bound -- trust the
                    # "shortcuts" results instead of the bare code.
                    logger.info(
                        "BindShortcuts returned response=%s but reported our shortcut(s) "
                        "bound anyway (known GNOME quirk -- trusting the results over the "
                        "response code): bound=%s triggers=%s",
                        response,
                        sorted(bound_now),
                        triggers,
                    )
                if not self._bind_notified:
                    self._bind_notified = True
                    if on_bound:
                        on_bound(True)
            else:
                if response == _RESPONSE_SUCCESS:
                    logger.warning(
                        "BindShortcuts returned response=0 but no shortcuts were reported "
                        "bound; treating as a failed bind"
                    )
                elif response == _RESPONSE_OTHER:
                    logger.warning(
                        "BindShortcuts returned response=2 (ended without a normal user "
                        "response) and reported no shortcuts bound. No hotkey was bound "
                        "this run; if the compositor later reports it bound via "
                        "ShortcutsChanged this will still recover automatically."
                    )
                else:
                    logger.warning(
                        "Portal shortcut binding failed/cancelled (response=%s)", response
                    )
                if on_bound:
                    on_bound(False)

        self._request_async("BindShortcuts", _build_args, _on_response, interactive=True)

    def _subscribe_session_signals(self, on_activated) -> None:
        """Subscribe to Activated and ShortcutsChanged, and best-effort
        EnableShortcuts, right after CreateSession succeeds.

        Independent of whatever BindShortcuts later reports (see module
        docstring) -- GNOME can deliver Activated for a shortcut it
        already considers bound before our own BindShortcuts Response
        arrives, and can report a shortcut bound via ShortcutsChanged
        after an initial BindShortcuts failure. Neither Activated/
        ShortcutsChanged-subscribe nor EnableShortcuts is a Request-pattern
        call (no handle_token/Response signal in the portal spec for
        either), so a short bounded call_sync for EnableShortcuts remains
        appropriate here -- it is a plain method call with a direct, fast
        reply, never a dialog.

        Field bug: subscribing with ``sender=_PORTAL_BUS`` (a well-known
        name) and the session handle passed as the ``arg0`` filter used to
        mean these signals were silently never delivered on a real GNOME
        session, even though dbus-monitor showed the portal emitting them
        correctly (unicast to our unique name). Two independent problems:

        1. ``Activated``/``ShortcutsChanged``'s first argument is the
           session handle, of D-Bus type ``o`` (OBJECT_PATH) -- GDBus's
           arg0 match filter only ever matches a STRING-typed first
           argument (this is how match rules work: ``arg0='...'`` in the
           D-Bus match-rule grammar is a string comparison), so a filter
           built from an object path can never match, full stop.
        2. ``NO_MATCH_RULE`` means no match rule is installed on the bus at
           all -- GDBus relies on its own client-side sender tracking
           instead, keyed off resolving the well-known name to its current
           owner's unique name. That resolution is exactly the mechanism
           CVE-2024-34397's fix made stricter, and combined with (1) it
           made these two particular subscriptions silently inert in the
           field while the structurally-similar ``Request::Response``
           subscription in ``_request_async`` (``sender=None``, object
           path filter, ``arg0=None``, ``NONE`` flags) kept working, since
           it never relied on arg0 or well-known-name sender tracking.

        The fix: ``sender=None`` (match any sender; we don't need the
        portal's unique name), ``object_path=_PORTAL_PATH`` (cheap, exact,
        always a match), ``arg0=None`` (no arg0 filtering at the bus
        level), ``flags=NONE`` (do install a real match rule -- harmless
        for a unicast signal, and the only way GDBus reliably delivers
        these on some compositors). The session handle is instead checked
        inside the callback (`_on_activated`/`_on_shortcuts_changed`),
        which also guards against ever acting on a signal for some other
        app's session, should the bus ever deliver one.
        """
        Gio = self._Gio  # noqa: N806
        GLib = self._GLib  # noqa: N806
        self._activated_cb = on_activated

        if self._act_sub_id is None:
            self._act_sub_id = self._bus.signal_subscribe(
                None,
                _IFACE_GS,
                "Activated",
                _PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_activated,
            )
            logger.info(
                "Subscribed to Activated for session=%s (sub_id=%s)",
                self._session_path,
                self._act_sub_id,
            )

        if self._shortcuts_changed_sub_id is None:
            self._shortcuts_changed_sub_id = self._bus.signal_subscribe(
                None,
                _IFACE_GS,
                "ShortcutsChanged",
                _PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_shortcuts_changed,
            )
            logger.info(
                "Subscribed to ShortcutsChanged for session=%s (sub_id=%s)",
                self._session_path,
                self._shortcuts_changed_sub_id,
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
        """Handle GlobalShortcuts.Activated(session_handle, shortcut_id,
        timestamp, options).

        Since the bus-level subscription no longer filters by session
        (see `_subscribe_session_signals`'s docstring -- an object-path
        arg0 filter can never match), the session handle is checked here
        instead: a signal for any session other than ours (e.g. another
        app's GlobalShortcuts session, should the portal ever deliver one
        to us) is logged and ignored, never dispatched.
        """
        try:
            session, sc_id, _ts, _details = params.unpack()
        except Exception:
            try:
                sc_id, _ts, _details = params.unpack()
                session = self._session_path
            except Exception:
                logger.exception("Unknown Activated() signature: %r", params)
                return
        if session != self._session_path:
            logger.debug(
                "Ignoring Activated for unrelated session=%s (ours=%s)",
                session,
                self._session_path,
            )
            return
        logger.info("Portal shortcut activated: %s", sc_id)
        if callable(self._activated_cb):
            self._activated_cb(sc_id)

    def _on_shortcuts_changed(self, conn, sender, path, iface, signal, params):
        """Handle GlobalShortcuts.ShortcutsChanged(session_handle, shortcuts).

        Recovery path (see module docstring): if BindShortcuts initially
        failed to report one of our ids bound, a later ShortcutsChanged
        for that id still marks it bound and fires on_bound(True) once.

        Like `_on_activated`, the session handle is checked here rather
        than at the bus-subscription level (see `_subscribe_session_signals`'s
        docstring for why an arg0 filter on this object-path argument can
        never work) -- a signal for a different session is ignored.
        """
        try:
            session, shortcuts = params.unpack()
        except Exception:
            logger.exception("Unparseable ShortcutsChanged signal: %r", params)
            return
        if session != self._session_path:
            logger.debug(
                "Ignoring ShortcutsChanged for unrelated session=%s (ours=%s)",
                session,
                self._session_path,
            )
            return
        entries = self._extract_shortcuts_result({"shortcuts": shortcuts})
        newly_bound = self._record_bound(entries)
        if not newly_bound:
            return
        logger.info("ShortcutsChanged reports bound shortcuts: %s", sorted(newly_bound))
        if not self._bind_notified and callable(self._on_bound_cb):
            self._bind_notified = True
            self._on_bound_cb(True)

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
        if self._shortcuts_changed_sub_id is not None:
            self._bus.signal_unsubscribe(self._shortcuts_changed_sub_id)
        self._shortcuts_changed_sub_id = None
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
