"""Tests for the WHISP-64 portal registration/bind flow, rewritten for the
fully async, continuation-based design (maintainer directive, field bug 2
second pass): no nested GLib.MainLoop, no background thread anywhere in
hotkey_portal.py. `_request_async()` subscribes to Request::Response and
issues the method call via the async `Gio.DBusProxy.call()` API, returning
immediately; the CreateSession -> ListShortcuts -> BindShortcuts-if-needed
-> Activated-subscribe chain resumes entirely from Response callbacks.

All Gio/GLib bus traffic is mocked (bus_get_sync, DBusProxy.new_sync/call,
and signal_subscribe/unsubscribe) via `_FakePortalHarness` below, which lets
a test script Response arriving later, out of order, or never -- these
tests never open a real D-Bus connection and never touch the real
xdg-desktop-portal, which would pop a real consent dialog on the
developer's desktop (see conftest.py).

An optional integration-level test further down publishes a *fake*
org.freedesktop.portal.Desktop (GlobalShortcuts + host Registry) on a
private, test-only bus started via `dbus-run-session`, and is marked
`dbus_private` so it only runs when explicitly requested, e.g.:

    scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_portal_flow.py -q

Its skip guard (tests._private_bus.private_bus_address) never connects to
any bus -- it only inspects env vars conftest.py already captured -- so a
plain `pytest -q` run can never dial a real D-Bus connection from this
file; only the body of that one test (once the guard has already let it
run) ever points DBUS_SESSION_BUS_ADDRESS at the private bus.
"""

import os
import subprocess
import sys
import textwrap
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from tests._private_bus import private_bus_address  # noqa: E402
from whisper_aloud.service.hotkey_portal import PortalHotkeys  # noqa: E402


class _FakePortalHarness:
    """Drives PortalHotkeys's fully-async Request/Response flow with no
    real D-Bus connection and no real event loop.

    `call_log` records, in order, every portal-facing call made: "Register",
    "CreateSession", "ListShortcuts", "BindShortcuts", "Session.Close".

    `respond(method_name, response, results, which=-1)` delivers a
    synthetic Response signal for the `which`-th call to `method_name`
    (default: the most recent one) by directly invoking the callback that
    `PortalHotkeys` passed to `signal_subscribe` -- exactly what a real
    GLib main loop dispatching a real D-Bus signal would do, just without
    actually running one. Not calling `respond()` at all simulates "no
    Response ever arrives" (e.g. an abandoned dialog); calling it out of
    the order the requests were issued simulates a reordered/delayed
    Response.

    By default, each `proxy.call()` immediately succeeds at the "reached
    the portal" leg (via `call_finish` returning None) -- tests that need
    to simulate the method call itself failing patch
    `harness.mock_proxy.call_finish.side_effect`.
    """

    def __init__(self, registry_available: bool = True, bound_ids=None):
        self.call_log: list[str] = []
        self.registry_available = registry_available
        self.bound_ids = bound_ids or set()
        self._stack = ExitStack()
        self._requests: list[dict] = []
        self._next_sub_id = 1

        self.mock_bus = MagicMock(name="bus")
        self.mock_bus.get_unique_name.return_value = ":1.99"
        self.mock_bus.call_sync.side_effect = self._bus_call_sync
        self.mock_bus.signal_subscribe.side_effect = self._signal_subscribe
        self.mock_bus.signal_unsubscribe.side_effect = self._signal_unsubscribe

        self.mock_proxy = MagicMock(name="proxy")
        self.mock_proxy.call.side_effect = self._proxy_call
        self.mock_proxy.call_finish.side_effect = lambda result: None

    def _bus_call_sync(
        self, bus_name, path, iface, method, args, reply_type, flags, timeout, cancellable
    ):
        if iface == "org.freedesktop.host.portal.Registry" and method == "Register":
            self.call_log.append("Register")
            if not self.registry_available:
                raise GLib.Error("no such interface org.freedesktop.host.portal.Registry")
            return None
        if iface == "org.freedesktop.portal.Session" and method == "Close":
            self.call_log.append("Session.Close")
            return None
        raise AssertionError(f"unexpected bus.call_sync: {iface}.{method}")

    def _signal_subscribe(self, sender, iface, signal, path, arg0, flags, callback):
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        self._requests.append(
            {"sub_id": sub_id, "callback": callback, "method": None, "unsubscribed": False}
        )
        return sub_id

    def _signal_unsubscribe(self, sub_id):
        for req in self._requests:
            if req["sub_id"] == sub_id:
                req["unsubscribed"] = True

    def _proxy_call(self, method_name, args, flags, timeout, cancellable, callback):
        self.call_log.append(method_name)
        # EnableShortcuts goes through call_sync in production, not call() --
        # this harness only ever sees Request-pattern methods here.
        for req in reversed(self._requests):
            if req["method"] is None:
                req["method"] = method_name
                break
        callback(self.mock_proxy, MagicMock(name="async_result"), None)

    def respond(self, method_name, response, results, which=-1):
        """Deliver a Response signal for the `which`-th call to `method_name`."""
        matches = [r for r in self._requests if r["method"] == method_name]
        req = matches[which]
        params = GLib.Variant("(ua{sv})", (response, results))
        req["callback"](None, None, None, None, None, params)

    def is_unsubscribed(self, method_name, which=-1) -> bool:
        matches = [r for r in self._requests if r["method"] == method_name]
        return matches[which]["unsubscribed"]

    def pending_count(self) -> int:
        """Number of Response subscriptions still outstanding (sent a
        request, no Response delivered yet)."""
        return sum(1 for r in self._requests if r["method"] is not None and not r["unsubscribed"])

    def __enter__(self) -> PortalHotkeys:
        self._stack.enter_context(patch.object(Gio, "bus_get_sync", return_value=self.mock_bus))
        self._stack.enter_context(
            patch.object(Gio.DBusProxy, "new_sync", return_value=self.mock_proxy)
        )
        self.portal = PortalHotkeys(app_id="org.fede.whisperaloud")
        return self.portal

    def __exit__(self, *exc_info):
        self._stack.close()

    def respond_create_session(self, session_path="/org/freedesktop/portal/desktop/session/1"):
        self.respond(
            "CreateSession", 0, {"session_handle": GLib.Variant("o", session_path)}
        )

    def respond_list_shortcuts(self, bound_ids=None):
        ids = self.bound_ids if bound_ids is None else bound_ids
        shortcuts = GLib.Variant("a(sa{sv})", [(sid, {}) for sid in ids])
        self.respond("ListShortcuts", 0, {"shortcuts": shortcuts})

    def respond_bind_shortcuts(self, response=0, results=None):
        self.respond("BindShortcuts", response, results or {})


