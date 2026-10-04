"""Background portal RequestBackground client (WHISP-87).

Unit tests drive BackgroundPortal with a fake GDBusConnection (no real bus,
no real portal, so no consent dialog on anyone's desktop). The
`dbus_private` integration test at the bottom publishes a *fake*
org.freedesktop.portal.Desktop on a throwaway bus and only runs via:

    PYTHON=... scripts/test_private_bus.sh -m dbus_private tests/test_background_portal.py -q
"""

import os
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from tests._private_bus import private_bus_address  # noqa: E402
from whisper_aloud import background_portal as bp  # noqa: E402
from whisper_aloud.background_portal import (  # noqa: E402
    BackgroundPortal,
    BackgroundResult,
    interpret_response,
)


class _FakeBus:
    """Records signal subscriptions and async method calls; lets a test
    deliver a Request::Response whenever it likes (or never)."""

    def __init__(self, call_fails: bool = False):
        self.call_fails = call_fails
        self.events: list = []
        self.subs: dict = {}
        self.calls: list = []
        self._next = 1

    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, sender, iface, member, path, arg0, flags, callback):
        sub_id = self._next
        self._next += 1
        self.subs[sub_id] = {"iface": iface, "member": member, "path": path, "cb": callback}
        self.events.append(("subscribe", path))
        return sub_id

    def signal_unsubscribe(self, sub_id):
        self.subs.pop(sub_id, None)
        self.events.append(("unsubscribe", sub_id))

    def call(self, bus_name, path, iface, method, params, reply_type, flags, timeout, cancellable,
             callback):
        self.calls.append(
            {"bus_name": bus_name, "path": path, "iface": iface, "method": method,
             "params": params, "timeout": timeout}
        )
        self.events.append(("call", method))
        callback(self, MagicMock(name="async_result"))

    def call_finish(self, result):
        if self.call_fails:
            raise GLib.Error("org.freedesktop.DBus.Error.UnknownMethod: no Background")
        return GLib.Variant("(o)", ("/org/freedesktop/portal/desktop/request/1_42/x",))

    # -- test helpers --
    def respond(self, response, results, path=None):
        subs = [s for s in self.subs.values() if path is None or s["path"] == path]
        assert subs, "no live Response subscription"
        params = GLib.Variant("(ua{sv})", (response, results))
        for s in list(subs):
            s["cb"](self, ":1.7", s["path"], s["iface"], s["member"], params)


def _opts(bus):
    parent, options = bus.calls[-1]["params"].unpack()
    return parent, options


def _results(background, autostart):
    return {"background": GLib.Variant("b", background), "autostart": GLib.Variant("b", autostart)}


@pytest.fixture
def no_timers():
    """Interactive requests must never arm a timeout (no deadline for a human)."""
    with patch.object(bp.GLib, "timeout_add", side_effect=AssertionError("timer armed")), \
         patch.object(bp.GLib, "timeout_add_seconds", side_effect=AssertionError("timer armed")):
        yield


class TestInterpretResponse:
    def test_granted_on(self):
        r = interpret_response(True, 0, {"background": True, "autostart": True}, previous=False)
        assert r == BackgroundResult("applied", autostart=True, background=True, response=0)

    def test_permission_denied_comes_back_as_success_with_false(self):
        r = interpret_response(True, 0, {"background": False, "autostart": False}, previous=False)
        assert r.outcome == "denied"
        assert r.autostart is False

    def test_turn_off_applied(self):
        r = interpret_response(False, 0, {"background": True, "autostart": False}, previous=True)
        assert r.outcome == "applied"
        assert r.autostart is False

    def test_user_cancelled_keeps_previous_state(self):
        r = interpret_response(True, 1, {}, previous=False)
        assert r.outcome == "cancelled"
        assert r.autostart is False
        r = interpret_response(False, 1, {}, previous=True)
        assert r.autostart is True

    def test_other_response_keeps_previous_state(self):
        r = interpret_response(True, 2, {}, previous=True)
        assert r.outcome == "failed"
        assert r.autostart is True

    def test_call_failure(self):
        r = interpret_response(True, None, None, previous=False, detail="boom")
        assert r.outcome == "error"
        assert r.autostart is False
        assert r.detail == "boom"

    def test_missing_autostart_key_falls_back_to_request_when_background_allowed(self):
        r = interpret_response(True, 0, {"background": True}, previous=False)
        assert r.autostart is True
        r = interpret_response(True, 0, {}, previous=False)
        assert r.autostart is True
        r = interpret_response(True, 0, {"background": False}, previous=False)
        assert r.autostart is False

    def test_variants_are_unpacked(self):
        r = interpret_response(True, 0, _results(True, True), previous=False)
        assert r.autostart is True and r.background is True


