"""Integration test for PortalPasteSimulator against a fake RemoteDesktop
portal on a private D-Bus bus (WHISP-59).

Must only be invoked via:
    scripts/test_private_bus.sh -m dbus_private tests/test_portal_paste_integration.py
so the bus `dbus-run-session` created -- never the real desktop session
bus -- is what gets exercised. `private_bus_address()` never connects to
anything; it only inspects env vars conftest.py already captured (see
tests/_private_bus.py), so the skip decision itself can never dial a real
bus. conftest.py overwrites DBUS_SESSION_BUS_ADDRESS for every other
test; this test (and only this test) points it at the private address for
the duration of each D-Bus call it makes.

This exercises the real Request/Response wait loop in
PortalPasteSimulator -- not a mocked Gio layer -- against a fake
`org.freedesktop.portal.Desktop` implementing just enough of
RemoteDesktop (+ Request objects) to drive CreateSession -> SelectDevices
-> Start -> NotifyKeyboardKeysym, and verifies:
  1. the exact NotifyKeyboardKeysym sequence sent for "ctrl+v", and
  2. that the restore_token Start() returns is persisted to disk and
     replayed in SelectDevices on a second, separate PortalPasteSimulator
     session (simulating a second daemon run).
"""

import os
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from tests._private_bus import private_bus_address  # noqa: E402
from whisper_aloud.clipboard.portal_keysyms import (  # noqa: E402
    KEY_STATE_PRESSED,
    KEY_STATE_RELEASED,
    XK_CONTROL_L,
    XK_v,
)
from whisper_aloud.clipboard.portal_paste_simulator import PortalPasteSimulator  # noqa: E402

CONTROL_IFACE = "org.whisperaloud.test.FakePortalControl"


def _pump_until(predicate, timeout_s=5.0):
    """Iterate the default GLib main context (processing real D-Bus I/O,
    including Response signals) until `predicate()` is true or `timeout_s`
    elapses. Needed since PortalPasteSimulator's session setup is now fully
    async (maintainer directive, field bug 2 second pass): the first
    simulate_paste() call only *starts* CreateSession -> SelectDevices ->
    Start and returns False immediately (clipboard-only fallback); the
    session becomes ready later, from Response callbacks."""
    import time as _time

    context = GLib.MainContext.default()
    deadline = _time.monotonic() + timeout_s
    while not predicate() and _time.monotonic() < deadline:
        context.iteration(True)
    assert predicate(), "timed out waiting for condition"


def _control_call(bus, method, arg_variant=None, reply_type=None):
    return bus.call_sync(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        CONTROL_IFACE,
        method,
        arg_variant,
        reply_type,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )


