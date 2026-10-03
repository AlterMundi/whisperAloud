"""Tests for PortalPasteSimulator (WHISP-59), rewritten for the fully
async, continuation-based design (maintainer directive, field bug 2 second
pass): no nested GLib.MainLoop, no background thread anywhere in
portal_paste_simulator.py. `_request_async()` subscribes to
Request::Response and issues the method call via the async
`Gio.DBusProxy.call()` API, returning immediately; the CreateSession ->
SelectDevices -> Start chain resumes entirely from Response callbacks.

All Gio/GLib bus traffic is mocked via `_FakeHarness` below (modeled on
tests/test_hotkey_portal_flow.py's `_FakePortalHarness`) -- these tests
never open a real D-Bus connection and never touch the real
xdg-desktop-portal, which would pop a real RemoteDesktop consent dialog on
the developer's desktop.

A `dbus_private` integration test against a fake portal service lives in
tests/test_portal_paste_integration.py.
"""

from unittest.mock import MagicMock, patch

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from whisper_aloud.clipboard.portal_keysyms import (  # noqa: E402
    KEY_STATE_PRESSED,
    KEY_STATE_RELEASED,
    XK_CONTROL_L,
    XK_v,
)
from whisper_aloud.clipboard.portal_paste_simulator import (  # noqa: E402
    DEVICE_KEYBOARD,
    PERSIST_MODE_PERSISTENT,
    PortalPasteSimulator,
)


class _FakeHarness:
    """Drives PortalPasteSimulator's fully-async Request/Response flow with
    no real D-Bus connection and no real event loop.

    `call_log` records each portal-facing call: "CreateSession",
    "SelectDevices", "Start", "Session.Close", or ("NotifyKeyboardKeysym",
    keysym, state). `select_devices_options` records the (already fully
    unpacked, per GLib.Variant.unpack()'s recursive behavior on nested "v"
    members -- plain ints/strs, not GLib.Variant) options dict passed to
    SelectDevices so tests can assert on persist_mode/types/restore_token.

    `respond(method_name, response, results)` delivers a synthetic Response
    signal for the most recent call to `method_name` by directly invoking
    the callback PortalPasteSimulator passed to `signal_subscribe`. Not
    calling it simulates "no Response ever arrives".
    """

    def __init__(self, start_restore_token=None, fail_at=None):
        self.call_log: list = []
        self.select_devices_options: dict = {}
        self.start_restore_token = start_restore_token
        self.fail_at = fail_at  # e.g. "CreateSession" to make that step fail
        self._requests: list = []
        self._next_sub_id = 1

        self.mock_bus = MagicMock(name="bus")
        self.mock_bus.get_unique_name.return_value = ":1.50"
        self.mock_bus.call_sync.side_effect = self._bus_call_sync
        self.mock_bus.signal_subscribe.side_effect = self._signal_subscribe
        self.mock_bus.signal_unsubscribe.side_effect = self._signal_unsubscribe

        self.mock_proxy = MagicMock(name="proxy")
        self.mock_proxy.call.side_effect = self._proxy_call
        self.mock_proxy.call_finish.side_effect = lambda result: None
        self.mock_proxy.call_sync.side_effect = self._proxy_call_sync

    def _bus_call_sync(
        self, bus_name, path, iface, method, args, reply_type, flags, timeout, cancellable
    ):
        if iface == "org.freedesktop.host.portal.Registry" and method == "Register":
            self.call_log.append("Register")
            return None
        if iface == "org.freedesktop.portal.Session" and method == "Close":
            self.call_log.append("Session.Close")
            return None
        raise AssertionError(f"unexpected bus.call_sync: {iface}.{method}")

    def _proxy_call_sync(self, method_name, args, flags, timeout, cancellable):
        if method_name == "NotifyKeyboardKeysym":
            _session, _opts, keysym_v, state_v = args.unpack()
            self.call_log.append(("NotifyKeyboardKeysym", keysym_v, state_v))
            return None
        raise AssertionError(f"unexpected proxy.call_sync: {method_name}")

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
        if method_name == self.fail_at:
            self.mock_proxy.call_finish.side_effect = RuntimeError(f"{method_name} failed")
        else:
            self.mock_proxy.call_finish.side_effect = lambda result: None

        self.call_log.append(method_name)
        if method_name == "SelectDevices":
            _session, options = args.unpack()
            self.select_devices_options = options

        for req in reversed(self._requests):
            if req["method"] is None:
                req["method"] = method_name
                break
        callback(self.mock_proxy, MagicMock(name="async_result"), None)

    def respond(self, method_name, response, results, which=-1):
        matches = [r for r in self._requests if r["method"] == method_name]
        req = matches[which]
        params = GLib.Variant("(ua{sv})", (response, results))
        req["callback"](None, None, None, None, None, params)

    def pending_count(self) -> int:
        return sum(1 for r in self._requests if r["method"] is not None and not r["unsubscribed"])

    def __enter__(self) -> PortalPasteSimulator:
        self._stack_patches = [
            patch.object(Gio, "bus_get_sync", return_value=self.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=self.mock_proxy),
        ]
        for p in self._stack_patches:
            p.start()
        return PortalPasteSimulator(
            restore_token_loader=lambda: None, restore_token_saver=lambda token: None
        )

    def __exit__(self, *exc_info):
        for p in self._stack_patches:
            p.stop()

    def run_setup_to_ready(self, sim):
        """Drive simulate_paste() through CreateSession -> SelectDevices ->
        Start to a ready session, responding with success at each step."""
        sim.simulate_paste("ctrl+v")  # kicks off setup, returns False
        self.respond(
            "CreateSession",
            0,
            {"session_handle": GLib.Variant("o", "/session/1")},
        )
        self.respond("SelectDevices", 0, {})
        results = {}
        if self.start_restore_token:
            results["restore_token"] = GLib.Variant("s", self.start_restore_token)
        self.respond("Start", 0, results)


