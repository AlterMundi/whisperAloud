"""RemoteDesktop portal text insertion for Flatpak sandboxes (WHISP-59).

Inside a Flatpak sandbox there is no ydotool/xdotool: the only sanctioned
way to synthesize a keypress is the ``org.freedesktop.portal.RemoteDesktop``
portal. This mirrors the fully-async Request/Response pattern used by
``service/hotkey_portal.py`` for GlobalShortcuts (see WHISP-64).

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

Design note (maintainer directive, field bug 2 second pass): all three
Request-pattern calls above are fully asynchronous and continuation-based --
no nested GLib.MainLoop, no background thread. ``_request_async()``
subscribes to the predicted Request::Response path, issues the method call
via ``Gio.DBusProxy.call()`` (the async, callback-based API), and returns
immediately; the CreateSession -> SelectDevices -> Start chain resumes
entirely from Response callbacks dispatched by the daemon's own main loop.
``simulate_paste()`` therefore never blocks: if the session isn't ready yet
(setup still pending, or not even started), it kicks off (idempotent) async
setup and falls back to clipboard-only *for this call* immediately -- the
caller already copied the text to the clipboard before this is ever
reached (see service/daemon.py). Once Start's Response arrives with a
restore_token, it's persisted and the session is marked ready; the *next*
paste then uses NotifyKeyboardKeysym for real.

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
from typing import Callable, List, Optional

from .portal_keysyms import KEY_STATE_PRESSED, KEY_STATE_RELEASED, keysym_sequence_for_shortcut

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_IFACE_RD = "org.freedesktop.portal.RemoteDesktop"
_IFACE_REQ = "org.freedesktop.portal.Request"
_IFACE_REGISTRY = "org.freedesktop.host.portal.Registry"
_IFACE_SESSION = "org.freedesktop.portal.Session"

# RemoteDesktop DeviceType bitmask (portal spec).
DEVICE_KEYBOARD = 1

# RemoteDesktop persist_mode values (portal spec, v2+).
PERSIST_MODE_NONE = 0
PERSIST_MODE_APPLICATION = 1
PERSIST_MODE_PERSISTENT = 2

_RESTORE_TOKEN_FILENAME = "portal_restore_token"

# Non-interactive Request calls (CreateSession) never show a dialog, so the
# portal normally answers in well under a second. A WARNING-only watchdog
# after this long is a *diagnostic*, not a deadline: the Response
# subscription stays alive and a late answer is still processed in full
# (maintainer directive: never abandon the request).
_SLOW_RESPONSE_WARNING_MS = 8000


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
    """Persist `token` so the next daemon run skips the consent prompt.

    The restore_token is a long-lived RemoteDesktop consent credential, so
    it is written private to this user (dir 0700, file 0600) regardless of
    the process umask -- `os.open`'s mode argument alone is masked by
    umask, so each permission is pinned again with an explicit `os.chmod`
    after creation (and on every save, in case an existing file/dir was
    ever left world/group-readable by something else).
    """
    if not token:
        return
    try:
        path = _restore_token_path()
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, 0o600)
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
        # `_session_handle` is the RemoteDesktop session object path as soon
        # as CreateSession succeeds -- needed to build SelectDevices/Start's
        # arguments while setup is still in progress. `_session_path` is the
        # "ready for NotifyKeyboardKeysym" flag `simulate_paste()` checks,
        # and must only become non-None once Start's Response has actually
        # arrived (bug found via tests/test_portal_paste_integration.py: it
        # was previously set as soon as CreateSession responded, so a caller
        # racing session setup -- or this module's own _pump_until-style
        # readiness check -- could see "ready" and send
        # NotifyKeyboardKeysym/treat the session as usable before
        # SelectDevices/Start ever ran, meaning the consent dialog's
        # restore_token was silently never requested or persisted).
        self._session_handle = None
        self._session_path = None
        self._session_failed = False
        # Guards against starting a second concurrent session-setup chain
        # (e.g. two pastes requested back to back before the first consent
        # dialog is resolved): a second caller falls back to clipboard-only
        # for its own paste immediately instead of blocking behind, or
        # duplicating, the first interactive request in flight.
        self._session_pending = False
        self._closed = False

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
        # A plain method call with a direct, fast reply (not part of the
        # Request/Response flow this module makes asynchronous), so
        # call_sync here is fine.
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

    # ─── Async Request/Response core (mirrors service.hotkey_portal) ───────

    def _request_async(
        self, method_name: str, build_args, on_response, *, interactive: bool
    ) -> None:
        """Issue a Request-pattern portal method call without blocking.

        See `service.hotkey_portal.PortalHotkeys._request_async`'s
        docstring for the full rationale -- identical design here:
        subscribe to the predicted Request::Response path, call
        `self._proxy.call()` (async, never `call_sync`), return
        immediately. `on_response` fires exactly once from the Response
        signal callback. `interactive=True` (SelectDevices, Start) never
        arms a deadline; `interactive=False` (CreateSession) arms a
        one-shot diagnostic WARNING that never tears down the subscription.
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
                logger.debug("%s Response arrived after close(); ignoring", method_name)
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

        self._proxy.call(
            method_name,
            args,
            Gio.DBusCallFlags.NONE,
            self._timeout_ms,
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
                return GLib.SOURCE_REMOVE

            state["warn_id"] = GLib.timeout_add(_SLOW_RESPONSE_WARNING_MS, _warn)

        logger.debug("Issued %s, awaiting Response on %s", method_name, expected_req_path)

    # ─── Session setup state machine ───────────────────────────────────────

    def _start_session_setup_if_needed(self) -> None:
        """Kick off CreateSession -> SelectDevices -> Start, async, unless
        a session already exists, one permanently failed, or one is already
        in flight. Always non-blocking; completion (success or failure) is
        handled entirely by the chained callbacks below."""
        if self._session_path is not None or self._session_failed or self._session_pending:
            return
        self._session_pending = True
        try:
            self._connect()
        except Exception as e:
            logger.warning("RemoteDesktop portal connect failed: %s", e)
            self._finish_session_setup(False)
            return
        self._create_session_async(on_done=self._on_session_created)

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
            if response == 0 and isinstance(results, dict):
                v = results.get("session_handle")
                if v is not None:
                    session = v.unpack() if hasattr(v, "unpack") else v

            if response == 0 and session:
                self._session_handle = session
                on_done(True)
            else:
                logger.warning("RemoteDesktop CreateSession failed (response=%s)", response)
                on_done(False)

        self._request_async("CreateSession", _build_args, _on_response, interactive=False)

    def _on_session_created(self, ok: bool) -> None:
        if not ok:
            self._finish_session_setup(False)
            return
        self._select_devices_async(self._load_restore_token(), on_done=self._on_devices_selected)

    def _select_devices_async(self, restore_token: Optional[str], on_done) -> None:
        GLib = self._GLib  # noqa: N806

        def _build_args(handle_token):
            opts_dict = {
                "handle_token": GLib.Variant("s", handle_token),
                "types": GLib.Variant("u", DEVICE_KEYBOARD),
                "persist_mode": GLib.Variant("u", PERSIST_MODE_PERSISTENT),
            }
            if restore_token:
                opts_dict["restore_token"] = GLib.Variant("s", restore_token)
            opts = GLib.Variant("a{sv}", opts_dict)
            return GLib.Variant.new_tuple(GLib.Variant("o", self._session_handle), opts)

        def _on_response(response, _results):
            if response == 0:
                on_done(True)
            else:
                logger.warning("RemoteDesktop SelectDevices failed (response=%s)", response)
                on_done(False)

        # SelectDevices can require user interaction on some compositors
        # (device-selection consent) -- no fixed deadline.
        self._request_async("SelectDevices", _build_args, _on_response, interactive=True)

    def _on_devices_selected(self, ok: bool) -> None:
        if not ok:
            self._finish_session_setup(False)
            return
        self._start_async(on_done=self._on_started)

    def _start_async(self, on_done) -> None:
        GLib = self._GLib  # noqa: N806

        def _build_args(handle_token):
            opts = GLib.Variant("a{sv}", {"handle_token": GLib.Variant("s", handle_token)})
            return GLib.Variant.new_tuple(
                GLib.Variant("o", self._session_handle),
                GLib.Variant("s", ""),  # parent_window
                opts,
            )

        def _on_response(response, results):
            if response != 0:
                logger.warning("RemoteDesktop Start failed (response=%s)", response)
                on_done(False, None)
                return
            new_token = None
            if isinstance(results, dict):
                v = results.get("restore_token")
                if v is not None:
                    new_token = v.unpack() if hasattr(v, "unpack") else v
            on_done(True, new_token)

        # Start is the call that shows the "Share your screen" consent
        # dialog on GNOME -- no fixed deadline. A restore_token in the
        # response is persisted (see _on_started) regardless of how long
        # this took to arrive.
        self._request_async("Start", _build_args, _on_response, interactive=True)

    def _on_started(self, ok: bool, restore_token: Optional[str]) -> None:
        if not ok:
            self._finish_session_setup(False)
            return
        if restore_token:
            self._save_restore_token(restore_token)
        self._finish_session_setup(True)

    def _finish_session_setup(self, ok: bool) -> None:
        self._session_pending = False
        if ok:
            # Only now -- after Start's Response actually arrived -- is the
            # session ready for NotifyKeyboardKeysym. See __init__'s comment
            # on _session_handle vs. _session_path for why this must not
            # happen any earlier (e.g. right after CreateSession).
            self._session_path = self._session_handle
            logger.info("RemoteDesktop portal session ready for paste")
        else:
            self._session_failed = True
            self._session_handle = None
            self._session_path = None
            self._emit_fallback("RemoteDesktop portal session setup failed")

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
        """Send the paste shortcut via NotifyKeyboardKeysym, if the
        RemoteDesktop session is ready; otherwise fall back to
        clipboard-only immediately and (idempotently) kick off async
        session setup for next time.

        Returns True only if the portal session was already ready and the
        keysym sequence was sent without error. Returns False -- never
        blocks, never raises -- whenever the session isn't ready yet
        (consent still pending, not yet requested, or permanently failed)
        or a NotifyKeyboardKeysym call fails mid-sequence. The caller
        (service/daemon.py) already copied the text to the clipboard before
        reaching this, so a clipboard-only "failure" here still leaves the
        user with a usable transcript.

        If a press/release in the middle of the sequence fails (e.g. Ctrl
        pressed OK, then the V press fails), any modifier keys already
        pressed but not yet released are released best-effort, in reverse
        (innermost-first) order, in a `finally` block -- otherwise the
        user's keyboard is left with a "stuck" Ctrl/Shift held down.
        Errors from these best-effort releases are swallowed (logged, not
        raised): this path only runs when something is already wrong, and
        failing harder here would not help.
        """
        if self._session_path is None:
            if self._session_failed:
                self._emit_fallback("RemoteDesktop session previously failed")
            else:
                self._start_session_setup_if_needed()
                self._emit_fallback(
                    "RemoteDesktop session not ready yet (consent pending or not yet requested)"
                )
            return False

        held: List[int] = []
        error: Optional[Exception] = None
        try:
            for keysym, state in keysym_sequence_for_shortcut(paste_shortcut):
                self._notify_keysym(keysym, state)
                if state == KEY_STATE_PRESSED:
                    held.append(keysym)
                elif keysym in held:
                    held.remove(keysym)
            return True
        except Exception as e:
            error = e
            return False
        finally:
            if error is not None and held:
                logger.warning(
                    "RemoteDesktop NotifyKeyboardKeysym failed mid-sequence (%s); "
                    "releasing %d stuck key(s): %s",
                    error,
                    len(held),
                    list(reversed(held)),
                )
                for keysym in reversed(held):
                    try:
                        self._notify_keysym(keysym, KEY_STATE_RELEASED)
                    except Exception as release_error:
                        logger.warning(
                            "Failed to release stuck keysym 0x%x: %s", keysym, release_error
                        )
            if error is not None:
                logger.warning("RemoteDesktop NotifyKeyboardKeysym failed: %s", error)
                self._emit_fallback(str(error))

    def close(self) -> None:
        """End the RemoteDesktop session, if one is open.

        Marks any in-flight Response callback a no-op (see
        `_request_async`'s `_finish`) so a setup chain still waiting on a
        Response when shutdown happens never touches state after close().

        Sessions are closed via ``org.freedesktop.portal.Session.Close()``
        on the session's own object path (see
        ``service.hotkey_portal.PortalHotkeys.close`` for the same fix and
        rationale: there is no ``RemoteDesktop.EndSession`` method, and an
        unclosed session left dangling is a plausible contributor to a
        stale-request response on a later daemon run). Best-effort: never
        raises, since this is expected to run during daemon shutdown.
        persist_mode=PERSISTENT means the portal remembers this grant across
        the Close anyway (that's the point of the restore_token), so closing
        the live session here does not require re-consent next run.
        """
        self._closed = True
        # Use _session_handle, not _session_path: a session object can exist
        # on the portal side (CreateSession already succeeded) even while
        # setup is still in progress (SelectDevices/Start not done yet, so
        # _session_path -- the "ready" flag -- is still None); that session
        # should still be closed on shutdown rather than left dangling.
        session_to_close = self._session_handle
        if session_to_close and self._bus is not None:
            try:
                self._bus.call_sync(
                    _PORTAL_BUS,
                    session_to_close,
                    _IFACE_SESSION,
                    "Close",
                    None,
                    None,
                    self._Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
                logger.info("Closed RemoteDesktop session %s", session_to_close)
            except Exception as e:
                logger.debug("Session.Close failed (non-fatal, shutting down anyway): %s", e)
        self._session_handle = None
        self._session_path = None
