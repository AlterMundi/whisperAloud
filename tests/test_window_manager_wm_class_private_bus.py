"""dbus_private integration test: WindowManager.get_active_window() reports
wm_class from a real GNOME extension GetFocusedWindow call, and
is_own_window() correctly tells a WhisperAloud window from an external one.

Exercises the real Gio.DBusProxy.call_sync() path in
`WindowManager._get_active_gnome()` against a fake GNOME extension service
(standing in for contrib/gnome-shell-extension/whisperaloud@fede/
extension.js's GetFocusedWindow), on a throwaway dbus-run-session bus --
never the real session bus. Field bug covered (see
tests/test_daemon_own_window_capture.py for the daemon-level coverage):
stopping recording from the GUI window captured the GUI's own window
(wm_class org.fede.whisperaloud.Gui) as the paste target.

Must only be invoked via:
    scripts/test_private_bus.sh -m dbus_private \
        tests/test_window_manager_wm_class_private_bus.py
"""
import os
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

import pytest

from tests._private_bus import private_bus_address

pytestmark = [
    pytest.mark.dbus_private,
    pytest.mark.skipif(
        private_bus_address() is None,
        reason="no private bus: run via scripts/test_private_bus.sh",
    ),
]

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

EXT_BUS_NAME = "org.gnome.Shell.Extensions.whisperaloud"
WINDOW_PATH = "/org/fede/whisperaloud/Window"
CONTROL_IFACE = "org.whisperaloud.test.FakeWmClassWindowControl"

# {focused window reported by GetFocusedWindow, with an id/title/wm_class
# settable over the test-only control interface} -- minimal, just enough
# to exercise _get_active_gnome()'s real D-Bus round trip.
_FAKE_EXTENSION_SERVICE_SRC = textwrap.dedent(
    """
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    BUS_NAME = "org.gnome.Shell.Extensions.whisperaloud"
    WINDOW_PATH = "/org/fede/whisperaloud/Window"
    WINDOW_IFACE = "org.fede.whisperaloud.Window"

    window = {"id": 0, "title": "", "wm_class": ""}

    INTROSPECTION = \"\"\"
    <node>
      <interface name="org.fede.whisperaloud.Window">
        <method name="GetFocusedWindow">
          <arg direction="out" type="a{sv}" name="window"/>
        </method>
        <method name="ActivateWindow">
          <arg direction="in" type="u" name="window_id"/>
          <arg direction="out" type="b" name="success"/>
        </method>
        <signal name="FocusChanged">
          <arg type="s" name="window_id"/>
        </signal>
      </interface>
      <interface name="org.whisperaloud.test.FakeWmClassWindowControl">
        <method name="SetFocusedWindow">
          <arg type="u" name="id" direction="in"/>
          <arg type="s" name="title" direction="in"/>
          <arg type="s" name="wm_class" direction="in"/>
        </method>
      </interface>
    </node>
    \"\"\"

    def handle_method_call(conn, sender, path, iface, method, params, invocation):
        if iface == "org.fede.whisperaloud.Window":
            if method == "GetFocusedWindow":
                if window["id"] == 0:
                    invocation.return_value(GLib.Variant("(a{sv})", ({},)))
                    return
                invocation.return_value(GLib.Variant("(a{sv})", ({
                    "id": GLib.Variant("u", window["id"]),
                    "title": GLib.Variant("s", window["title"]),
                    "wm_class": GLib.Variant("s", window["wm_class"]),
                },)))
            elif method == "ActivateWindow":
                invocation.return_value(GLib.Variant("(b)", (True,)))
            return
        if iface == "org.whisperaloud.test.FakeWmClassWindowControl":
            if method == "SetFocusedWindow":
                wid, title, wm_class = params.unpack()
                window["id"] = wid
                window["title"] = title
                window["wm_class"] = wm_class
                invocation.return_value(None)
            return
        invocation.return_value(None)

    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    def on_bus_acquired(conn, name):
        for iface in node_info.interfaces:
            conn.register_object(WINDOW_PATH, iface, handle_method_call, None, None)

    Gio.bus_own_name(
        Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired, None, None,
    )

    GLib.MainLoop().run()
    """
)


@pytest.fixture
def fake_extension(tmp_path):
    bus_addr = private_bus_address()
    script = tmp_path / "fake_wm_class_extension_service.py"
    script.write_text(_FAKE_EXTENSION_SERVICE_SRC)

    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
    proc = subprocess.Popen([sys.executable, str(script)], env=env)

    control_bus = Gio.DBusConnection.new_for_address_sync(
        bus_addr,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None,
    )
    deadline = time.monotonic() + 5.0
    owned = False
    while time.monotonic() < deadline:
        reply = control_bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (EXT_BUS_NAME,)), None,
            Gio.DBusCallFlags.NONE, 1000, None,
        )
        if reply.unpack()[0]:
            owned = True
            break
        time.sleep(0.05)
    assert owned, "fake extension service never claimed its bus name"

    def set_focused_window(window_id: int, title: str, wm_class: str) -> None:
        control_bus.call_sync(
            EXT_BUS_NAME, WINDOW_PATH, CONTROL_IFACE, "SetFocusedWindow",
            GLib.Variant("(uss)", (window_id, title, wm_class)), None,
            Gio.DBusCallFlags.NONE, 2000, None,
        )

    yield set_focused_window

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _make_window_manager(bus_addr):
    with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
        from whisper_aloud.clipboard.window_manager import WindowManager

        wm = WindowManager()
        assert wm.backend == "gnome-extension"
        return wm


class TestGetActiveWindowReportsWmClass:
    def test_own_gui_window_is_identified(self, fake_extension):
        from whisper_aloud.clipboard.window_manager import is_own_window

        set_focused_window = fake_extension
        set_focused_window(1, "WhisperAloud", "org.fede.whisperaloud.Gui")
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
            info = wm.get_active_window()

        assert info is not None
        assert info.wm_class == "org.fede.whisperaloud.Gui"
        assert is_own_window(info) is True

    def test_external_window_is_not_identified_as_own(self, fake_extension):
        from whisper_aloud.clipboard.window_manager import is_own_window

        set_focused_window = fake_extension
        set_focused_window(2, "Terminal", "org.gnome.Terminal")
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
            info = wm.get_active_window()

        assert info is not None
        assert info.wm_class == "org.gnome.Terminal"
        assert is_own_window(info) is False

    def test_flatpak_wm_class_is_identified_as_own(self, fake_extension):
        from whisper_aloud.clipboard.window_manager import is_own_window

        set_focused_window = fake_extension
        set_focused_window(3, "WhisperAloud", "org.fede.whisperaloud")
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
            info = wm.get_active_window()

        assert info is not None
        assert is_own_window(info) is True