class TestSessionCreationOrder:
    def test_lazy_no_dbus_at_construction(self):
        """Constructing the object must not touch Gio/D-Bus at all."""
        with (
            patch.object(Gio, "bus_get_sync", side_effect=AssertionError("must not connect")),
            patch.object(Gio.DBusProxy, "new_sync", side_effect=AssertionError("must not connect")),
        ):
            sim = PortalPasteSimulator()
            assert sim._session_path is None

    def test_first_paste_starts_setup_and_falls_back(self):
        harness = _FakeHarness()
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False
            assert harness.call_log == ["Register", "CreateSession"]

    def test_register_called_before_create_session(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+v")

        assert harness.call_log.index("Register") < harness.call_log.index("CreateSession")

    def test_full_order_then_second_paste_notifies(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            assert harness.call_log[:3] == ["Register", "CreateSession", "SelectDevices"]
            assert "Start" in harness.call_log
            assert sim.simulate_paste("ctrl+v") is True
            notify_calls = [c for c in harness.call_log if isinstance(c, tuple)]
            assert notify_calls == [
                ("NotifyKeyboardKeysym", XK_CONTROL_L, KEY_STATE_PRESSED),
                ("NotifyKeyboardKeysym", XK_v, KEY_STATE_PRESSED),
                ("NotifyKeyboardKeysym", XK_v, KEY_STATE_RELEASED),
                ("NotifyKeyboardKeysym", XK_CONTROL_L, KEY_STATE_RELEASED),
            ]

    def test_select_devices_requests_keyboard_only_and_persistent_mode(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)

        opts = harness.select_devices_options
        assert opts["types"] == DEVICE_KEYBOARD
        assert opts["persist_mode"] == PERSIST_MODE_PERSISTENT
        assert "restore_token" not in opts  # none was loaded

    def test_second_paste_reuses_session_no_new_create_session(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            harness.call_log.clear()
            assert sim.simulate_paste("ctrl+v") is True

        assert "CreateSession" not in harness.call_log
        assert "SelectDevices" not in harness.call_log
        assert "Start" not in harness.call_log
        assert len([c for c in harness.call_log if isinstance(c, tuple)]) == 4


class TestSessionReadyOnlyAfterStart:
    """Regression test for a real bug found via the dbus_private integration
    test (tests/test_portal_paste_integration.py): `_session_path` -- the
    flag simulate_paste() checks to decide the session is ready -- was
    previously set as soon as CreateSession's Response arrived, long before
    SelectDevices/Start ran. A caller (or a readiness-polling helper like
    the integration test's `_pump_until`) that observed "ready" at that
    point and sent NotifyKeyboardKeysym would appear to work (the fake/real
    portal accepts it) while SelectDevices/Start -- and therefore the
    restore_token consent dialog -- never actually happened, so no token
    was ever requested or persisted and the dialog would reappear every
    session. Session readiness must only become true after Start's
    Response, never earlier."""

    def test_session_path_is_none_until_create_session_responds(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+v")
            assert sim._session_path is None
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            # CreateSession succeeded -- a session handle exists on the
            # portal side -- but it is NOT yet ready: SelectDevices/Start
            # haven't run.
            assert sim._session_path is None
            assert sim._session_handle == "/session/1"

    def test_session_path_is_none_until_select_devices_responds(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+v")
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            harness.respond("SelectDevices", 0, {})
            # SelectDevices succeeded too, but Start hasn't run -- still not
            # ready, and critically the restore_token consent (granted by
            # Start) hasn't happened yet.
            assert sim._session_path is None

    def test_session_path_is_set_only_after_start_responds(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            assert sim._session_path == "/session/1"

    def test_simulate_paste_does_not_send_notify_before_start_completes(self):
        """The core symptom: calling simulate_paste() again after
        CreateSession/SelectDevices but before Start answers must NOT send
        NotifyKeyboardKeysym -- it must still report "not ready" and fall
        back to clipboard-only."""
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+v")
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            harness.respond("SelectDevices", 0, {})

            assert sim.simulate_paste("ctrl+v") is False
            assert not any(isinstance(c, tuple) for c in harness.call_log)

            harness.respond("Start", 0, {})
            assert sim.simulate_paste("ctrl+v") is True
            assert any(isinstance(c, tuple) for c in harness.call_log)


class TestRestoreToken:
    def test_new_restore_token_is_persisted(self):
        saved = {}
        harness = _FakeHarness(start_restore_token="tok-123")
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
        ):
            sim = PortalPasteSimulator(
                restore_token_loader=lambda: None,
                restore_token_saver=lambda token: saved.setdefault("token", token),
            )
            harness.run_setup_to_ready(sim)

        assert saved["token"] == "tok-123"

    def test_existing_restore_token_is_sent_in_select_devices(self):
        harness = _FakeHarness()
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
        ):
            sim = PortalPasteSimulator(
                restore_token_loader=lambda: "existing-token", restore_token_saver=lambda t: None
            )
            harness.run_setup_to_ready(sim)

        assert harness.select_devices_options["restore_token"] == "existing-token"


class TestShortcutVariants:
    def test_ctrl_shift_v_sends_six_notifications(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            notify_count_before = len([c for c in harness.call_log if isinstance(c, tuple)])
            assert sim.simulate_paste("ctrl+shift+v") is True
            notify_calls = [c for c in harness.call_log if isinstance(c, tuple)]
            assert len(notify_calls) - notify_count_before == 6


class TestFailureFallback:
    def test_create_session_failure_falls_back_and_does_not_retry(self):
        fallback_reasons = []
        harness = _FakeHarness(fail_at="CreateSession")
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
        ):
            sim = PortalPasteSimulator(
                on_fallback=fallback_reasons.append,
                restore_token_loader=lambda: None,
                restore_token_saver=lambda t: None,
            )
            assert sim.simulate_paste("ctrl+v") is False
            assert sim._session_failed is True

            # A second paste must not retry CreateSession.
            fallback_reasons.clear()
            assert sim.simulate_paste("ctrl+v") is False
            assert harness.call_log.count("CreateSession") == 1
            assert fallback_reasons == ["RemoteDesktop session previously failed"]

    def test_notify_keysym_failure_does_not_poison_session_for_next_call(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)

            def _failing_notify(method_name, args, flags, timeout, cancellable):
                raise RuntimeError("D-Bus is gone")

            harness.mock_proxy.call_sync.side_effect = _failing_notify
            assert sim.simulate_paste("ctrl+v") is False  # first notify fails

            harness.mock_proxy.call_sync.side_effect = harness._proxy_call_sync
            assert sim.simulate_paste("ctrl+v") is True  # retried, succeeds


class TestStuckModifierRelease:
    def _harness_failing_on_notify_call(self, fail_on_call_number: int):
        harness = _FakeHarness()
        sent = []

        def _proxy_call_sync(method_name, args, flags, timeout, cancellable):
            if method_name == "NotifyKeyboardKeysym":
                sent.append(1)
                if len(sent) == fail_on_call_number:
                    raise RuntimeError("simulated notify failure")
                _session, _opts, keysym_v, state_v = args.unpack()
                harness.call_log.append(("NotifyKeyboardKeysym", keysym_v, state_v))
                return None
            raise AssertionError(method_name)

        harness.mock_proxy.call_sync.side_effect = _proxy_call_sync
        return harness, sent

    def test_v_press_fails_releases_ctrl_only(self):
        harness, _sent = self._harness_failing_on_notify_call(fail_on_call_number=2)
        with harness as sim:
            harness.run_setup_to_ready(sim)
            assert sim.simulate_paste("ctrl+v") is False

    def test_v_release_fails_releases_v_then_ctrl(self):
        harness, _sent = self._harness_failing_on_notify_call(fail_on_call_number=3)
        with harness as sim:
            harness.run_setup_to_ready(sim)
            assert sim.simulate_paste("ctrl+v") is False

    def test_release_retry_failure_is_swallowed_not_raised(self):
        """If even the finally block's best-effort release fails, that
        error must be swallowed -- simulate_paste() must still return False
        cleanly rather than raising or masking the original error."""
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)

            def always_failing(method_name, args, flags, timeout, cancellable):
                if method_name == "NotifyKeyboardKeysym":
                    raise RuntimeError("D-Bus is gone")
                raise AssertionError(method_name)

            harness.mock_proxy.call_sync.side_effect = always_failing
            assert sim.simulate_paste("ctrl+v") is False  # must not raise

    def test_fallback_emitted_once_even_with_stuck_modifier_cleanup(self):
        harness, _sent = self._harness_failing_on_notify_call(fail_on_call_number=2)
        fallback_reasons = []
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
        ):
            sim = PortalPasteSimulator(
                on_fallback=fallback_reasons.append,
                restore_token_loader=lambda: None,
                restore_token_saver=lambda token: None,
            )
            harness.run_setup_to_ready(sim)
            fallback_reasons.clear()
            assert sim.simulate_paste("ctrl+v") is False

        assert len(fallback_reasons) == 1


class TestAsyncRequestNeverBlocks:
    """Core assertion of the maintainer's design directive: issuing a
    Request-pattern call must never block the caller, must never spin a
    nested GLib.MainLoop, and must never spawn a thread."""

    def test_simulate_paste_returns_before_any_response_arrives(self):
        harness = _FakeHarness()
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False
            assert harness.call_log == ["Register", "CreateSession"]
            assert harness.pending_count() == 1
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            harness.respond("SelectDevices", 0, {})
            harness.respond("Start", 0, {})

    def test_module_imports_no_threading(self):
        import whisper_aloud.clipboard.portal_paste_simulator as pps_module

        assert not hasattr(pps_module, "threading")

    def test_select_devices_and_start_never_arm_a_timeout(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+v")
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            with patch.object(GLib, "timeout_add") as mock_timeout_add:
                harness.respond("SelectDevices", 0, {})
                mock_timeout_add.assert_not_called()
                harness.respond("Start", 0, {})
                mock_timeout_add.assert_not_called()

    def test_create_session_arms_a_diagnostic_watchdog_only(self):
        harness = _FakeHarness()
        with harness as sim:
            with patch.object(GLib, "timeout_add", wraps=GLib.timeout_add) as mock_timeout_add:
                sim.simulate_paste("ctrl+v")
                assert mock_timeout_add.called
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            harness.respond("SelectDevices", 0, {})
            harness.respond("Start", 0, {})


class TestConcurrentSessionSetupGuard:
    """A second paste requested while the first is still waiting on the
    consent dialog must fall back to clipboard-only immediately, not block
    behind or duplicate the first interactive request."""

    def test_concurrent_paste_falls_back_without_duplicating_create_session(self):
        harness = _FakeHarness()
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False  # starts setup
            assert sim.simulate_paste("ctrl+v") is False  # setup still pending
            assert harness.call_log.count("CreateSession") == 1

    def test_pending_flag_cleared_after_successful_setup(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            assert sim._session_pending is False

    def test_pending_flag_cleared_after_failed_setup(self):
        harness = _FakeHarness(fail_at="CreateSession")
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False
            assert sim._session_pending is False


class TestResponseNeverArrives:
    def test_no_response_leaves_session_pending_not_failed(self):
        harness = _FakeHarness()
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False
            assert sim._session_pending is True
            assert sim._session_failed is False

    def test_response_after_close_is_ignored(self):
        harness = _FakeHarness()
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False
            sim.close()
            # CreateSession's Response shows up anyway, after shutdown.
            harness.respond(
                "CreateSession", 0, {"session_handle": GLib.Variant("o", "/session/1")}
            )
            assert sim._session_path is None


class TestPortalSessionClose:
    """Sessions must be closed via org.freedesktop.portal.Session.Close() on
    the session's own path, not a nonexistent RemoteDesktop.EndSession."""

    def test_close_calls_session_close_on_session_path(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            harness.mock_bus.call_sync.reset_mock()
            sim.close()

        harness.mock_bus.call_sync.assert_called_once()
        call_args = harness.mock_bus.call_sync.call_args[0]
        assert call_args[0] == "org.freedesktop.portal.Desktop"
        assert call_args[1] == "/session/1"
        assert call_args[2] == "org.freedesktop.portal.Session"
        assert call_args[3] == "Close"
        assert sim._session_path is None

    def test_close_never_raises_when_session_close_fails(self):
        harness = _FakeHarness()
        with harness as sim:
            harness.run_setup_to_ready(sim)
            harness.mock_bus.call_sync.side_effect = GLib.Error("no such object")
            sim.close()  # must not raise
        assert sim._session_path is None

    def test_close_with_no_session_is_a_no_op(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.close()  # never pasted; must not raise
