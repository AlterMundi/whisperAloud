"""Native text insertion through the GNOME Shell extension (WHISP-119).

The extension (``contrib/gnome-shell-extension/whisperaloud@fede``) exports
``org.fede.whisperaloud.TextInput`` on its own bus name, which the Flatpak
manifest already lets the daemon talk to:

- ``GetCapabilities() -> a{sv}``: ``insert_text`` (b) tells whether
  ``InsertText`` is available. The daemon gates on this, never on the
  extension's version number.
- ``InsertText(s text, a{sv} options) -> a{sv}``: ``{method, reason}`` where
  method is ``commit`` (IM commit through text-input-v3), ``paste`` (clipboard
  + in-Shell paste chord) or ``none`` (refused; ``reason`` says why).

Everything here is asynchronous on the GLib main loop (``Gio.DBusConnection
.call`` with a callback): never a blocking call from the daemon's main loop.
The text is never logged.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, NamedTuple, Optional

from ..shell_extension import EXTENSION_BUS_NAME, TEXT_INPUT_IFACE, TEXT_INPUT_PATH

logger = logging.getLogger(__name__)

try:
    from gi.repository import Gio, GLib
except (ImportError, ValueError):  # pragma: no cover - gi always present in practice
    Gio = None
    GLib = None

# Bounds the D-Bus round trip only (a hung Shell); never a success criterion.
DEFAULT_CALL_TIMEOUT_MS = 5000

# Reason reported when the InsertText call itself failed (no reply, access
# denied, extension vanished mid-call).
CALL_FAILED = "call-failed"

# What to tell the user after an insertion attempt. The text is always on the
# clipboard already (the daemon copies it first).
NOTICE_COPIED = "copied"
NOTICE_FOCUS_CHANGED = "focus-changed"
NOTICE_LOCKED = "locked"
NOTICE_PASSWORD = "password"

NOTICE_MESSAGES = {
    NOTICE_COPIED: "Text copied — press Ctrl+V to paste",
    NOTICE_LOCKED: "Text copied; the screen is locked, so it was not pasted",
    NOTICE_PASSWORD: "Text copied; not typed into a password field",
}

_FOCUS_REASONS = frozenset({"window-mismatch", "no-focus"})


class InsertResult(NamedTuple):
    method: str  # "commit" | "paste" | "none"
    reason: str

    @property
    def inserted(self) -> bool:
        return self.method in ("commit", "paste")


def parse_insert_result(result: Optional[Mapping[str, Any]]) -> InsertResult:
    """Normalize InsertText's unpacked ``a{sv}`` reply."""
    if not isinstance(result, Mapping):
        return InsertResult("none", "")
    return InsertResult(str(result.get("method", "none")), str(result.get("reason", "")))


def notice_for_result(result: InsertResult) -> Optional[str]:
    """Map an insertion outcome onto a NOTICE_* (None: nothing to say)."""
    if result.inserted or result.reason == "empty":
        return None
    if result.reason in _FOCUS_REASONS:
        return NOTICE_FOCUS_CHANGED
    if result.reason == "locked":
        return NOTICE_LOCKED
    if result.reason == "password-field":
        return NOTICE_PASSWORD
    return NOTICE_COPIED


def capabilities_allow_insert(caps: Optional[Mapping[str, Any]]) -> bool:
    return isinstance(caps, Mapping) and caps.get("insert_text") is True


class ExtensionTextInserter:
    """Daemon-side client of the extension's TextInput interface.

    ``on_extension_presence_changed`` is fed by the daemon's single
    ``ShellExtensionWatcher`` (bus-name events, no polling); each appearance
    re-probes ``GetCapabilities`` once. ``connection``: a Gio.DBusConnection
    to use instead of the session bus (tests use a private bus).
    """

    def __init__(self, connection=None, *, timeout_ms: int = DEFAULT_CALL_TIMEOUT_MS) -> None:
        self._connection = connection
        self._timeout_ms = timeout_ms
        self._capabilities: Optional[dict] = None
        self._generation = 0
        self._probed_generation = -1

    @property
    def can_insert(self) -> bool:
        return capabilities_allow_insert(self._capabilities)

    @property
    def capabilities(self) -> Optional[dict]:
        return self._capabilities

    @property
    def capabilities_known(self) -> bool:
        """True once the probe for the extension's current appearance has
        answered (successfully or not), or while the extension is absent."""
        return self._probed_generation == self._generation

    def _bus(self):
        if self._connection is not None:
            return self._connection
        if Gio is None:
            raise RuntimeError("Gio not available")
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(self, method: str, params, callback: Callable) -> None:
        flags = Gio.DBusCallFlags.NO_AUTO_START if Gio is not None else 0
        self._bus().call(
            EXTENSION_BUS_NAME, TEXT_INPUT_PATH, TEXT_INPUT_IFACE, method, params,
            None, flags, self._timeout_ms, None, callback,
        )

    def on_extension_presence_changed(self, present: bool) -> None:
        self._generation += 1
        self._capabilities = None
        if present:
            self._probe(self._generation)
        else:
            self._probed_generation = self._generation

    def _probe(self, generation: int) -> None:
        def _on_reply(conn, res) -> None:
            try:
                reply = conn.call_finish(res)
                caps = reply.unpack()[0]
            except Exception as e:
                # An extension older than version 5 has no TextInput object.
                logger.info("GNOME Shell extension cannot insert text: %s", e)
                caps = None
            if generation != self._generation:
                return
            self._capabilities = dict(caps) if isinstance(caps, Mapping) else None
            self._probed_generation = generation
            logger.info(
                "GNOME Shell extension text insertion %s",
                "available" if self.can_insert else "unavailable",
            )

        try:
            self._call("GetCapabilities", None, _on_reply)
        except Exception as e:
            logger.info("Could not probe the GNOME Shell extension for text insertion: %s", e)
            self._probed_generation = generation

    def insert(
        self,
        text: str,
        *,
        window_id: Optional[int] = None,
        on_done: Callable[[InsertResult], None],
    ) -> None:
        """Ask the extension to insert ``text`` into the focused app.

        ``window_id``: the captured window (extension window ids); the
        extension refuses with ``window-mismatch`` unless it still has focus
        at the moment of insertion (WHISP-88). ``on_done`` runs exactly once,
        on the main loop, with the outcome (``none``/``call-failed`` when the
        call itself fails).
        """
        done = {"called": False}

        def _finish(result: InsertResult) -> None:
            if done["called"]:
                return
            done["called"] = True
            on_done(result)

        def _on_reply(conn, res) -> None:
            try:
                reply = conn.call_finish(res)
                result = parse_insert_result(reply.unpack()[0])
            except Exception as e:
                logger.warning("Extension InsertText failed: %s", _error_summary(e))
                result = InsertResult("none", CALL_FAILED)
            _finish(result)

        options = {}
        if window_id is not None:
            options["window_id"] = GLib.Variant("u", int(window_id) & 0xFFFFFFFF)
        try:
            params = GLib.Variant("(sa{sv})", (text, options))
            self._call("InsertText", params, _on_reply)
        except Exception as e:
            logger.warning("Extension InsertText could not be sent: %s", _error_summary(e))
            _finish(InsertResult("none", CALL_FAILED))


def _error_summary(error: Exception) -> str:
    """D-Bus error name when available (never the call's arguments)."""
    if Gio is not None:
        try:
            remote = Gio.DBusError.get_remote_error(error)
            if remote:
                return remote
        except Exception:
            pass
    return type(error).__name__
