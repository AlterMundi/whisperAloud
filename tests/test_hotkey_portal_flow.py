"""Tests for the WHISP-64 portal registration/bind flow.

Verifies the call order the daemon needs to avoid re-prompting
"Add Keyboard Shortcuts" on every start:

    Register -> CreateSession -> ListShortcuts -> (BindShortcuts only if
    our shortcut id is absent from ListShortcuts' result)

All Gio/GLib bus traffic is mocked (bus_get_sync, DBusProxy.new_sync, and
the Request::Response wait loop in PortalHotkeys._call_request) so these
tests never open a real D-Bus connection and never touch the real
xdg-desktop-portal -- which would pop a real consent dialog on the
developer's desktop (see conftest.py).

An optional integration-level test further down publishes a *fake*
org.freedesktop.portal.Desktop (GlobalShortcuts + host Registry) on a
private, test-only bus started via `dbus-run-session`, and is marked
`dbus_private` so it only runs when explicitly requested, e.g.:

    timeout 60 dbus-run-session -- python -m pytest -m dbus_private \\
        tests/test_hotkey_portal_flow.py -q
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

from whisper_aloud.service.hotkey_portal import PortalHotkeys  # noqa: E402


class _FakePortalHarness:
    """Patches Gio bus/proxy construction and the Request/Response wait
    loop so PortalHotkeys can be driven with no real D-Bus connection.

    `call_log` records, in order, every portal-facing call made:
    "Register", "CreateSession", "ListShortcuts", "BindShortcuts".
    """

    def __init__(self, registry_available: bool = True, bound_ids=None):
        self.call_log: list[str] = []
        self.registry_available = registry_available
        self.bound_ids = bound_ids or set()
        self._stack = ExitStack()

        self.mock_bus = MagicMock(name="bus")
        self.mock_bus.get_unique_name.return_value = ":1.99"
        self.mock_bus.call_sync.side_effect = self._bus_call_sync
        self.mock_proxy = MagicMock(name="proxy")

    def _bus_call_sync(
        self, bus_name, path, iface, method, args, reply_type, flags, timeout, cancellable
    ):
        if iface == "org.freedesktop.host.portal.Registry" and method == "Register":
            self.call_log.append("Register")
            if not self.registry_available:
                raise GLib.Error("no such interface org.freedesktop.host.portal.Registry")
            return None
        raise AssertionError(f"unexpected bus.call_sync: {iface}.{method}")

    def _fake_call_request(self, method_name, args, timeout_ms=5000):
        if method_name == "CreateSession":
            self.call_log.append("CreateSession")
            return 0, {
                "session_handle": GLib.Variant("o", "/org/freedesktop/portal/desktop/session/1")
            }
        if method_name == "ListShortcuts":
            self.call_log.append("ListShortcuts")
            shortcuts = [(sid, {}) for sid in self.bound_ids]
            return 0, {"shortcuts": shortcuts}
        if method_name == "BindShortcuts":
            self.call_log.append("BindShortcuts")
            return 0, {}
        raise AssertionError(f"unexpected _call_request: {method_name}")

    def __enter__(self) -> PortalHotkeys:
        self._stack.enter_context(patch.object(Gio, "bus_get_sync", return_value=self.mock_bus))
        self._stack.enter_context(
            patch.object(Gio.DBusProxy, "new_sync", return_value=self.mock_proxy)
        )
        self._stack.enter_context(
            patch.object(PortalHotkeys, "_call_request", self._fake_call_request)
        )
        self.portal = PortalHotkeys(app_id="org.fede.whisperaloud")
        return self.portal

    def __exit__(self, *exc_info):
        self._stack.close()


class TestRegisterCalledBeforeCreateSession:
    def test_register_called_once_at_init(self):
        """Registry.Register must happen during __init__, before any other
        portal call, per the xdg-desktop-portal >=1.19 handshake."""
        harness = _FakePortalHarness()
        with harness:
            pass
        assert harness.call_log == ["Register"]

    def test_full_order_first_run(self):
        """First run (no shortcuts bound yet): Register, CreateSession,
        ListShortcuts, then BindShortcuts because our id is missing."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.create_session()
            portal.bind_shortcuts_if_needed(
                shortcuts=[
                    {
                        "id": "toggle",
                        "description": "Toggle recording",
                        "accelerators": ["<Super><Alt>r"],
                    }
                ],
                on_activated=lambda sid: None,
            )

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts", "BindShortcuts"]

    def test_second_run_skips_bind_when_already_bound(self):
        """Second run: our shortcut id is already in ListShortcuts' result,
        so BindShortcuts must NOT be called again (that's what re-prompts)."""
        harness = _FakePortalHarness(bound_ids={"toggle"})
        with harness as portal:
            portal.create_session()
            portal.bind_shortcuts_if_needed(
                shortcuts=[
                    {
                        "id": "toggle",
                        "description": "Toggle recording",
                        "accelerators": ["<Super><Alt>r"],
                    }
                ],
                on_activated=lambda sid: None,
            )

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts"]
        assert "BindShortcuts" not in harness.call_log

    def test_bind_called_again_when_a_new_shortcut_is_added(self):
        """If we ever add a second shortcut id that isn't bound yet, Bind
        must run again even though "toggle" itself was already bound."""
        harness = _FakePortalHarness(bound_ids={"toggle"})
        with harness as portal:
            portal.create_session()
            portal.bind_shortcuts_if_needed(
                shortcuts=[
                    {"id": "toggle", "description": "Toggle", "accelerators": ["<Super><Alt>r"]},
                    {"id": "cancel", "description": "Cancel", "accelerators": ["Escape"]},
                ],
                on_activated=lambda sid: None,
            )

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts", "BindShortcuts"]

    def test_registry_missing_is_ignored_not_fatal(self):
        """Older portals (<1.19) don't have the Registry interface at all;
        Register failing must not block CreateSession/BindShortcuts."""
        harness = _FakePortalHarness(registry_available=False, bound_ids=set())
        with harness as portal:
            portal.create_session()
            portal.bind_shortcuts_if_needed(
                shortcuts=[{"id": "toggle", "description": "", "accelerators": []}],
                on_activated=lambda sid: None,
            )

        assert harness.call_log == ["Register", "CreateSession", "ListShortcuts", "BindShortcuts"]

    def test_no_retry_rebind_within_same_session(self):
        """Calling bind_shortcuts_if_needed twice in a row (e.g. a buggy
        caller retrying) must not call BindShortcuts twice once bound."""
        harness = _FakePortalHarness(bound_ids=set())
        with harness as portal:
            portal.create_session()
            shortcuts = [{"id": "toggle", "description": "", "accelerators": ["<Super><Alt>r"]}]
            portal.bind_shortcuts_if_needed(shortcuts=shortcuts, on_activated=lambda sid: None)
            # Simulate the portal now reporting it as bound (as it would on
            # a subsequent ListShortcuts call within the same session).
            harness.bound_ids = {"toggle"}
            portal.bind_shortcuts_if_needed(shortcuts=shortcuts, on_activated=lambda sid: None)

        assert harness.call_log == [
            "Register",
            "CreateSession",
            "ListShortcuts",
            "BindShortcuts",
            "ListShortcuts",
        ]


