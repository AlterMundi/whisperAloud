"""dbus_private integration test for HotkeyStateChanged (WHISP-99).

The daemon's own wiring (HotkeyManager.on_state_change -> GLib.idle_add ->
self.HotkeyStateChanged(...)) is unit-tested with a fully mocked pydbus in
tests/test_daemon.py and tests/test_hotkey.py -- those never exercise a real
pydbus `signal()` descriptor travelling over an actual D-Bus connection.

This test publishes a small *real* pydbus object (just the one signal, not
the whole WhisperAloudService -- that needs real audio/GTK bindings, which
is what the e2e harness in tests/e2e/ is for) on a private bus started via
`dbus-run-session`, drives it with a real `HotkeyManager`, and asserts a
second, independent connection to that same private bus actually receives
the signal -- the exact mechanism docs/dbus-api.md documents for clients.

Must only be invoked via:
    scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_state_signal_private_bus.py
so the bus `dbus-run-session` created -- never the real desktop session
bus -- is what gets exercised. ``private_bus_address()`` never connects to
anything; it only inspects env vars conftest.py already captured (see
tests/_private_bus.py), so the skip decision itself can never dial a real
bus. conftest.py overwrites DBUS_SESSION_BUS_ADDRESS for every other test;
this test (and only this test) points it at the private address for the
duration of each D-Bus call it makes.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests._private_bus import private_bus_address

pytestmark = pytest.mark.dbus_private

pytest.importorskip("gi")


@pytest.mark.skipif(
    private_bus_address() is None,
    reason="no private bus: run via scripts/test_private_bus.sh",
)
class TestHotkeyStateChangedOverRealDBus:
    def test_signal_delivered_to_a_separate_connection(self, monkeypatch):
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        from pydbus import SessionBus

        from whisper_aloud.service.hotkey import HotkeyManager

        bus_addr = private_bus_address()
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", bus_addr)

        bus_name = "org.whisperaloud.test.HotkeyStateSignal"
        path = "/org/whisperaloud/test/HotkeyStateSignal"

        class _Publisher:
            """
            <node>
              <interface name="org.whisperaloud.test.HotkeyStateSignal">
                <signal name="HotkeyStateChanged">
                  <arg type="b" name="bound"/>
                  <arg type="s" name="trigger"/>
                  <arg type="s" name="backend"/>
                </signal>
              </interface>
            </node>
            """

            from pydbus.generic import signal

            HotkeyStateChanged = signal()

        publisher_bus = SessionBus()
        publisher = _Publisher()
        name_owner = publisher_bus.request_name(bus_name)
        publisher_bus.register_object(path, publisher, None)

        # A real HotkeyManager -- backend forced to "keybinder" with a
        # mocked libkeybinder3 module (this sandboxed test environment has
        # no real portal/keybinder to reach), so register()/unregister()
        # below produce real bound-state transitions. What this test cares
        # about is that on_state_change's (bound, trigger, backend) tuple
        # reaches a *different* D-Bus connection via a real round trip --
        # not backend detection, which is covered by tests/test_hotkey.py.
        received = []
        mock_kb = MagicMock()
        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch(
                "whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb
            ):
                manager = HotkeyManager(
                    on_state_change=lambda *args: publisher.HotkeyStateChanged(*args)
                )
        assert manager.backend == "keybinder"

        subscriber_bus = SessionBus()
        subscriber_proxy = subscriber_bus.get(bus_name, path)
        subscriber_proxy.HotkeyStateChanged.connect(lambda *args: received.append(args))

        try:
            # The initial (False, "", "keybinder") notification already
            # fired at HotkeyManager() construction time, above, before the
            # subscriber connected -- these are the transitions it should
            # actually observe, exactly like a daemon's hotkey config
            # change (unregister then register) or shutdown (unregister)
            # would produce.
            manager.register("<Super><Alt>r", callback=lambda: None)
            manager.unregister()

            import time

            # A periodic heartbeat guarantees iteration(True) below wakes up
            # regularly even if the signal never arrives, so the deadline
            # check always gets re-evaluated instead of blocking forever.
            heartbeat_id = GLib.timeout_add(100, lambda: True)
            context = GLib.MainContext.default()
            deadline = time.monotonic() + 5.0
            try:
                while len(received) < 2 and time.monotonic() < deadline:
                    context.iteration(True)
            finally:
                GLib.source_remove(heartbeat_id)
        finally:
            name_owner.unown()

        assert len(received) >= 2, received
        assert received[0] == (True, "", "keybinder")
        assert received[-1] == (False, "", "keybinder")