class TestRequest:
    def test_request_shape(self, no_timers):
        bus = _FakeBus()
        BackgroundPortal(bus=bus).request(True, "Dictate at login", lambda r: None, previous=False)
        call = bus.calls[-1]
        assert call["bus_name"] == "org.freedesktop.portal.Desktop"
        assert call["path"] == "/org/freedesktop/portal/desktop"
        assert call["iface"] == "org.freedesktop.portal.Background"
        assert call["method"] == "RequestBackground"
        parent, opts = _opts(bus)
        assert parent == ""
        assert opts["reason"] == "Dictate at login"
        assert opts["autostart"] is True
        assert opts["commandline"] == ["whisper-aloud", "--daemon"]
        assert opts["dbus-activatable"] is False
        assert opts["handle_token"].startswith("wa_bg_")

    def test_subscribes_to_the_predicted_request_path_before_calling(self, no_timers):
        bus = _FakeBus()
        BackgroundPortal(bus=bus).request(True, "r", lambda r: None, previous=False)
        _parent, opts = _opts(bus)
        expected = f"/org/freedesktop/portal/desktop/request/1_42/{opts['handle_token']}"
        assert bus.events[0] == ("subscribe", expected)
        assert bus.events[1] == ("call", "RequestBackground")
        (sub,) = bus.subs.values()
        assert sub["iface"] == "org.freedesktop.portal.Request"
        assert sub["member"] == "Response"

    def test_parent_window_is_passed_through(self, no_timers):
        bus = _FakeBus()
        BackgroundPortal(bus=bus).request(
            True, "r", lambda r: None, previous=False, parent_window="wayland:abc"
        )
        assert _opts(bus)[0] == "wayland:abc"

    def test_granted(self, no_timers):
        bus = _FakeBus()
        got = []
        BackgroundPortal(bus=bus).request(True, "r", got.append, previous=False)
        assert got == []  # returns before any answer
        bus.respond(0, _results(True, True))
        assert [r.outcome for r in got] == ["applied"]
        assert got[0].autostart is True
        assert bus.subs == {}

    def test_denied(self, no_timers):
        bus = _FakeBus()
        got = []
        BackgroundPortal(bus=bus).request(True, "r", got.append, previous=False)
        bus.respond(0, _results(False, False))
        assert got[0].outcome == "denied"
        assert got[0].autostart is False

    def test_late_response_is_still_processed(self, no_timers):
        """No deadline: nothing happens until the user answers, however long
        that takes; the subscription stays alive and the answer is used."""
        bus = _FakeBus()
        got = []
        BackgroundPortal(bus=bus).request(True, "r", got.append, previous=False)
        for _ in range(3):
            GLib.MainContext.default().iteration(False)
        assert got == []
        assert len(bus.subs) == 1
        bus.respond(0, _results(True, True))
        assert got[0].autostart is True

    def test_toggle_off_requests_autostart_false(self, no_timers):
        bus = _FakeBus()
        got = []
        BackgroundPortal(bus=bus).request(False, "r", got.append, previous=True)
        assert _opts(bus)[1]["autostart"] is False
        bus.respond(0, _results(True, False))
        assert got[0].outcome == "applied"
        assert got[0].autostart is False

    def test_duplicate_response_is_delivered_once(self, no_timers):
        bus = _FakeBus()
        got = []
        portal = BackgroundPortal(bus=bus)
        portal.request(True, "r", got.append, previous=False)
        (sub,) = bus.subs.values()
        params = GLib.Variant("(ua{sv})", (0, _results(True, True)))
        sub["cb"](bus, ":1.7", sub["path"], sub["iface"], sub["member"], params)
        sub["cb"](bus, ":1.7", sub["path"], sub["iface"], sub["member"], params)
        assert len(got) == 1

    def test_call_failure_reports_error_and_unsubscribes(self, no_timers):
        bus = _FakeBus(call_fails=True)
        got = []
        BackgroundPortal(bus=bus).request(True, "r", got.append, previous=False)
        assert got[0].outcome == "error"
        assert got[0].autostart is False
        assert "no Background" in got[0].detail
        assert bus.subs == {}

    def test_response_after_close_is_ignored(self, no_timers):
        bus = _FakeBus()
        got = []
        portal = BackgroundPortal(bus=bus)
        portal.request(True, "r", got.append, previous=False)
        (sub,) = bus.subs.values()
        portal.close()
        assert bus.subs == {}
        params = GLib.Variant("(ua{sv})", (0, _results(True, True)))
        sub["cb"](bus, ":1.7", sub["path"], sub["iface"], sub["member"], params)
        assert got == []

    def test_callback_exception_does_not_escape_into_glib(self, no_timers):
        bus = _FakeBus()

        def _boom(_r):
            raise RuntimeError("ui gone")

        BackgroundPortal(bus=bus).request(True, "r", _boom, previous=False)
        bus.respond(0, _results(True, True))  # must not raise

    def test_no_bus_reports_error_instead_of_raising(self, no_timers):
        got = []
        with patch.object(bp.Gio, "bus_get_sync", side_effect=GLib.Error("no bus")):
            BackgroundPortal().request(True, "r", got.append, previous=True)
        assert got[0].outcome == "error"
        assert got[0].autostart is True


