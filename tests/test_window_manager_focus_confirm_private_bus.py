"""dbus_private integration test for WindowManager.confirm_focus (WHISP-88).

Exercises the real Gio signal-subscribe/unsubscribe and D-Bus-call paths in
`WindowManager.confirm_focus()`'s gnome-extension backend against a fake
GNOME extension service (standing in for
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
in-process: confirm_focus's own async D-Bus calls are dispatched on the
test's main thread's default GMainContext, which an in-process fake
service registered on that SAME context would also need iterated to
answer -- simplest and most realistic to just run it as an independent
process with its own main loop, exactly like the real extension.

Regression covered here (field bug, found in review): the maintainer's
real extension at the time was v1 -- it exports GetFocusedWindow but NOT
the FocusChanged signal added in this ticket. Blindly waiting for a signal
that will never arrive made EVERY auto-paste silently skip. confirm_focus
must detect this via the extension's own GetInfo version (cached per
name-owner-change) and:
  * version >= 4 (supports FocusChanged): wait for the signal as before.
  * version < 4 (known unsupported): confirm via bounded GetFocusedWindow
    re-queries instead of a signal that will never come.
  * GetInfo itself unavailable (unknown): fail open -- the pre-WHISP-88
    behavior (proceed to paste, no confirmation) with a WARNING, rather
    than skipping every paste against an extension we could not even ask.
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
EXT_INFO_PATH = "/org/fede/whisperaloud/ShellExtension"
CONTROL_IFACE = "org.whisperaloud.test.FakeWindowExtensionControl"

# {VERSION_BLOCK} is substituted per-test: either a real ShellExtension
# interface/handler reporting a chosen "version" (>=4 supports
# FocusChanged, <4 predates it), or nothing at all (simulating an
# extension old enough that GetInfo/the whole interface is unavailable --
# the "unknown capability" case).
_FAKE_EXTENSION_SERVICE_SRC = textwrap.dedent(
    """
    # Minimal fake org.gnome.Shell.Extensions.whisperaloud service for
    # WHISP-88 integration testing: implements org.fede.whisperaloud.Window
    # (GetFocusedWindow/ActivateWindow/FocusChanged), optionally
    # org.fede.whisperaloud.ShellExtension (GetInfo, with a configurable
    # version), plus a test-only control interface to trigger a
    # FocusChanged emission and to move the "currently focused" window id
    # GetFocusedWindow reports.
    import os
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    BUS_NAME = "org.gnome.Shell.Extensions.whisperaloud"
    WINDOW_PATH = "/org/fede/whisperaloud/Window"
    WINDOW_IFACE = "org.fede.whisperaloud.Window"
    EXT_INFO_PATH = "/org/fede/whisperaloud/ShellExtension"
    EXT_INFO_IFACE = "org.fede.whisperaloud.ShellExtension"

    version_env = os.environ.get("FAKE_EXT_VERSION", "")
    HAS_INFO = version_env != ""
    VERSION = int(version_env) if HAS_INFO else 0
    focused_window_id = [""]

    # The ShellExtension interface is always DECLARED, but only REGISTERED
    # (below) when HAS_INFO -- so an "unknown capability" run (no
    # FAKE_EXT_VERSION) answers GetInfo with a real D-Bus UnknownInterface
    # error instead of needing a second, string-spliced introspection XML.
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
      <interface name="org.fede.whisperaloud.ShellExtension">
        <method name="GetInfo">
          <arg direction="out" type="a{sv}" name="info"/>
        </method>
      </interface>
      <interface name="org.whisperaloud.test.FakeWindowExtensionControl">
        <method name="EmitFocusChanged">
          <arg type="s" name="window_id" direction="in"/>
        </method>
        <method name="SetFocusedWindow">
          <arg type="s" name="window_id" direction="in"/>
        </method>
      </interface>
    </node>
    \"\"\"

    def handle_method_call(conn, sender, path, iface, method, params, invocation):
        if iface == "org.fede.whisperaloud.Window":
            if method == "GetFocusedWindow":
                window = {}
                if focused_window_id[0]:
                    window = {"id": GLib.Variant("u", int(focused_window_id[0]))}
                invocation.return_value(GLib.Variant("(a{sv})", (window,)))
            elif method == "ActivateWindow":
                invocation.return_value(GLib.Variant("(b)", (True,)))
            return
        if iface == "org.fede.whisperaloud.ShellExtension":
            if method == "GetInfo":
                invocation.return_value(GLib.Variant(
                    "(a{sv})", ({"version": GLib.Variant("u", VERSION)},)))
            return
        if iface == "org.whisperaloud.test.FakeWindowExtensionControl":
            if method == "EmitFocusChanged":
                (window_id,) = params.unpack()
                conn.emit_signal(
                    None, WINDOW_PATH, WINDOW_IFACE, "FocusChanged",
                    GLib.Variant("(s)", (window_id,)),
                )
                invocation.return_value(None)
            elif method == "SetFocusedWindow":
                (window_id,) = params.unpack()
                focused_window_id[0] = window_id
                invocation.return_value(None)
            return
        invocation.return_value(None)

    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    def on_bus_acquired(conn, name):
        for iface in node_info.interfaces:
            if iface.name == EXT_INFO_IFACE:
                if not HAS_INFO:
                    continue
                conn.register_object(EXT_INFO_PATH, iface, handle_method_call, None, None)
            else:
                conn.register_object(WINDOW_PATH, iface, handle_method_call, None, None)

    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired, None, None,
    )

    loop = GLib.MainLoop()
    loop.run()
    """
)


def _control_call(bus, method, arg_variant=None):
    return bus.call_sync(
        EXT_BUS_NAME, WINDOW_PATH, CONTROL_IFACE, method, arg_variant, None,
        Gio.DBusCallFlags.NONE, 2000, None,
    )


@pytest.fixture
def start_fake_extension(tmp_path):
    """Returns a factory ``start(version=None)`` that spawns the fake
    extension (``version=None`` omits the ShellExtension/GetInfo interface
    entirely, simulating an extension too old to even answer that) and
    yields ``(emit_focus_changed, set_focused_window)`` helpers. Cleans up
    the subprocess on teardown."""
    bus_addr = private_bus_address()
    procs = []

    def start(version=None):
        script = tmp_path / f"fake_window_extension_service_{len(procs)}.py"
        script.write_text(_FAKE_EXTENSION_SERVICE_SRC)

        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
        if version is not None:
            env["FAKE_EXT_VERSION"] = str(version)
        else:
            env.pop("FAKE_EXT_VERSION", None)
        proc = subprocess.Popen([sys.executable, str(script)], env=env)
        procs.append(proc)

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

        def emit_focus_changed(window_id: str) -> None:
            _control_call(control_bus, "EmitFocusChanged", GLib.Variant("(s)", (window_id,)))

        def set_focused_window(window_id: str) -> None:
            _control_call(control_bus, "SetFocusedWindow", GLib.Variant("(s)", (window_id,)))

        return emit_focus_changed, set_focused_window

    yield start

    for proc in procs:
        proc.terminate()
    for proc in procs:
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


class TestConfirmFocusViaSignalWhenSupported:
    """version >= 4: the extension supports FocusChanged, so confirm_focus
    waits for it exactly as before."""

    def test_focus_changed_to_captured_window_confirms_true(self, start_fake_extension):
        emit, _set_focus = start_fake_extension(version=4)
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)
        result = _confirm(wm, 42)

        emit("42")

        assert _spin(lambda: "ok" in result)
        assert result["ok"] is True

    def test_focus_changed_to_a_different_window_reports_false_fast(self, start_fake_extension):
        emit, _set_focus = start_fake_extension(version=4)
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)
        result = _confirm(wm, 42, timeout_ms=5000)

        emit("99")  # a DIFFERENT window got focus -- a real, observed event

        # Must resolve immediately on the mismatch, not wait out the 5s
        # timeout -- bound the assertion's own wait well under that.
        assert _spin(lambda: "ok" in result, timeout=1.5)
        assert result["ok"] is False

    def test_no_focus_changed_signal_times_out_and_degrades(self, start_fake_extension, caplog):
        import logging

        _emit, _set_focus = start_fake_extension(version=4)
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        with caplog.at_level(logging.WARNING):
            result = _confirm(wm, 42, timeout_ms=300)
            assert _spin(lambda: "ok" in result, timeout=3.0)

        assert result["ok"] is False
        assert any("WHISP-88" in r.message for r in caplog.records)


class TestConfirmFocusViaRequeryWhenUnsupported:
    """Field regression: version < 4 (e.g. the maintainer's real v1
    install) never emits FocusChanged. confirm_focus must detect this via
    GetInfo and confirm via GetFocusedWindow re-queries instead of waiting
    on a signal that will never arrive."""

    def test_matching_focused_window_confirms_true_without_any_signal(self, start_fake_extension):
        _emit, set_focus = start_fake_extension(version=1)
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        set_focus("42")
        result = _confirm(wm, 42, timeout_ms=5000)

        assert _spin(lambda: "ok" in result, timeout=3.0)
        assert result["ok"] is True

    def test_persistent_mismatch_degrades_with_warning(self, start_fake_extension, caplog):
        import logging

        _emit, set_focus = start_fake_extension(version=1)
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        set_focus("99")  # never matches the captured window
        with caplog.at_level(logging.WARNING):
            result = _confirm(wm, 42, timeout_ms=5000)
            assert _spin(lambda: "ok" in result, timeout=3.0)

        assert result["ok"] is False
        assert any("WHISP-88" in r.message for r in caplog.records)


class TestConfirmFocusFallsBackWhenCapabilityUnknown:
    """GetInfo itself unavailable (extension too old to even export
    ShellExtension): must NOT skip every paste -- fail open to the
    pre-WHISP-88 behavior (proceed, no confirmation) with a WARNING."""

    def test_unknown_capability_falls_back_to_proceeding_with_warning(
        self, start_fake_extension, caplog
    ):
        import logging

        start_fake_extension(version=None)
        bus_addr = private_bus_address()
        wm = _make_window_manager(bus_addr)

        with caplog.at_level(logging.WARNING):
            result = _confirm(wm, 42, timeout_ms=5000)
            assert _spin(lambda: "ok" in result, timeout=3.0)

        assert result["ok"] is True
        assert any("WHISP-88" in r.message for r in caplog.records)
