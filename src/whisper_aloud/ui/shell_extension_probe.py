"""Asynchronous D-Bus queries about the GNOME Shell extension (Gio only).

Used by the Settings "Top bar indicator" section (ui/shell_extension_section.py).
Every call is asynchronous, NO_AUTO_START, with a short timeout; results
arrive on the main loop.

- Our extension's own bus name (GetInfo): works natively and in Flatpak
  (the manifest grants --talk-name for exactly that name).
- org.gnome.Shell.Extensions (GetExtensionInfo, Enable/DisableExtension):
  native only. The Flatpak manifest deliberately does not grant it (it
  would allow disabling/uninstalling any extension), so a sandboxed probe
  never calls it and the UI shows host commands instead.
"""

from __future__ import annotations

import logging
from typing import Callable, Mapping, Optional

from gi.repository import Gio, GLib

from .. import shell_extension as se

logger = logging.getLogger(__name__)

QueryDone = Callable[[Optional[se.ShellInfo], Optional[Mapping]], None]


class ShellExtensionProbe:
    """Asynchronous Gio queries about the extension (results on the main loop)."""

    def __init__(self, *, sandboxed: bool, connection: Optional[Gio.DBusConnection] = None):
        self._sandboxed = sandboxed
        self._connection = connection
        self._cancellable = Gio.Cancellable()

    def _bus(self) -> Optional[Gio.DBusConnection]:
        if self._connection is None:
            try:
                self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error as e:
                logger.debug("No session bus for the shell extension probe: %s", e)
                return None
        return self._connection

    def _call(self, name, path, iface, method, params, reply_type, on_reply):
        bus = self._bus()
        if bus is None:
            on_reply(None, "")
            return

        def _done(conn, res):
            try:
                on_reply(conn.call_finish(res), None)
            except GLib.Error as e:
                if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                    return
                on_reply(None, Gio.DBusError.get_remote_error(e) or "")

        bus.call(name, path, iface, method, params,
                 GLib.VariantType.new(reply_type) if reply_type else None,
                 Gio.DBusCallFlags.NO_AUTO_START, 3000, self._cancellable, _done)

    def query(self, on_done: QueryDone) -> None:
        results: dict = {}
        pending = {"n": 1 if self._sandboxed else 2}

        def _finish():
            pending["n"] -= 1
            if pending["n"] == 0:
                on_done(results.get("shell"), results.get("running"))

        def _on_info(reply, error):
            if reply is not None:
                results["running"] = reply.unpack()[0]
            else:
                results["running"] = se.running_info_from_dbus_error(error or "")
            _finish()

        self._call(se.EXTENSION_BUS_NAME, se.EXTENSION_INFO_PATH, se.EXTENSION_INFO_IFACE,
                   "GetInfo", None, "(a{sv})", _on_info)

        if not self._sandboxed:
            def _on_shell(reply, _error):
                results["shell"] = (
                    se.parse_shell_info(reply.unpack()[0]) if reply is not None else None
                )
                _finish()

            self._call(se.SHELL_EXTENSIONS_BUS_NAME, se.SHELL_EXTENSIONS_PATH,
                       se.SHELL_EXTENSIONS_IFACE, "GetExtensionInfo",
                       GLib.Variant("(s)", (se.EXTENSION_UUID,)), "(a{sv})", _on_shell)

    def set_enabled(self, enabled: bool, on_done: Callable[[bool], None]) -> None:
        if self._sandboxed:
            on_done(False)
            return

        def _on_reply(reply, error):
            ok = reply is not None and bool(reply.unpack()[0])
            if not ok:
                logger.warning("%s of %s failed: %s",
                               "Enable" if enabled else "Disable", se.EXTENSION_UUID, error)
            on_done(ok)

        self._call(se.SHELL_EXTENSIONS_BUS_NAME, se.SHELL_EXTENSIONS_PATH,
                   se.SHELL_EXTENSIONS_IFACE,
                   "EnableExtension" if enabled else "DisableExtension",
                   GLib.Variant("(s)", (se.EXTENSION_UUID,)), "(b)", _on_reply)

    def watch(self, on_change: Callable[[], None]) -> Callable[[], None]:
        """Call on_change whenever the extension's bus name changes owner."""
        bus = self._bus()
        if bus is None:
            return lambda: None
        watch_id = Gio.bus_watch_name_on_connection(
            bus, se.EXTENSION_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_a: on_change(), lambda *_a: on_change())

        def _close():
            Gio.bus_unwatch_name(watch_id)

        return _close

    def close(self) -> None:
        self._cancellable.cancel()