@pytest.mark.dbus_private
@pytest.mark.skipif(
    private_bus_address() is None,
    reason="no private bus: run via scripts/test_private_bus.sh",
)
class TestPortalPasteIntegrationOnPrivateBus:
    def test_notify_sequence_and_restore_token_reuse(self, tmp_path):
        bus_addr = private_bus_address()
        fake_portal_script = tmp_path / "fake_remote_desktop_portal.py"
        fake_portal_script.write_text(_FAKE_PORTAL_SERVICE_SRC)

        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
        proc = subprocess.Popen([sys.executable, str(fake_portal_script)], env=env)
        try:
            time.sleep(1.0)  # let the fake service claim the bus name

            data_home = tmp_path / "xdg-data-home"
            with patch.dict(
                os.environ,
                {"DBUS_SESSION_BUS_ADDRESS": bus_addr, "XDG_DATA_HOME": str(data_home)},
            ):
                # --- First "daemon run": no restore_token yet. The first
                # simulate_paste() call only starts the async session-setup
                # chain and falls back to clipboard-only immediately; pump
                # the main loop for the real Response signals to land, then
                # paste for real. ---
                sim1 = PortalPasteSimulator()
                assert sim1.simulate_paste("ctrl+v") is False
                _pump_until(lambda: sim1._session_path is not None)
                assert sim1.simulate_paste("ctrl+v") is True

                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                notify_calls = _control_call(
                    bus, "NotifyCalls", None, GLib.VariantType("(a(ii))")
                ).unpack()[0]
                assert notify_calls == [
                    (XK_CONTROL_L, KEY_STATE_PRESSED),
                    (XK_v, KEY_STATE_PRESSED),
                    (XK_v, KEY_STATE_RELEASED),
                    (XK_CONTROL_L, KEY_STATE_RELEASED),
                ]

                first_restore_token = (
                    data_home / "whisper_aloud" / "portal_restore_token"
                ).read_text()
                assert first_restore_token

                session_count_run1 = _control_call(
                    bus, "SessionCount", None, GLib.VariantType("(i)")
                ).unpack()[0]
                assert session_count_run1 == 1

                # --- Second "daemon run": a brand-new PortalPasteSimulator
                # (simulating a daemon restart) must replay the persisted
                # restore_token instead of prompting again. ---
                sim2 = PortalPasteSimulator()
                assert sim2.simulate_paste("ctrl+v") is False
                _pump_until(lambda: sim2._session_path is not None)
                assert sim2.simulate_paste("ctrl+v") is True

                last_token_seen = _control_call(
                    bus, "LastSelectDevicesRestoreToken", None, GLib.VariantType("(s)")
                ).unpack()[0]
                assert last_token_seen == first_restore_token

                session_count_run2 = _control_call(
                    bus, "SessionCount", None, GLib.VariantType("(i)")
                ).unpack()[0]
                assert session_count_run2 == 2
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_prewarm_with_existing_token_delivers_queued_paste(self, tmp_path):
        """Field bug fix: with a restore_token already on disk (as if from a
        prior daemon run), `prewarm()` should get the session ready without
        any simulate_paste() call ever starting it, and a paste requested
        while that non-interactive setup is still in flight must be
        delivered once it completes -- not lost to clipboard-only fallback,
        which was the original bug (first auto-paste of every daemon run
        lost)."""
        bus_addr = private_bus_address()
        fake_portal_script = tmp_path / "fake_remote_desktop_portal.py"
        fake_portal_script.write_text(_FAKE_PORTAL_SERVICE_SRC)

        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
        proc = subprocess.Popen([sys.executable, str(fake_portal_script)], env=env)
        try:
            time.sleep(1.0)  # let the fake service claim the bus name

            data_home = tmp_path / "xdg-data-home"
            token_dir = data_home / "whisper_aloud"
            token_dir.mkdir(parents=True)
            (token_dir / "portal_restore_token").write_text("fake-restore-token-abc123")

            with patch.dict(
                os.environ,
                {"DBUS_SESSION_BUS_ADDRESS": bus_addr, "XDG_DATA_HOME": str(data_home)},
            ):
                sim = PortalPasteSimulator()
                sim.prewarm()  # non-interactive: a restore_token is already on disk
                assert sim._prewarm_active is True

                # A paste request arrives before setup has finished -- must
                # be queued, not dropped to clipboard-only.
                assert sim.simulate_paste("ctrl+v") is False
                assert sim._queued_paste_shortcut == "ctrl+v"

                _pump_until(lambda: sim._session_path is not None)

                # The queued paste must have been delivered automatically,
                # with no further simulate_paste() call from this test.
                assert sim._queued_paste_shortcut is None

                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                notify_calls = _control_call(
                    bus, "NotifyCalls", None, GLib.VariantType("(a(ii))")
                ).unpack()[0]
                assert notify_calls == [
                    (XK_CONTROL_L, KEY_STATE_PRESSED),
                    (XK_v, KEY_STATE_PRESSED),
                    (XK_v, KEY_STATE_RELEASED),
                    (XK_CONTROL_L, KEY_STATE_RELEASED),
                ]
        finally:
            proc.terminate()
            proc.wait(timeout=5)