# ---------------------------------------------------------------------------
# Integration: fake Background portal on a private bus.
# ---------------------------------------------------------------------------


@pytest.mark.dbus_private
@pytest.mark.skipif(
    private_bus_address() is None,
    reason="no private bus: run via scripts/test_private_bus.sh",
)
class TestBackgroundPortalOnPrivateBus:
    """Real GDBus signal routing against a fake portal. The fake answers on
    an explicit control call (`Answer`), so the test can check the client is
    still waiting -- no fixed sleeps, only bus events."""

    def _run(self, tmp_path, flow):
        bus_addr = private_bus_address()
        script = tmp_path / "fake_bg_portal.py"
        script.write_text(_FAKE_BACKGROUND_PORTAL_SRC)
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
        proc = subprocess.Popen([sys.executable, str(script)], env=env)
        try:
            with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
                bus = Gio.DBusConnection.new_for_address_sync(
                    bus_addr,
                    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                    None,
                    None,
                )
                _wait_for_name(bus, "org.freedesktop.portal.Desktop")
                return flow(bus)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_granted_then_turned_off(self, tmp_path):
        def flow(bus):
            portal = BackgroundPortal(bus=bus)
            on = _request_and_answer(bus, portal, True, previous=False, answer=(0, True, True))
            assert on.outcome == "applied" and on.autostart is True
            last = _control(bus, "LastOptions", "(a{sv})")[0]
            assert last["autostart"] is True
            assert last["commandline"] == ["whisper-aloud", "--daemon"]
            assert last["dbus-activatable"] is False
            off = _request_and_answer(bus, portal, False, previous=True, answer=(0, True, False))
            assert off.outcome == "applied" and off.autostart is False
            assert _control(bus, "LastOptions", "(a{sv})")[0]["autostart"] is False

        self._run(tmp_path, flow)

    def test_denied(self, tmp_path):
        def flow(bus):
            portal = BackgroundPortal(bus=bus)
            r = _request_and_answer(bus, portal, True, previous=False, answer=(0, False, False))
            assert r.outcome == "denied" and r.autostart is False

        self._run(tmp_path, flow)

    def test_cancelled(self, tmp_path):
        def flow(bus):
            portal = BackgroundPortal(bus=bus)
            r = _request_and_answer(bus, portal, True, previous=False, answer=(1, False, False))
            assert r.outcome == "cancelled" and r.autostart is False

        self._run(tmp_path, flow)


