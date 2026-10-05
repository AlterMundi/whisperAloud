"""The daemon hides its AppIndicator tray icon while the GNOME Shell
extension (which shows its own top-bar indicator) owns its bus name.

Unit level: WhisperAloudIndicator.set_suppressed, ShellExtensionWatcher with
injected watch functions, and the daemon wiring. The real Gio name-watch is
exercised on a private bus only (dbus_private, scripts/test_private_bus.sh).
"""

from unittest.mock import MagicMock, patch

import pytest

from tests._private_bus import private_bus_address
from whisper_aloud import shell_extension as se


def _make_indicator():
    mock_ai3 = MagicMock()
    instance = MagicMock()
    mock_ai3.Indicator.new.return_value = instance
    mock_ai3.IndicatorStatus.ACTIVE = "ACTIVE"
    mock_ai3.IndicatorStatus.PASSIVE = "PASSIVE"
    with patch("whisper_aloud.service.indicator.HAS_INDICATOR", True), \
         patch("whisper_aloud.service.indicator.AyatanaAppIndicator3", mock_ai3), \
         patch("whisper_aloud.service.indicator.Gtk", MagicMock()):
        from whisper_aloud.service.indicator import WhisperAloudIndicator

        ind = WhisperAloudIndicator(on_toggle=lambda: None)
        return ind, instance, mock_ai3


class TestIndicatorSuppression:
    def test_suppress_sets_passive_and_back(self):
        ind, instance, ai3 = _make_indicator()
        with patch("whisper_aloud.service.indicator.AyatanaAppIndicator3", ai3):
            ind.set_suppressed(True)
            instance.set_status.assert_called_with("PASSIVE")
            assert ind.suppressed is True
            ind.set_suppressed(False)
            instance.set_status.assert_called_with("ACTIVE")
            assert ind.suppressed is False

    def test_suppress_noop_when_unavailable(self):
        with patch("whisper_aloud.service.indicator.HAS_INDICATOR", False):
            from whisper_aloud.service.indicator import WhisperAloudIndicator

            ind = WhisperAloudIndicator(on_toggle=lambda: None)
            ind.set_suppressed(True)  # must not raise


class TestShellExtensionWatcher:
    def _watcher(self):
        from whisper_aloud.service.shell_extension_watch import ShellExtensionWatcher

        calls = {}

        def fake_watch(name, appeared, vanished):
            calls["name"] = name
            calls["appeared"] = appeared
            calls["vanished"] = vanished
            return 42

        unwatch = MagicMock()
        changes = []
        w = ShellExtensionWatcher(changes.append, watch=fake_watch, unwatch=unwatch)
        return w, calls, changes, unwatch

    def test_watches_extension_bus_name(self):
        _w, calls, _changes, _ = self._watcher()
        assert calls["name"] == se.EXTENSION_BUS_NAME

    def test_reports_presence_changes_once(self):
        w, calls, changes, _ = self._watcher()
        calls["appeared"]()
        calls["appeared"]()  # duplicate: no second notification
        calls["vanished"]()
        assert changes == [True, False]
        assert w.present is False

    def test_initial_vanished_is_reported(self):
        # Gio always calls exactly one handler first; "absent" at startup must
        # still reach the daemon so the tray shows.
        _w, calls, changes, _ = self._watcher()
        calls["vanished"]()
        assert changes == [False]

    def test_close_unwatches_once(self):
        w, _calls, _changes, unwatch = self._watcher()
        w.close()
        w.close()
        unwatch.assert_called_once_with(42)

    def test_callback_errors_are_contained(self):
        from whisper_aloud.service.shell_extension_watch import ShellExtensionWatcher

        handlers = {}

        def fake_watch(name, appeared, vanished):
            handlers["appeared"] = appeared
            return 1

        def boom(_present):
            raise RuntimeError("x")

        ShellExtensionWatcher(boom, watch=fake_watch, unwatch=MagicMock())
        handlers["appeared"]()  # must not raise into GLib


class TestDaemonWiring:
    def test_daemon_suppresses_tray_while_extension_present(self):
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            daemon = _make_daemon()
        cls.assert_called_once()
        on_change = cls.call_args.args[0]
        on_change(True)
        daemon.indicator.set_suppressed.assert_called_with(True)
        on_change(False)
        daemon.indicator.set_suppressed.assert_called_with(False)

    def test_cleanup_closes_watcher(self):
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            daemon = _make_daemon()
        daemon._cleanup()
        cls.return_value.close.assert_called_once()

    def test_window_manager_reprobes_on_presence_change(self):
        """WHISP-90: the same watcher that hides the tray also drives
        WindowManager's backend re-probe -- no second, duplicate
        Gio.bus_watch_name() for the same bus name."""
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            daemon = _make_daemon()
        on_change = cls.call_args.args[0]
        on_change(True)
        daemon.window_manager.on_shell_extension_presence_changed.assert_called_with(True)
        on_change(False)
        daemon.window_manager.on_shell_extension_presence_changed.assert_called_with(False)

    def test_window_manager_reprobe_failure_is_contained(self):
        """A broken WindowManager must never break tray suppression."""
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            daemon = _make_daemon()
        daemon.window_manager.on_shell_extension_presence_changed.side_effect = RuntimeError("x")
        on_change = cls.call_args.args[0]
        on_change(True)  # must not raise
        daemon.indicator.set_suppressed.assert_called_with(True)

    def test_watcher_without_indicator(self):
        """WHISP-119: the watcher also drives the extension text-insertion
        probe, so it exists even with no tray icon to hide."""
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            daemon = _make_daemon(indicator_fails=True)
        assert daemon.indicator is None
        cls.assert_called_once()

    def test_no_watcher_in_test_mode(self):
        from tests.test_daemon import _make_daemon

        with patch.dict("os.environ", {"WHISPER_ALOUD_TEST_MODE": "1"}), \
                patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            daemon = _make_daemon()
        assert daemon._shell_extension_watcher is None
        assert daemon._text_inserter is None
        cls.assert_not_called()


@pytest.mark.dbus_private
@pytest.mark.skipif(private_bus_address() is None,
                    reason="no private bus: run via scripts/test_private_bus.sh")
def test_real_name_watch_on_private_bus():
    gi = pytest.importorskip("gi")  # noqa: F841
    from gi.repository import Gio, GLib

    from whisper_aloud.service.shell_extension_watch import ShellExtensionWatcher

    addr = private_bus_address()
    flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
    watcher_conn = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    owner_conn = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)

    changes = []
    watcher = ShellExtensionWatcher(changes.append, connection=watcher_conn)
    ctx = GLib.MainContext.default()

    def spin_until(pred, timeout=5.0):
        deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
        while not pred() and GLib.get_monotonic_time() < deadline:
            ctx.iteration(False)
        return pred()

    try:
        assert spin_until(lambda: changes == [False])
        own_id = Gio.bus_own_name_on_connection(
            owner_conn, se.EXTENSION_BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        assert spin_until(lambda: changes == [False, True])
        Gio.bus_unown_name(own_id)
        assert spin_until(lambda: changes == [False, True, False])
    finally:
        watcher.close()
        watcher_conn.close_sync(None)
        owner_conn.close_sync(None)
