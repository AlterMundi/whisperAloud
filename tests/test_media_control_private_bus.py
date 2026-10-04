"""WHISP-89: integration test for MprisController's event-driven pause/
resume confirmation against a *real* fake MPRIS player on a private D-Bus
session bus (never the real session bus -- see tests/_private_bus.py and
scripts/test_private_bus.sh).

Why this exists in addition to tests/test_media_control.py's mocked unit
tests: this module's pause_all_playing()/resume_ours() read MPRIS
properties as plain pydbus interface-proxy *attributes*
(``iface.PlaybackStatus``), not via a dict-like ``.get()`` -- a real
``pydbus`` ``ProxyObject`` has no such method (confirmed against a real
fake player on a private bus while building this fix; a mocked unit test
using ``.get()``-style access would never have caught that it doesn't work
against the real thing). This test exercises the real pydbus property
descriptors, the real ``org.freedesktop.DBus.Properties.PropertiesChanged``
signal dispatch, and a real ``bus.watch_name()`` NameOwnerChanged path --
none of which a fully mocked bus can prove actually work end to end.

Must only be invoked via:
    scripts/test_private_bus.sh -m dbus_private tests/test_media_control_private_bus.py

so the bus ``dbus-run-session`` created -- never the real desktop session
bus -- is what gets exercised. ``private_bus_address()`` never connects to
anything; it only inspects env vars conftest.py already captured (see
tests/_private_bus.py), so the skip decision itself can never dial a real
bus.
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
from gi.repository import GLib  # noqa: E402

from tests._private_bus import private_bus_address  # noqa: E402
from whisper_aloud.service.media_control import MprisController  # noqa: E402

# A minimal fake MPRIS player: org.mpris.MediaPlayer2.Player with just
# PlaybackStatus/CanPause and Pause()/Play(), controllable via an auxiliary
# control interface so the test can tell it whether/when to emit
# PropertiesChanged after Pause()/Play() is called, or to vanish from the
# bus entirely instead. Mirrors tests/test_hotkey_portal_flow.py's
# `_FAKE_PORTAL_SERVICE_SRC` pattern for a real (not mocked) fake service.
_FAKE_MPRIS_PLAYER_SRC = textwrap.dedent(
    """
    import os
    import sys
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    BUS_NAME = "org.mpris.MediaPlayer2.whisperaloudtest"
    PATH = "/org/mpris/MediaPlayer2"
    PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
    PROPS_IFACE = "org.freedesktop.DBus.Properties"

    # "immediate": emit PropertiesChanged right after Pause()/Play().
    # "delayed": emit it ~400ms later (via GLib.timeout_add).
    # "never": change the property but never emit the signal at all.
    # "vanish": drop off the bus entirely instead of ever confirming.
    MODE = os.environ.get("FAKE_PLAYER_MODE", "immediate")

    state = {"status": "Playing"}

    INTROSPECTION = \"\"\"
    <node>
      <interface name="org.mpris.MediaPlayer2.Player">
        <property name="PlaybackStatus" type="s" access="read"/>
        <property name="CanPause" type="b" access="read"/>
        <method name="Pause"/>
        <method name="Play"/>
      </interface>
      <interface name="org.whisperaloud.test.FakePlayerControl">
        <method name="CallCounts">
          <arg type="i" name="pause_calls" direction="out"/>
          <arg type="i" name="play_calls" direction="out"/>
        </method>
      </interface>
    </node>
    \"\"\"

    calls = {"pause": 0, "play": 0}
    owner_id_holder = {}

    def _emit_props_changed(conn):
        conn.emit_signal(
            None, PATH, PROPS_IFACE, "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (
                PLAYER_IFACE, {"PlaybackStatus": GLib.Variant("s", state["status"])}, [],
            )),
        )

    def handle_method_call(conn, sender, path, iface, method, params, invocation):
        if iface == "org.whisperaloud.test.FakePlayerControl" and method == "CallCounts":
            invocation.return_value(GLib.Variant("(ii)", (calls["pause"], calls["play"])))
            return
        if method == "Pause":
            calls["pause"] += 1
            state["status"] = "Paused"
            invocation.return_value(None)
            if MODE == "immediate":
                _emit_props_changed(conn)
            elif MODE == "delayed":
                GLib.timeout_add(400, lambda: (_emit_props_changed(conn), False)[1])
            elif MODE == "vanish":
                GLib.timeout_add(50, lambda: (Gio.bus_unown_name(owner_id_holder["id"]), False)[1])
            # "never": change state, confirm nothing, ever.
        elif method == "Play":
            calls["play"] += 1
            state["status"] = "Playing"
            invocation.return_value(None)
            if MODE == "immediate":
                _emit_props_changed(conn)
            elif MODE == "delayed":
                GLib.timeout_add(400, lambda: (_emit_props_changed(conn), False)[1])
            # "never"/"vanish": confirm nothing for Play() either.
        else:
            invocation.return_value(None)

    def handle_get_property(conn, sender, path, iface, prop):
        if prop == "PlaybackStatus":
            return GLib.Variant("s", state["status"])
        if prop == "CanPause":
            return GLib.Variant("b", True)
        return None

    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    def on_bus_acquired(conn, name):
        for iface in node_info.interfaces:
            conn.register_object(PATH, iface, handle_method_call, handle_get_property, None)
        print("SERVICE_READY", flush=True)

    owner_id_holder["id"] = Gio.bus_own_name(
        Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired, None, None,
    )

    loop = GLib.MainLoop()
    loop.run()
    """
)

_FAKE_PLAYER_NAME = "org.mpris.MediaPlayer2.whisperaloudtest"


def _spawn_fake_player(tmp_path, bus_addr: str, mode: str) -> subprocess.Popen:
    script = tmp_path / f"fake_player_{mode}.py"
    script.write_text(_FAKE_MPRIS_PLAYER_SRC)
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=bus_addr, FAKE_PLAYER_MODE=mode)
    proc = subprocess.Popen(
        [sys.executable, str(script)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    # Wait for the service to actually claim the bus name instead of a fixed
    # sleep: read its one-line readiness print (bounded by Popen's own pipe
    # blocking -- if it never prints, the test's own GLib.MainLoop safety
    # timeout below still bounds the overall test).
    line = proc.stdout.readline()
    assert "SERVICE_READY" in line, f"fake player failed to start: {line!r}"
    return proc


@pytest.mark.dbus_private
@pytest.mark.skipif(
    private_bus_address() is None,
    reason="no private bus: run via scripts/test_private_bus.sh",
)
class TestMprisConfirmationOnPrivateBus:
    """Real pydbus + real GDBus signal dispatch, against a real (fake)
    MPRIS player subprocess -- never the user's real session bus or a real
    media player."""

    def _run(self, fn, timeout_s=5.0):
        """Run `fn()` (which must schedule GLib work and call `loop.quit()`
        itself once done) under a real GLib.MainLoop, bounded by a safety
        timeout so a test bug can never hang the suite."""
        loop = GLib.MainLoop()
        fn(loop)
        timeout_id = GLib.timeout_add(int(timeout_s * 1000), loop.quit)
        loop.run()
        GLib.source_remove(timeout_id)

    def test_pause_confirms_via_immediate_properties_changed(self, tmp_path):
        bus_addr = private_bus_address()
        proc = _spawn_fake_player(tmp_path, bus_addr, "immediate")
        try:
            with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
                import pydbus
                bus = pydbus.SessionBus()
                ctrl = MprisController()
                ctrl.set_bus(bus)

                done = []

                def _go(loop):
                    def _on_done():
                        done.append(True)
                        loop.quit()
                    ctrl.pause_all_playing(on_done=_on_done)

                self._run(_go)

                assert done == [True]
                assert _FAKE_PLAYER_NAME in ctrl._paused_by_us
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_pause_degrades_when_player_never_confirms(self, tmp_path):
        """A non-compliant player (changes status, never signals) must not
        hang -- the bounded safety timeout degrades and proceeds, which
        this test proves by actually waiting out real elapsed time (the
        only test in this module that does; everything else is signal- or
        vanish-driven and resolves immediately)."""
        bus_addr = private_bus_address()
        proc = _spawn_fake_player(tmp_path, bus_addr, "never")
        try:
            with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}), \
                 patch(
                     "whisper_aloud.service.media_control._CONFIRM_SAFETY_TIMEOUT_MS", 300
                 ):
                import pydbus
                bus = pydbus.SessionBus()
                ctrl = MprisController()
                ctrl.set_bus(bus)

                done = []

                def _go(loop):
                    def _on_done():
                        done.append(True)
                        loop.quit()
                    ctrl.pause_all_playing(on_done=_on_done)

                self._run(_go, timeout_s=3.0)

                assert done == [True], "must degrade-and-proceed, not hang forever"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_pause_confirms_via_player_vanishing(self, tmp_path):
        bus_addr = private_bus_address()
        proc = _spawn_fake_player(tmp_path, bus_addr, "vanish")
        try:
            with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
                import pydbus
                bus = pydbus.SessionBus()
                ctrl = MprisController()
                ctrl.set_bus(bus)

                done = []

                def _go(loop):
                    def _on_done():
                        done.append(True)
                        loop.quit()
                    ctrl.pause_all_playing(on_done=_on_done)

                self._run(_go)

                assert done == [True]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_resume_confirms_via_delayed_properties_changed(self, tmp_path):
        """resume_ours() against a player that only confirms ~400ms after
        Play() -- proves the wait is real (driven by the signal actually
        arriving later), not an artifact of firing instantly. Pauses the
        fake player for real first (same "delayed" mode, so Pause() also
        only confirms ~400ms later) so resume_ours() has an actually-Paused
        player to find, rather than faking `_paused_by_us` against a player
        that was never genuinely paused."""
        bus_addr = private_bus_address()
        proc = _spawn_fake_player(tmp_path, bus_addr, "delayed")
        try:
            with patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": bus_addr}):
                import pydbus
                bus = pydbus.SessionBus()
                ctrl = MprisController()
                ctrl.set_bus(bus)

                def _pause(loop):
                    ctrl.pause_all_playing(on_done=loop.quit)

                self._run(_pause)
                assert _FAKE_PLAYER_NAME in ctrl._paused_by_us

                done = []

                def _go(loop):
                    def _on_done():
                        done.append(time.monotonic())
                        loop.quit()
                    done.append(time.monotonic())  # start marker
                    ctrl.resume_ours(on_done=_on_done)

                self._run(_go)

                assert len(done) == 2
                start, finish = done
                assert finish - start >= 0.3, "must actually wait for the real signal"
        finally:
            proc.terminate()
            proc.wait(timeout=5)