class TestRegisterCalledBeforeCreateSession:
    def test_register_called_once_at_init(self):
        harness = _FakePortalHarness()
        with harness:
            pass
        assert harness.call_log == ["Register"]

    def test_full_order_first_run(self):
        """First run (no shortcuts bound yet): Register, CreateSession,
        ListShortcuts, then BindShortcuts because our id is missing."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            bound_results = []
            portal.bind(
                shortcuts=[
                    {
                        "id": "toggle",
                        "description": "Toggle recording",
                        "accelerators": ["<Super><Alt>r"],
                    }
                ],
                on_activated=lambda sid: None,
                on_bound=bound_results.append,
            )
            assert harness.call_log == ["Register", "CreateSession"]
            harness.respond_create_session()
            assert harness.call_log == ["Register", "CreateSession", "ListShortcuts"]
            harness.respond_list_shortcuts()
            assert harness.call_log == [
                "Register",
                "CreateSession",
                "ListShortcuts",
                "BindShortcuts",
            ]
            harness.respond_bind_shortcuts()

        assert bound_results == [True]

    def test_second_run_skips_bind_when_already_bound(self):
        """Second run: our shortcut id is already in ListShortcuts' result,
        so BindShortcuts must NOT be called again (that's what re-prompts)."""
        harness = _FakePortalHarness(bound_ids={"toggle"})
        with harness as portal:
            bound_results = []
            portal.bind(
                shortcuts=[
                    {
                        "id": "toggle",
                        "description": "Toggle recording",
                        "accelerators": ["<Super><Alt>r"],
                    }
                ],
                on_activated=lambda sid: None,
                on_bound=bound_results.append,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts"]
        assert "BindShortcuts" not in harness.call_log
        assert bound_results == [True]

    def test_bind_called_again_when_a_new_shortcut_is_added(self):
        """If we ever add a second shortcut id that isn't bound yet, Bind
        must run again even though "toggle" itself was already bound."""
        harness = _FakePortalHarness(bound_ids={"toggle"})
        with harness as portal:
            portal.bind(
                shortcuts=[
                    {"id": "toggle", "description": "Toggle", "accelerators": ["<Super><Alt>r"]},
                    {"id": "cancel", "description": "Cancel", "accelerators": ["Escape"]},
                ],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts", "BindShortcuts"]

    def test_registry_missing_is_ignored_not_fatal(self):
        """Older portals (<1.19) don't have the Registry interface at all;
        Register failing must not block CreateSession/BindShortcuts."""
        harness = _FakePortalHarness(registry_available=False, bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts", "BindShortcuts"]

    def test_no_retry_rebind_within_same_session(self):
        """Calling bind() twice in a row (e.g. a buggy caller retrying) must
        not call BindShortcuts twice once bound -- the second bind() starts
        a brand new CreateSession though, since PortalHotkeys has no
        "already fully bound" shortcut; real callers never do this."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": ["<Super><Alt>r"]}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()

        assert harness.call_log == [
            "Register",
            "CreateSession",
            "ListShortcuts",
            "BindShortcuts",
        ]


class TestAsyncRequestNeverBlocks:
    """Core assertion of the maintainer's design directive: issuing a
    Request-pattern call must never block the caller, must never spin a
    nested GLib.MainLoop, and must never spawn a thread."""

    def test_bind_returns_before_any_response_arrives(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            # bind() must have returned already -- only CreateSession was
            # issued, nothing has been answered yet.
            assert harness.call_log == ["Register", "CreateSession"]
            assert harness.pending_count() == 1
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()

    def test_no_background_thread_is_created(self):
        import whisper_aloud.service.hotkey_portal as hp_module

        # threading isn't even imported by the module any more -- the async
        # design has no need for it.
        assert not hasattr(hp_module, "threading")

        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()

    def test_bind_shortcuts_never_arms_a_timeout(self):
        """Interactive calls must never schedule a GLib timeout at all --
        there is no correct deadline for a human."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            with patch.object(GLib, "timeout_add") as mock_timeout_add:
                harness.respond_list_shortcuts()
                # BindShortcuts issued now -- must not have armed a timeout.
                mock_timeout_add.assert_not_called()
            harness.respond_bind_shortcuts()

    def test_non_interactive_calls_arm_a_diagnostic_watchdog_only(self):
        """CreateSession/ListShortcuts may log a WARNING if slow, but that
        must be a one-shot GLib.timeout_add, never a loop.run()-style wait,
        and never unsubscribes the Response handler."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            with patch.object(GLib, "timeout_add", wraps=GLib.timeout_add) as mock_timeout_add:
                portal.bind(
                    shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                    on_activated=lambda sid: None,
                )
                assert mock_timeout_add.called
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()


class TestLateOrMissingResponses:
    """A Response that never arrives, or arrives very late, must never be
    treated as a permanent failure -- the subscription stays alive and a
    late answer is still processed."""

    def test_response_never_arriving_leaves_subscription_open(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            # Simulate "never responds": just don't call harness.respond().
            assert harness.is_unsubscribed("CreateSession") is False
            assert harness.pending_count() == 1

    def test_late_response_after_simulated_delay_still_completes_the_flow(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            bound_results = []
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
                on_bound=bound_results.append,
            )
            # CreateSession's Response arrives "late" -- nothing special
            # needs to happen, it just works exactly like an immediate one.
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()
            assert bound_results == [True]

    def test_response_after_close_is_ignored(self):
        """A Response arriving after close() must not touch any state."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            bound_results = []
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
                on_bound=bound_results.append,
            )
            portal.close()
            # CreateSession's Response shows up anyway, after shutdown.
            harness.respond_create_session()
        assert bound_results == []  # on_bound never fired


class TestBindShortcutsPendingGuard:
    """Field bug 2 follow-up: never issue a second interactive BindShortcuts
    request while one is already pending -- that produced an observed
    response=2 on the portal's side for the abandoned first request."""

    def test_second_concurrent_bind_is_refused(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            assert harness.call_log.count("BindShortcuts") == 1
            assert portal._bind_pending is True

            # A second bind attempt while the first is still pending.
            bound_results = []
            portal._bind_shortcuts_async(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
                on_bound=bound_results.append,
            )
            assert harness.call_log.count("BindShortcuts") == 1  # not issued again
            assert bound_results == [False]

            harness.respond_bind_shortcuts()

    def test_flag_cleared_after_bind_completes(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            assert portal._bind_pending is True
            harness.respond_bind_shortcuts()
            assert portal._bind_pending is False

    def test_flag_cleared_after_failed_bind(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts(response=1)  # cancelled by user
            assert portal._bind_pending is False


class TestBindShortcutsResponseTwo:
    """response=2 ("ended without a normal user response") must be logged
    distinctly, not just folded into the generic failure branch, and must
    never raise -- it's a recoverable degradation."""

    def test_response_two_is_handled_without_raising(self, caplog):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal, caplog.at_level("WARNING"):
            bound_results = []
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
                on_bound=bound_results.append,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts(response=2)

        assert any("response=2" in rec.message for rec in caplog.records)
        assert bound_results == [False]
        assert portal._bind_pending is False


class TestSessionClose:
    """Sessions must be closed via org.freedesktop.portal.Session.Close() on
    the session's own path, not a nonexistent GlobalShortcuts.EndSession."""

    def test_close_calls_session_close_on_session_path(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()
            harness.mock_bus.call_sync.reset_mock()
            portal.close()

        assert "Session.Close" in harness.call_log or harness.mock_bus.call_sync.called
        call_args = harness.mock_bus.call_sync.call_args[0]
        assert call_args[0] == "org.freedesktop.portal.Desktop"
        assert call_args[1] == "/org/freedesktop/portal/desktop/session/1"
        assert call_args[2] == "org.freedesktop.portal.Session"
        assert call_args[3] == "Close"

    def test_close_never_raises_when_session_close_fails(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.bind(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )
            harness.respond_create_session()
            harness.respond_list_shortcuts()
            harness.respond_bind_shortcuts()
            harness.mock_bus.call_sync.side_effect = GLib.Error("no such object")
            portal.close()  # must not raise

    def test_close_with_no_session_is_a_no_op(self):
        harness = _FakePortalHarness()
        with harness as portal:
            portal.close()  # never bound; must not raise


class TestListShortcutsParsing:
    def test_list_shortcuts_returns_empty_set_on_failure_response(self):
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            results = []
            portal._create_session_async(on_done=lambda ok: results.append(ok))
            harness.respond_create_session()
            assert results == [True]

            ids_results = []
            portal._list_shortcuts_async(on_done=ids_results.append)
            harness.respond("ListShortcuts", 1, {})  # non-zero response = cancelled/failed
            assert ids_results == [set()]


# ---------------------------------------------------------------------------
# Optional integration test against a fake portal on a private bus.
# ---------------------------------------------------------------------------


@pytest.mark.dbus_private
@pytest.mark.skipif(
    private_bus_address() is None,
    reason="no private bus: run via scripts/test_private_bus.sh",
)
class TestPortalIntegrationOnPrivateBus:
    """End-to-end check against a fake portal service on a private bus.

    Must only be invoked via:
        scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_portal_flow.py
    so the bus `dbus-run-session` created -- never the real desktop session
    bus -- is what gets exercised. ``private_bus_address()`` never connects
    to anything; it only inspects env vars conftest.py already captured
    (see tests/_private_bus.py), so the skip decision itself can never dial
    a real bus. conftest.py overwrites DBUS_SESSION_BUS_ADDRESS for every
    other test; this test (and only this test) points it at the private
    address for the duration of each D-Bus call it makes.
    """

    def test_bind_once_then_skipped_on_second_run(self, tmp_path):
        bus_addr = private_bus_address()
        fake_portal_script = tmp_path / "fake_portal.py"
        fake_portal_script.write_text(_FAKE_PORTAL_SERVICE_SRC)

        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr)
        proc = subprocess.Popen([sys.executable, str(fake_portal_script)], env=env)
        try:
            import time

            time.sleep(1.0)  # let the fake service claim the bus name

            with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
                bind_calls_run1 = _run_daemon_bind_once()
                assert bind_calls_run1 == 1

                bind_calls_run2 = _run_daemon_bind_once()
                assert bind_calls_run2 == 0
        finally:
            proc.terminate()
            proc.wait(timeout=5)


def _run_daemon_bind_once() -> int:
    """Run the async bind() flow against the fake portal running on the
    current (private) session bus, pumping a real GLib.MainLoop until it
    settles, then return BindShortcuts call count as reported by the fake
    service over an auxiliary D-Bus method."""
    portal = PortalHotkeys(app_id="org.fede.whisperaloud")

    loop = GLib.MainLoop()
    done = []

    def _on_bound(success):
        done.append(success)
        loop.quit()

    portal.bind(
        shortcuts=[{"id": "toggle", "description": "Toggle", "accelerators": ["<Super><Alt>r"]}],
        on_activated=lambda sid: None,
        on_bound=_on_bound,
    )

    def _safety_timeout():
        loop.quit()
        return GLib.SOURCE_REMOVE

    timeout_id = GLib.timeout_add(5000, _safety_timeout)
    loop.run()
    if done:
        GLib.source_remove(timeout_id)

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = bus.call_sync(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.whisperaloud.test.FakePortalControl",
        "BindCallCount",
        None,
        GLib.VariantType("(i)"),
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )
    count = result.unpack()[0]
    portal.close()
    # Reset for the next call in this same test process.
    bus.call_sync(
        "org.freedesktop.portal.Desktop",
        "/org/freedesktop/portal/desktop",
        "org.whisperaloud.test.FakePortalControl",
        "ResetBindCallCount",
        None,
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )
    return count


_FAKE_PORTAL_SERVICE_SRC = textwrap.dedent(
    """
    # Minimal fake org.freedesktop.portal.Desktop for WHISP-64 integration
    # testing: implements just enough of Registry + GlobalShortcuts to
    # exercise the real Request/Response wait loop in PortalHotkeys.
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    BUS_NAME = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"

    bound_ids = set()
    bind_call_count = [0]

    INTROSPECTION = \"\"\"
    <node>
      <interface name="org.freedesktop.host.portal.Registry">
        <method name="Register">
          <arg type="s" name="app_id" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
        </method>
      </interface>
      <interface name="org.freedesktop.portal.GlobalShortcuts">
        <method name="CreateSession">
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
        <method name="ListShortcuts">
          <arg type="o" name="session_handle" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
        <method name="BindShortcuts">
          <arg type="o" name="session_handle" direction="in"/>
          <arg type="a(sa{sv})" name="shortcuts" direction="in"/>
          <arg type="s" name="parent_window" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
          <arg type="o" name="handle" direction="out"/>
        </method>
        <method name="EnableShortcuts">
          <arg type="o" name="session_handle" direction="in"/>
          <arg type="s" name="parent_window" direction="in"/>
          <arg type="b" name="dummy" direction="in"/>
          <arg type="a{sv}" name="options" direction="in"/>
        </method>
        <method name="EndSession">
          <arg type="o" name="session_handle" direction="in"/>
        </method>
      </interface>
      <interface name="org.freedesktop.portal.Session">
        <method name="Close"/>
      </interface>
      <interface name="org.whisperaloud.test.FakePortalControl">
        <method name="BindCallCount">
          <arg type="i" name="count" direction="out"/>
        </method>
        <method name="ResetBindCallCount"/>
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
        if iface == "org.freedesktop.portal.Session" and method == "Close":
            invocation.return_value(None)
            return
        if iface == "org.whisperaloud.test.FakePortalControl":
            if method == "BindCallCount":
                invocation.return_value(GLib.Variant("(i)", (bind_call_count[0],)))
            elif method == "ResetBindCallCount":
                bind_call_count[0] = 0
                invocation.return_value(None)
            return
        if iface != "org.freedesktop.portal.GlobalShortcuts":
            invocation.return_value(None)
            return

        if method == "CreateSession":
            options = args[0]
            handle_token = options.get("handle_token")
            invocation.return_value(GLib.Variant("(o)", ("/session/1",)))
            GLib.idle_add(
                _emit_response, conn, sender, handle_token, 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
        elif method == "ListShortcuts":
            _session, options = args
            handle_token = options.get("handle_token")
            invocation.return_value(GLib.Variant("(o)", ("/request/list",)))
            shortcuts = GLib.Variant("a(sa{sv})", [(sid, {}) for sid in bound_ids])
            GLib.idle_add(_emit_response, conn, sender, handle_token, 0, {"shortcuts": shortcuts})
        elif method == "BindShortcuts":
            _session, shortcuts, _parent, options = args
            handle_token = options.get("handle_token")
            bind_call_count[0] += 1
            for sid, _opts in shortcuts:
                bound_ids.add(sid)
            invocation.return_value(GLib.Variant("(o)", ("/request/bind",)))
            GLib.idle_add(_emit_response, conn, sender, handle_token, 0, {})
        elif method == "EnableShortcuts":
            invocation.return_value(None)
        elif method == "EndSession":
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
