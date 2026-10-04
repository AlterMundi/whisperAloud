"""Event-driven presence of the GNOME Shell extension (whisperaloud@fede).

The extension owns ``org.gnome.Shell.Extensions.whisperaloud`` while it is
enabled. The daemon watches that name (no polling) to hide its AppIndicator
tray icon while the extension's top-bar indicator is shown, so the user does
not get two WhisperAloud icons. Inside Flatpak this needs the manifest's
``--talk-name`` for exactly that name.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from ..shell_extension import EXTENSION_BUS_NAME

logger = logging.getLogger(__name__)

try:
    from gi.repository import Gio
except (ImportError, ValueError):  # pragma: no cover - gi always present in practice
    Gio = None


class ShellExtensionWatcher:
    """Call ``on_change(present)`` whenever the extension's bus name gains
    or loses an owner (and once at start with the initial state).

    ``connection``: watch on this Gio.DBusConnection instead of the session
    bus (tests use a private bus). ``watch``/``unwatch`` are injectable for
    unit tests: ``watch(name, appeared_cb, vanished_cb) -> id``.
    """

    def __init__(
        self,
        on_change: Callable[[bool], None],
        *,
        connection=None,
        watch: Optional[Callable] = None,
        unwatch: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._on_change = on_change
        self._present: Optional[bool] = None
        if watch is None:
            watch, unwatch = self._gio_functions(connection)
        self._unwatch = unwatch
        self._id = watch(
            EXTENSION_BUS_NAME,
            lambda *_a: self._set(True),
            lambda *_a: self._set(False),
        )

    @staticmethod
    def _gio_functions(connection):
        if Gio is None:
            raise RuntimeError("Gio not available")
        flags = Gio.BusNameWatcherFlags.NONE
        if connection is not None:
            def watch(name, appeared, vanished):
                return Gio.bus_watch_name_on_connection(
                    connection, name, flags, appeared, vanished)
        else:
            def watch(name, appeared, vanished):
                return Gio.bus_watch_name(
                    Gio.BusType.SESSION, name, flags, appeared, vanished)
        return watch, Gio.bus_unwatch_name

    @property
    def present(self) -> bool:
        return bool(self._present)

    def _set(self, present: bool) -> None:
        if present == self._present:
            return
        self._present = present
        logger.info("GNOME Shell extension %s", "present" if present else "absent")
        try:
            self._on_change(present)
        except Exception as e:  # never propagate into the GLib main loop
            logger.warning("Shell extension presence handler failed: %s", e)

    def close(self) -> None:
        if self._id and self._unwatch is not None:
            self._unwatch(self._id)
        self._id = 0