class TestListShortcutsParsing:
    def test_list_shortcuts_returns_empty_set_on_failure_response(self):
        harness = _FakePortalHarness()

        def failing_call_request(self, method_name, args, timeout_ms=5000):
            if method_name == "CreateSession":
                return 0, {"session_handle": GLib.Variant("o", "/s/1")}
            if method_name == "ListShortcuts":
                return 1, {}  # non-zero response = cancelled/failed
            raise AssertionError(method_name)

        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
            patch.object(PortalHotkeys, "_call_request", failing_call_request),
        ):
            portal = PortalHotkeys(app_id="org.fede.whisperaloud")
            portal.create_session()
            assert portal.list_shortcuts() == set()


# ---------------------------------------------------------------------------
# Optional integration test against a fake portal on a private bus.
# ---------------------------------------------------------------------------


def _candidate_private_bus_address() -> str:
    """The bus address this process actually started with (e.g. the
    throwaway bus from `dbus-run-session`), stashed by conftest.py before
    it clobbers DBUS_SESSION_BUS_ADDRESS for every other test. Empty string
    if conftest.py didn't run (e.g. this module imported standalone)."""
    return os.environ.get("_WHISPER_ALOUD_TEST_ORIGINAL_DBUS_ADDR", "")


def _private_bus_is_safe() -> bool:
    """True only when the bus this process actually started on is clearly
    NOT the real user desktop session bus, per the WHISP-64 brief's
    self-skip guard. Never looks at DBUS_SESSION_BUS_ADDRESS directly --
    conftest.py has already overwritten that to a nonexistent path for
    every test in this suite; this test is the one sanctioned exception
    that reads the original value conftest.py stashed."""
    addr = _candidate_private_bus_address()
    if not addr:
        return False
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    real_addr = f"unix:path={runtime_dir}/bus" if runtime_dir else None
    if real_addr and addr == real_addr:
        return False
    try:
        from gi.repository import Gio

        with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": addr}):
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant.new_tuple(GLib.Variant("s", "org.freedesktop.systemd1")),
                None,
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
        # A reply means systemd1 IS owned on this bus -> looks like a real
        # user session bus, not a scratch dbus-run-session bus.
        return False
    except Exception:
        return True


@pytest.mark.dbus_private
@pytest.mark.skipif(
    not _private_bus_is_safe(), reason="refusing to run against what looks like a real session bus"
)
class TestPortalIntegrationOnPrivateBus:
    """End-to-end check against a fake portal service on a private bus.

    Must only be invoked via:
        timeout 60 dbus-run-session -- python -m pytest -m dbus_private ...
    so the bus `dbus-run-session` created -- never the real desktop session
    bus -- is what gets exercised. conftest.py overwrites
    DBUS_SESSION_BUS_ADDRESS for every other test; this test (and only this
    test) restores it to the real-for-this-process value for the duration
    of each D-Bus call it makes.
    """

    def test_bind_once_then_skipped_on_second_run(self, tmp_path):
        bus_addr = _candidate_private_bus_address()
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
    """Run create_session + bind_shortcuts_if_needed against the fake portal
    running on the current (private) session bus, return BindShortcuts call
    count as reported by the fake service over an auxiliary D-Bus method."""
    portal = PortalHotkeys(app_id="org.fede.whisperaloud")
    portal.create_session()
    portal.bind_shortcuts_if_needed(
        shortcuts=[{"id": "toggle", "description": "Toggle", "accelerators": ["<Super><Alt>r"]}],
        on_activated=lambda sid: None,
    )
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