_FAKE_PORTAL_SERVICE_SRC = textwrap.dedent(
    """
    # Minimal fake org.freedesktop.portal.Desktop for WHISP-59 integration
    # testing: implements just enough of Registry + RemoteDesktop to
    # exercise the real Request/Response wait loop in PortalPasteSimulator.
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    BUS_NAME = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"

    session_count = [0]
    notify_calls = []
    last_select_devices_restore_token = [""]
    issued_restore_token = "fake-restore-token-abc123"

    INTROSPECTION = \"\"\"
    <node>
      <interface name="org.freedesktop.host.portal.Registry">
        <method name="Register">
          <arg type="s" name="app_id" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
        </method>
      </interface>
      <interface name="org.freedesktop.portal.RemoteDesktop">
        <method name="CreateSession">
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
        <method name="SelectDevices">
          <arg type="o" name="session_handle" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
        <method name="Start">
          <arg type="o" name="session_handle" direction="in"/>
          <arg type="s" name="parent_window" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
        <method name="NotifyKeyboardKeysym">
          <arg type="o" name="session_handle" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="i" name="keysym" direction="in"/>
          <arg type="u" name="state" direction="in"/>
        </method>
      </interface>
      <interface name="org.whisperaloud.test.FakePortalControl">
        <method name="NotifyCalls">
          <arg type="a(ii)" name="calls" direction="out"/>
        </method>
        <method name="SessionCount">
          <arg type="i" name="count" direction="out"/>
        </method>
        <method name="LastSelectDevicesRestoreToken">
          <arg type="s" name="token" direction="out"/>
        </method>
      </interface>
    </node>
    \"\"\"

    def _emit_response(conn, sender, handle_token, response, results):
        sender_safe = sender.lstrip(":").replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender_safe}/{handle_token}"
        conn.emit_signal(
            sender,
            path,
            "org.freedesktop.portal.Request",
            "Response",
            GLib.Variant("(ua{sv})", (response, results)),
        )

    def handle_method_call(conn, sender, path, iface, method, params, invocation):
        args = params.unpack() if params else ()
        if iface == "org.freedesktop.host.portal.Registry" and method == "Register":
            invocation.return_value(None)
            return
        if iface == "org.whisperaloud.test.FakePortalControl":
            if method == "NotifyCalls":
                invocation.return_value(GLib.Variant("(a(ii))", (notify_calls,)))
            elif method == "SessionCount":
                invocation.return_value(GLib.Variant("(i)", (session_count[0],)))
            elif method == "LastSelectDevicesRestoreToken":
                invocation.return_value(
                    GLib.Variant("(s)", (last_select_devices_restore_token[0],))
                )
            return
        if iface != "org.freedesktop.portal.RemoteDesktop":
            invocation.return_value(None)
            return

        if method == "CreateSession":
            options = args[0]
            handle_token = options.get("handle_token")
            session_count[0] += 1
            invocation.return_value(GLib.Variant("(o)", ("/session/1",)))
            GLib.idle_add(
                _emit_response, conn, sender, handle_token, 0,
                {"session_handle": GLib.Variant("o", "/session/1")},
            )
        elif method == "SelectDevices":
            _session, options = args
            handle_token = options.get("handle_token")
            last_select_devices_restore_token[0] = options.get("restore_token", "")
            invocation.return_value(GLib.Variant("(o)", ("/request/select",)))
            GLib.idle_add(_emit_response, conn, sender, handle_token, 0, {})
        elif method == "Start":
            _session, _parent, options = args
            handle_token = options.get("handle_token")
            invocation.return_value(GLib.Variant("(o)", ("/request/start",)))
            results = {"restore_token": GLib.Variant("s", issued_restore_token)}
            GLib.idle_add(_emit_response, conn, sender, handle_token, 0, results)
        elif method == "NotifyKeyboardKeysym":
            _session, _options, keysym, state = args
            notify_calls.append((keysym, state))
            invocation.return_value(None)
        else:
            invocation.return_value(None)

    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    def on_bus_acquired(conn, name):
        for iface in node_info.interfaces:
            conn.register_object(PATH, iface, handle_method_call, None, None)

    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired,
        None,
        None,
    )

    loop = GLib.MainLoop()
    loop.run()
    """
)
