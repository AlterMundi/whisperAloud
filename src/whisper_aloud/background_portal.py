"""org.freedesktop.portal.Background client (WHISP-87).

Inside Flatpak the app cannot write the host's ``~/.config/autostart``; the
Background portal does it on the app's behalf, after asking the user.
``RequestBackground(parent_window, options)`` follows the portal
Request/Response pattern: the method returns a request object path at once,
and the real answer arrives later as ``org.freedesktop.portal.Request::
Response`` on that path. GNOME shows a consent dialog the first time, so the
answer can take as long as the user takes: there is **no deadline** and no
watchdog timer here. The subscription stays alive until the Response arrives
(or ``close()`` is called), and a late answer is processed in full -- the
same event-driven pattern as ``service/hotkey_portal.py``, without its
nested loops or threads.

Options sent (xdg-desktop-portal Background, version 1+):

- ``handle_token`` -- picked here, so the request path is known and the
  Response subscription is in place before the call is issued;
- ``reason`` -- shown in the consent dialog;
- ``autostart`` -- true to start at login, false to remove the entry;
- ``commandline`` -- ``["whisper-aloud", "--daemon"]``: the portal writes a
  host autostart entry running ``flatpak run --command=whisper-aloud
  <app-id> --daemon``, i.e. the daemon only, never the window;
- ``dbus-activatable`` -- false: the entry runs the command line above.

Results: ``background`` (b, permission to run in the background) and
``autostart`` (b, whether the autostart entry is now enabled).
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

_PORTAL_BUS = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_IFACE_BACKGROUND = "org.freedesktop.portal.Background"
_IFACE_REQUEST = "org.freedesktop.portal.Request"

DEFAULT_COMMANDLINE: Sequence[str] = ("whisper-aloud", "--daemon")

# Upper bound for the *method call* leg only (portal reachable, request
# object created) -- never for the user's answer, which has no deadline.
_METHOD_CALL_TIMEOUT_MS = 25000

OUTCOME_APPLIED = "applied"  # autostart now matches what was requested
OUTCOME_DENIED = "denied"  # the portal answered, but autostart differs (permission refused)
OUTCOME_CANCELLED = "cancelled"  # response 1: the user dismissed the dialog
OUTCOME_FAILED = "failed"  # response 2: the request ended some other way
OUTCOME_ERROR = "error"  # the call never reached the portal


@dataclass(frozen=True)
class BackgroundResult:
    """Outcome of one RequestBackground.

    ``autostart`` is the best knowledge of the autostart state *after* the
    request: the portal's own answer when it gave one, else the state before
    the request (``previous``).
    """

    outcome: str
    autostart: bool
    background: Optional[bool] = None
    response: Optional[int] = None
    detail: str = ""


def _unpack(value):
    return value.unpack() if hasattr(value, "unpack") else value


def interpret_response(
    requested_autostart: bool,
    response: Optional[int],
    results: Optional[dict],
    *,
    previous: bool,
    detail: str = "",
) -> BackgroundResult:
    """Map a Response (or a failed call: ``response=None``) to a result."""
    if response is None:
        return BackgroundResult(OUTCOME_ERROR, autostart=previous, detail=detail)

    results = results if isinstance(results, dict) else {}
    background = results.get("background")
    background = bool(_unpack(background)) if background is not None else None
    autostart = results.get("autostart")

    if response != 0:
        state = bool(_unpack(autostart)) if autostart is not None else previous
        outcome = OUTCOME_CANCELLED if response == 1 else OUTCOME_FAILED
        return BackgroundResult(
            outcome, autostart=state, background=background, response=response, detail=detail
        )

    if autostart is not None:
        state = bool(_unpack(autostart))
    else:
        # Not seen in practice (the portal always reports it), but stay sane:
        # a request that was allowed did what it asked for.
        state = requested_autostart and background is not False
    outcome = OUTCOME_APPLIED if state == requested_autostart else OUTCOME_DENIED
    return BackgroundResult(
        outcome, autostart=state, background=background, response=response, detail=detail
    )


class BackgroundPortal:
    """Issues RequestBackground calls, fully async, no deadline."""

    def __init__(self, bus=None):
        self._bus = bus
        self._closed = False
        self._live_subs: set = set()

    def _connection(self):
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._bus

    def close(self) -> None:
        """Drop every pending Response subscription; later answers are ignored."""
        self._closed = True
        for sub_id in list(self._live_subs):
            try:
                self._bus.signal_unsubscribe(sub_id)
            except Exception:  # pragma: no cover - teardown best effort
                logger.debug("signal_unsubscribe failed", exc_info=True)
        self._live_subs.clear()

    def request(
        self,
        autostart: bool,
        reason: str,
        on_done: Callable[[BackgroundResult], None],
        *,
        previous: bool,
        parent_window: str = "",
        commandline: Sequence[str] = DEFAULT_COMMANDLINE,
    ) -> None:
        """Ask the portal to enable (``autostart=True``) or remove the login
        autostart. Returns at once; ``on_done(BackgroundResult)`` runs exactly
        once, from the main loop, whenever the answer arrives.

        ``previous`` is the autostart state before this request, reported
        back when the portal gives no answer of its own (cancel, error).
        """
        state = {"sub_id": None, "done": False}

        def _finish(response, results, detail=""):
            if state["done"]:
                return
            state["done"] = True
            if state["sub_id"] is not None:
                self._live_subs.discard(state["sub_id"])
                try:
                    self._bus.signal_unsubscribe(state["sub_id"])
                except Exception:
                    logger.debug("signal_unsubscribe failed", exc_info=True)
                state["sub_id"] = None
            if self._closed:
                logger.debug("RequestBackground answer after close(); ignoring")
                return
            result = interpret_response(
                autostart, response, results, previous=previous, detail=detail
            )
            logger.info(
                "RequestBackground(autostart=%s) -> %s (response=%s, background=%s, autostart=%s)",
                autostart, result.outcome, response, result.background, result.autostart,
            )
            try:
                on_done(result)
            except Exception:
                logger.exception("RequestBackground result handler failed")

        try:
            bus = self._connection()
            sender = bus.get_unique_name().lstrip(":").replace(".", "_")
        except Exception as e:
            logger.warning("Background portal unavailable (no session bus): %s", e)
            _finish(None, None, detail=str(e))
            return

        token = f"wa_bg_{uuid.uuid4().hex}"
        request_path = f"{_PORTAL_PATH}/request/{sender}/{token}"

        def _on_response(_conn, _sender, _path, _iface, _member, params):
            try:
                response, results = params.unpack()
            except Exception:
                logger.exception("Unparseable RequestBackground Response: %r", params)
                response, results = 2, {}
            _finish(response, results)

        # Subscribe before calling, so even an instant answer is not missed.
        state["sub_id"] = bus.signal_subscribe(
            None,
            _IFACE_REQUEST,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_response,
        )
        self._live_subs.add(state["sub_id"])

        options = {
            "handle_token": GLib.Variant("s", token),
            "reason": GLib.Variant("s", reason),
            "autostart": GLib.Variant("b", bool(autostart)),
            "commandline": GLib.Variant("as", list(commandline)),
            "dbus-activatable": GLib.Variant("b", False),
        }
        params = GLib.Variant("(sa{sv})", (parent_window or "", options))

        def _on_call_done(conn, result, _user_data=None):
            try:
                conn.call_finish(result)
            except Exception as e:
                logger.warning("RequestBackground call failed (no request created): %s", e)
                _finish(None, None, detail=str(e))
            # On success the answer is the Response signal, not this reply.

        bus.call(
            _PORTAL_BUS,
            _PORTAL_PATH,
            _IFACE_BACKGROUND,
            "RequestBackground",
            params,
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE,
            _METHOD_CALL_TIMEOUT_MS,
            None,
            _on_call_done,
        )
        logger.debug("Issued RequestBackground(autostart=%s), awaiting %s", autostart, request_path)