def _pump_until(predicate, limit_s: float = 10.0):
    """Iterate the default main context until predicate() holds. The limit is
    a hang guard for a broken test, not a wait the logic depends on."""
    ctx = GLib.MainContext.default()
    expired = []
    tid = GLib.timeout_add(int(limit_s * 1000), lambda: expired.append(1) or GLib.SOURCE_REMOVE)
    while not predicate() and not expired:
        ctx.iteration(True)
    if not expired:
        GLib.source_remove(tid)
    assert predicate(), "timed out waiting on the private bus"


def _wait_for_name(bus, name):
    seen = []
    watch = Gio.bus_watch_name_on_connection(
        bus, name, Gio.BusNameWatcherFlags.NONE, lambda *a: seen.append(1), None
    )
    _pump_until(lambda: seen)
    Gio.bus_unwatch_name(watch)


def _control(bus, method, reply=None, args=None):
    res = bus.call_sync(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
        "org.whisperaloud.test.FakeBackgroundControl", method, args,
        GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 2000, None,
    )
    return res.unpack() if res is not None else None


def _request_and_answer(bus, portal, want, previous, answer):
    got = []
    portal.request(want, "test reason", got.append, previous=previous)
    # The request reaches the fake (event), which then holds it -- the
    # client must still be waiting: no deadline.
    _pump_until(lambda: _control(bus, "Pending", "(i)")[0] == 1)
    assert got == []
    response, background, autostart = answer
    _control(bus, "Answer", None, GLib.Variant("(ubb)", (response, background, autostart)))
    _pump_until(lambda: got)
    return got[0]


_FAKE_BACKGROUND_PORTAL_SRC = textwrap.dedent(
    """
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    PATH = "/org/freedesktop/portal/desktop"
    XML = '''
    <node>
      <interface name="org.freedesktop.portal.Background">
        <method name="RequestBackground">
          <arg type="s" name="parent_window" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
      </interface>
      <interface name="org.whisperaloud.test.FakeBackgroundControl">
        <method name="Pending"><arg type="i" name="n" direction="out"/></method>
        <method name="LastOptions"><arg type="a{sv}" name="o" direction="out"/></method>
        <method name="Answer">
          <arg type="u" name="response" direction="in"/>
          <arg type="b" name="background" direction="in"/>
          <arg type="b" name="autostart" direction="in"/>
        </method>
      </interface>
    </node>
    '''
    pending = []
    last = [{}]
    last_raw = [GLib.Variant("a{sv}", {})]

    def on_call(conn, sender, path, iface, method, params, inv):
        if method == "RequestBackground":
            parent, options = params.unpack()
            last[0] = options
            last_raw[0] = params.get_child_value(1)
            token = options["handle_token"]
            req = "/org/freedesktop/portal/desktop/request/%s/%s" % (
                sender.lstrip(":").replace(".", "_"), token)
            pending.append((sender, req))
            inv.return_value(GLib.Variant("(o)", (req,)))
        elif method == "Pending":
            inv.return_value(GLib.Variant("(i)", (len(pending),)))
        elif method == "LastOptions":
            inv.return_value(GLib.Variant.new_tuple(last_raw[0]))
        elif method == "Answer":
            response, background, autostart = params.unpack()
            inv.return_value(None)
            sender, req = pending.pop(0)
            results = {"background": GLib.Variant("b", background),
                       "autostart": GLib.Variant("b", autostart)}
            conn.emit_signal(sender, req, "org.freedesktop.portal.Request", "Response",
                             GLib.Variant("(ua{sv})", (response, results)))

    info = Gio.DBusNodeInfo.new_for_xml(XML)

    def on_bus(conn, name):
        for iface in info.interfaces:
            conn.register_object(PATH, iface, on_call, None, None)

    Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.portal.Desktop",
                     Gio.BusNameOwnerFlags.NONE, on_bus, None, None)
    GLib.MainLoop().run()
    """
)
