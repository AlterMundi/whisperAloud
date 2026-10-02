"""Tests for PortalPasteSimulator (WHISP-59).

All Gio/GLib bus traffic is mocked (bus_get_sync, DBusProxy.new_sync, and
the Request::Response wait loop in `_call_request`), exactly like
tests/test_hotkey_portal_flow.py does for PortalHotkeys -- these tests
never open a real D-Bus connection and never touch the real
xdg-desktop-portal, which would pop a real RemoteDesktop consent dialog
on the developer's desktop.

A `dbus_private` integration test against a fake portal service lives in
tests/test_portal_paste_integration.py.
"""

from contextlib import ExitStack
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
    """Patches Gio bus/proxy construction and `_call_request` so
    PortalPasteSimulator can be driven with no real D-Bus connection.

    `call_log` records each portal-facing call: "CreateSession",
    "SelectDevices", "Start", or ("NotifyKeyboardKeysym", keysym, state).
    `select_devices_options` records the options dict passed to
    SelectDevices so tests can assert on persist_mode/types/restore_token.
    """

    def __init__(self, start_restore_token=None, fail_at=None):
        self.call_log: list = []
        self.select_devices_options: dict = {}
        self.start_restore_token = start_restore_token
        self.fail_at = fail_at  # e.g. "CreateSession" to make that step fail

        self.mock_bus = MagicMock(name="bus")
        self.mock_bus.get_unique_name.return_value = ":1.50"
        self.mock_proxy = MagicMock(name="proxy")
        self.mock_proxy.call_sync.side_effect = self._proxy_call_sync
        self._stack = ExitStack()

    def _proxy_call_sync(self, method_name, args, flags, timeout, cancellable):
        if method_name == "NotifyKeyboardKeysym":
            _session, _opts, keysym_v, state_v = args.unpack()
            self.call_log.append(("NotifyKeyboardKeysym", keysym_v, state_v))
            return None
        raise AssertionError(f"unexpected proxy.call_sync: {method_name}")

    def _fake_call_request(self, method_name, args, timeout_ms=None):
        if method_name == self.fail_at:
            return 1, {}

        if method_name == "CreateSession":
            self.call_log.append("CreateSession")
            return 0, {"session_handle": GLib.Variant("o", "/session/1")}
        if method_name == "SelectDevices":
            self.call_log.append("SelectDevices")
            _session, options = args.unpack()
            self.select_devices_options = options
            return 0, {}
        if method_name == "Start":
            self.call_log.append("Start")
            results = {}
            if self.start_restore_token:
                results["restore_token"] = GLib.Variant("s", self.start_restore_token)
            return 0, results
        raise AssertionError(f"unexpected _call_request: {method_name}")

    def __enter__(self) -> PortalPasteSimulator:
        self._stack.enter_context(patch.object(Gio, "bus_get_sync", return_value=self.mock_bus))
        self._stack.enter_context(
            patch.object(Gio.DBusProxy, "new_sync", return_value=self.mock_proxy)
        )
        self._stack.enter_context(
            patch.object(PortalPasteSimulator, "_call_request", self._fake_call_request)
        )
        return PortalPasteSimulator(
            restore_token_loader=lambda: None, restore_token_saver=lambda token: None
        )

    def __exit__(self, *exc_info):
        self._stack.close()


class TestSessionCreationOrder:
    def test_lazy_no_dbus_at_construction(self):
        """Constructing the object must not touch Gio/D-Bus at all."""
        with (
            patch.object(Gio, "bus_get_sync", side_effect=AssertionError("must not connect")),
            patch.object(Gio.DBusProxy, "new_sync", side_effect=AssertionError("must not connect")),
        ):
            sim = PortalPasteSimulator()
            assert sim._session_path is None

    def test_first_paste_creates_session_then_notifies(self):
        harness = _FakeHarness()
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is True

        assert harness.call_log[:3] == ["CreateSession", "SelectDevices", "Start"]
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
            sim.simulate_paste("ctrl+v")

        opts = harness.select_devices_options
        assert opts["types"] == DEVICE_KEYBOARD
        assert opts["persist_mode"] == PERSIST_MODE_PERSISTENT
        assert "restore_token" not in opts  # none was loaded

    def test_second_paste_reuses_session_no_new_create_session(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+v")
            harness.call_log.clear()
            assert sim.simulate_paste("ctrl+v") is True

        assert "CreateSession" not in harness.call_log
        assert "SelectDevices" not in harness.call_log
        assert "Start" not in harness.call_log
        assert len([c for c in harness.call_log if isinstance(c, tuple)]) == 4


class TestRestoreToken:
    def test_new_restore_token_is_persisted(self):
        harness = _FakeHarness(start_restore_token="tok-123")
        saved = {}
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
            patch.object(PortalPasteSimulator, "_call_request", harness._fake_call_request),
        ):
            sim = PortalPasteSimulator(
                restore_token_loader=lambda: None,
                restore_token_saver=lambda token: saved.setdefault("token", token),
            )
            sim.simulate_paste("ctrl+v")

        assert saved["token"] == "tok-123"

    def test_existing_restore_token_is_sent_in_select_devices(self):
        harness = _FakeHarness()
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
            patch.object(PortalPasteSimulator, "_call_request", harness._fake_call_request),
        ):
            sim = PortalPasteSimulator(
                restore_token_loader=lambda: "prior-token",
                restore_token_saver=lambda token: None,
            )
            sim.simulate_paste("ctrl+v")

        assert harness.select_devices_options["restore_token"] == "prior-token"


class TestShortcutVariants:
    def test_ctrl_shift_v_sends_six_notifications(self):
        harness = _FakeHarness()
        with harness as sim:
            sim.simulate_paste("ctrl+shift+v")

        notify_calls = [c for c in harness.call_log if isinstance(c, tuple)]
        assert len(notify_calls) == 6


class TestFailureFallback:
    def test_create_session_failure_falls_back_and_does_not_retry(self):
        harness = _FakeHarness(fail_at="CreateSession")
        fallback_reasons = []
        with (
            patch.object(Gio, "bus_get_sync", return_value=harness.mock_bus),
            patch.object(Gio.DBusProxy, "new_sync", return_value=harness.mock_proxy),
            patch.object(PortalPasteSimulator, "_call_request", harness._fake_call_request),
        ):
            sim = PortalPasteSimulator(
                on_fallback=fallback_reasons.append,
                restore_token_loader=lambda: None,
                restore_token_saver=lambda token: None,
            )
            assert sim.simulate_paste("ctrl+v") is False
            assert len(fallback_reasons) == 1

            # Second attempt must not re-run CreateSession (no second dialog)
            # and must not re-emit a fallback notification either (one is enough).
            with patch.object(
                Gio, "bus_get_sync", side_effect=AssertionError("must not reconnect")
            ):
                assert sim.simulate_paste("ctrl+v") is False
            assert len(fallback_reasons) == 1

    def test_notify_keysym_failure_does_not_poison_session_for_next_call(self):
        harness = _FakeHarness()
        call_count = {"n": 0}

        def flaky_proxy_call_sync(method_name, args, flags, timeout, cancellable):
            if method_name == "NotifyKeyboardKeysym":
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("simulated transient D-Bus error")
                return None
            raise AssertionError(method_name)

        harness.mock_proxy.call_sync.side_effect = flaky_proxy_call_sync
        with harness as sim:
            assert sim.simulate_paste("ctrl+v") is False  # first notify fails
            assert sim._session_path is not None  # session must survive
            assert sim.simulate_paste("ctrl+v") is True  # retried, succeeds
