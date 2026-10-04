"""dbus_private integration test for WindowManager.confirm_focus (WHISP-88).

Exercises the real Gio signal-subscribe/unsubscribe and D-Bus-call paths in
`WindowManager.confirm_focus()`'s gnome-extension backend against a fake
`org.fede.whisperaloud.Window` service (standing in for the GNOME shell
extension's exported interface, see
contrib/gnome-shell-extension/whisperaloud@fede/extension.js) on a
throwaway dbus-run-session bus -- never the real session bus.

Must only be invoked via:
    scripts/test_private_bus.sh -m dbus_private tests/test_window_manager_focus_confirm_private_bus.py

`private_bus_address()` never connects to anything; it only inspects env
vars conftest.py already captured (see tests/_private_bus.py), so the skip
decision itself can never dial a real bus. conftest.py overwrites
DBUS_SESSION_BUS_ADDRESS for every other test; this test points it at the
private address for the duration of its own body.

The fake extension runs in a SEPARATE PROCESS (same pattern as
tests/test_portal_paste_integration.py's fake RemoteDesktop portal), not
in-process: `WindowManager._check_gnome_extension`/`confirm_focus`'s race
-guard both use a blocking `Gio.DBusProxy.call_sync()` on the test's main
thread, which pushes its own private GMainContext while waiting -- an
in-process fake service registered on that same thread's default context
would never get its method-dispatch source iterated, and the call would
simply time out. A separate process has its own independent main loop, so
it keeps answering regardless of what the test thread is doing.

Covers the three outcomes `confirm_focus()` must support:
  * FocusChanged(captured_id) -> callback(True), paste may proceed.
  * FocusChanged(other_id)    -> callback(False) immediately (a real,
    observed "focus went elsewhere" event -- never waits out the timeout).
  * no signal at all          -> callback(False) once the bounded timeout
    elapses (degrade, not a hang).
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
CONTROL_IFACE = "org.whisperaloud.test.FakeWindowExtensionControl"

_FAKE_EXTENSION_SERVICE_SRC = textwrap.dedent(
    """
    # Minimal fake org.gnome.Shell.Extensions.whisperaloud service for
    # WHISP-88 integration testing: implements org.fede.whisperaloud.Window
    # (GetFocusedWindow/ActivateWindow/FocusChanged) plus a test-only
    # control interface used to trigger a FocusChanged emission on demand.
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    BUS_NAME = "org.gnome.Shell.Extensions.whisperaloud"
    WINDOW_PATH = "/org/fede/whisperaloud/Window"
    WINDOW_IFACE = "org.fede.whisperaloud.Window"

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
      <interface name="org.whisperaloud.test.FakeWindowExtensionControl">
        <method name="EmitFocusChanged">
          <arg type="s" name="window_id" direction="in"/>
        </method>
      </interface>
    </node>
    \"\"\"

    def handle_method_call(conn, sender, path, iface, method, params, invocation):
        if iface == "org.fede.whisperaloud.Window":
            if method == "GetFocusedWindow":
                invocation.return_value(GLib.Variant("(a{sv})", ({},)))
            elif method == "ActivateWindow":
                invocation.return_value(GLib.Variant("(b)", (True,)))
            return
        if iface == "org.whisperaloud.test.FakeWindowExtensionControl":
            if method == "EmitFocusChanged":
                (window_id,) = params.unpack()
                conn.emit_signal(
                    None, WINDOW_PATH, WINDOW_IFACE, "FocusChanged",
                    GLib.Variant("(s)", (window_id,)),
                )
                invocation.return_value(None)
            return
        invocation.return_value(None)

    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    def on_bus_acquired(conn, name):
        for iface in node_info.interfaces:
            conn.register_object(WINDOW_PATH, iface, handle_method_call, None, None)

    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired, None, None,
    )

    loop = GLib.MainLoop()
    loop.run()
    """
)


def _emit_focus_changed(bus, window_id: str) -> None:
    bus.call_sync(
        EXT_BUS_NAME, "/org/fede/whisperaloud/Window", CONTROL_IFACE,
        "EmitFocusChanged", GLib.Variant("(s)", (window_id,)), None,
        Gio.DBusCallFlags.NONE, 2000, None,
    )


@pytest.fixture
def fake_extension(tmp_path):
    bus_addr = private_bus_address()
    script = tmp_path / "fake_window_extension_service.py"
    script.write_text(_FAKE_EXTENSION_SERVICE_SRC)

    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
    proc = subprocess.Popen([sys.executable, str(script)], env=env)
    try:
        # Bounded wait for the fake service to actually own its bus name,
        # polling the real NameHasOwner condition rather than a blind sleep.
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

        yield control_bus, lambda window_id: _emit_focus_changed(control_bus, window_id)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def window_manager(fake_extension):
    """A real WindowManager connected to the private bus, with the
    gnome-extension backend already detected (the fake service above
    answers GetFocusedWindow)."""
    bus_addr = private_bus_address()
    with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
        from whisper_aloud.clipboard.window_manager import WindowManager

        wm = WindowManager()
        assert wm.backend == "gnome-extension"
        yield wm


def _confirm(wm, window_id, timeout_ms=3000):
    result = {}
    bus_addr = private_bus_address()
    with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
        from whisper_aloud.clipboard.window_manager import WindowInfo

        info = WindowInfo(window_id=window_id, title="x", backend="gnome-extension")
        wm.confirm_focus(info, lambda ok: result.update(ok=ok), timeout_ms=timeout_ms)
    return result


def _spin(predicate, timeout=5.0):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        ctx.iteration(True if ctx.pending() else False)
        if not ctx.pending():
            time.sleep(0.01)
    return predicate()


class TestConfirmFocusMatches:
    def test_focus_changed_to_captured_window_confirms_true(self, window_manager, fake_extension):
        _control_bus, emit = fake_extension
        result = _confirm(window_manager, 42)

        emit("42")

        assert _spin(lambda: "ok" in result)
        assert result["ok"] is True


class TestConfirmFocusMismatchDegradesImmediately:
    def test_focus_changed_to_a_different_window_reports_false_fast(
        self, window_manager, fake_extension
    ):
        _control_bus, emit = fake_extension
        result = _confirm(window_manager, 42, timeout_ms=5000)

        emit("99")  # a DIFFERENT window got focus -- a real, observed event

        # Must resolve immediately on the mismatch, not wait out the 5s
        # timeout -- bound the assertion's own wait well under that.
        assert _spin(lambda: "ok" in result, timeout=1.5)
        assert result["ok"] is False


class TestConfirmFocusNoSignalDegrades:
    def test_no_focus_changed_signal_times_out_and_degrades(
        self, window_manager, fake_extension, caplog
    ):
        import logging

        _control_bus, _emit = fake_extension
        with caplog.at_level(logging.WARNING):
            result = _confirm(window_manager, 42, timeout_ms=300)

            assert _spin(lambda: "ok" in result, timeout=3.0)

        assert result["ok"] is False
        assert any("WHISP-88" in r.message for r in caplog.records)
