"""ShellExtensionProbe against fake services on a private bus.

Fakes both GNOME Shell's org.gnome.Shell.Extensions and our extension's
org.fede.whisperaloud.ShellExtension on a throwaway dbus-run-session bus, so
the real Gio call/unpack/error paths are exercised. Never touches the
session bus: run only via
    scripts/test_private_bus.sh -m dbus_private tests/test_shell_extension_probe_private_bus.py
"""

import pytest

from tests._private_bus import private_bus_address
from whisper_aloud import shell_extension as se

pytestmark = [
    pytest.mark.dbus_private,
    pytest.mark.skipif(private_bus_address() is None,
                       reason="no private bus: run via scripts/test_private_bus.sh"),
]

SHELL_XML = """
<node>
  <interface name="org.gnome.Shell.Extensions">
    <method name="GetExtensionInfo">
      <arg type="s" direction="in" name="uuid"/>
      <arg type="a{sv}" direction="out" name="info"/>
    </method>
    <method name="EnableExtension">
      <arg type="s" direction="in" name="uuid"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="DisableExtension">
      <arg type="s" direction="in" name="uuid"/>
      <arg type="b" direction="out" name="success"/>
    </method>
  </interface>
</node>
"""

EXT_XML = """
<node>
  <interface name="org.fede.whisperaloud.ShellExtension">
    <method name="GetInfo">
      <arg type="a{sv}" direction="out" name="info"/>
    </method>
  </interface>
</node>
"""


@pytest.fixture
def bus():
    from gi.repository import Gio, GLib

    addr = private_bus_address()
    flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
    client = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    service = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    ctx = GLib.MainContext.default()

    def spin(pred, timeout=5.0):
        deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
        while not pred() and GLib.get_monotonic_time() < deadline:
            ctx.iteration(False)
        return pred()

    state = {"enabled": False, "calls": []}

    def own(name):
        reply = service.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "RequestName", GLib.Variant("(su)", (name, 4)), None,
            Gio.DBusCallFlags.NONE, 2000, None)
        assert reply.unpack()[0] == 1

    def shell_method(_conn, _sender, _path, _iface, method, params, invocation):
        uuid = params.unpack()[0]
        state["calls"].append((method, uuid))
        if method == "GetExtensionInfo":
            info = {}
            if uuid == se.EXTENSION_UUID:
                info = {
                    "uuid": GLib.Variant("s", uuid),
                    "state": GLib.Variant("d", 1.0 if state["enabled"] else 2.0),
                    "enabled": GLib.Variant("b", state["enabled"]),
                    "version": GLib.Variant("d", 2.0),
                }
            invocation.return_value(GLib.Variant("(a{sv})", (info,)))
        else:
            state["enabled"] = method == "EnableExtension"
            invocation.return_value(GLib.Variant("(b)", (True,)))

    shell_iface = Gio.DBusNodeInfo.new_for_xml(SHELL_XML).interfaces[0]
    reg = [service.register_object(se.SHELL_EXTENSIONS_PATH, shell_iface, shell_method)]
    own(se.SHELL_EXTENSIONS_BUS_NAME)

    def start_extension(with_info=True):
        if with_info:
            def ext_method(_c, _s, _p, _i, _m, _params, invocation):
                invocation.return_value(GLib.Variant(
                    "(a{sv})", ({"uuid": GLib.Variant("s", se.EXTENSION_UUID),
                                 "version": GLib.Variant("u", 2)},)))
            ext_iface = Gio.DBusNodeInfo.new_for_xml(EXT_XML).interfaces[0]
            reg.append(service.register_object(se.EXTENSION_INFO_PATH, ext_iface, ext_method))
        own(se.EXTENSION_BUS_NAME)

    yield client, spin, state, start_extension

    for r in reg:
        service.unregister_object(r)
    client.close_sync(None)
    service.close_sync(None)


def _query(probe, spin):
    out = {}
    probe.query(lambda shell, running: out.update(shell=shell, running=running, done=True))
    assert spin(lambda: out.get("done"))
    return out


def test_native_probe_not_running_but_installed_disabled(bus):
    from whisper_aloud.ui.shell_extension_probe import ShellExtensionProbe

    client, spin, _state, _start = bus
    out = _query(ShellExtensionProbe(sandboxed=False, connection=client), spin)
    assert out["running"] is None
    assert out["shell"] == se.ShellInfo(installed=True, enabled=False, state=2, version=2)


def test_native_enable_then_running(bus):
    from whisper_aloud.ui.shell_extension_probe import ShellExtensionProbe

    client, spin, state, start = bus
    probe = ShellExtensionProbe(sandboxed=False, connection=client)
    done = {}
    probe.set_enabled(True, lambda ok: done.update(ok=ok))
    assert spin(lambda: "ok" in done)
    assert done["ok"] is True
    assert ("EnableExtension", se.EXTENSION_UUID) in state["calls"]

    start(with_info=True)
    out = _query(probe, spin)
    assert out["running"]["version"] == 2
    assert out["shell"].enabled is True


def test_sandboxed_probe_never_calls_gnome_shell(bus):
    from whisper_aloud.ui.shell_extension_probe import ShellExtensionProbe

    client, spin, state, start = bus
    start(with_info=False)  # v1 extension: name owned, no GetInfo object
    probe = ShellExtensionProbe(sandboxed=True, connection=client)
    out = _query(probe, spin)
    assert out["shell"] is None
    assert out["running"] == {}
    done = {}
    probe.set_enabled(True, lambda ok: done.update(ok=ok))
    assert done == {"ok": False}
    assert state["calls"] == []


def test_watch_reports_owner_changes(bus):
    from whisper_aloud.ui.shell_extension_probe import ShellExtensionProbe

    client, spin, _state, start = bus
    events = []
    probe = ShellExtensionProbe(sandboxed=True, connection=client)
    close = probe.watch(lambda: events.append(1))
    assert spin(lambda: len(events) == 1)  # initial "vanished"
    start(with_info=True)
    assert spin(lambda: len(events) == 2)
    close()
