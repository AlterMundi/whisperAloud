"""Tests for MprisController and GainController (WHISP-15, WHISP-89)."""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Set
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# MprisController tests
# =============================================================================

from whisper_aloud.service import media_control
from whisper_aloud.service.media_control import GainController, GainSnapshot, MprisController


def _make_player(status="Playing", can_pause=True):
    """Build a mock MPRIS player proxy.

    Properties are plain attributes (`iface.PlaybackStatus`), matching real
    pydbus interface-proxy semantics (see media_control.MprisController.
    _safe_prop's docstring) -- NOT a dict-like `.get()`, which a real
    pydbus ProxyObject does not support (confirmed against a real fake
    MPRIS player on a private bus; WHISP-89).
    """
    iface = MagicMock()
    iface.PlaybackStatus = status
    iface.CanPause = can_pause
    player = MagicMock()
    player.__getitem__ = lambda self, key, _iface=iface: _iface
    return player


def _make_bus(players: dict):
    """
    Build a minimal mock pydbus bus.

    players: {bus_name: mock_player_proxy}
    """
    dbus_proxy = MagicMock()
    all_names = list(players.keys()) + ["org.freedesktop.DBus", "com.other.app"]
    dbus_proxy.ListNames.return_value = all_names

    bus = MagicMock()
    bus.get.side_effect = lambda name, path=None: (
        dbus_proxy if name == ".DBus" else players.get(name, MagicMock())
    )
    # subscribe()/watch_name() default to inert MagicMocks (never fire)
    # unless a test wires them up via _FakeConfirmationBus below.
    return bus


class _FakeConfirmationBus:
    """A fake pydbus bus that supports exactly the surface MprisController
    uses (`.get`, `.subscribe`, `.watch_name`) with controllable
    PropertiesChanged/name-vanished delivery, so tests can exercise the
    event-driven pause/resume confirmation flow deterministically --
    without a real D-Bus connection or GLib main loop (see
    tests/test_hotkey_portal_flow.py's `_FakePortalHarness` for the
    analogous pattern used for the portal backend).

    `players`: {bus_name: {"status": str, "can_pause": bool}}
    """

    def __init__(self, players: dict):
        self.players = players
        self._prop_subs: list = []  # (sender_name, callback)
        self._watchers: list = []   # (name, name_vanished_callback)

    def get(self, name, path=None):
        if name == ".DBus":
            proxy = MagicMock()
            proxy.ListNames.return_value = list(self.players.keys())
            return proxy
        state = self.players[name]
        iface = MagicMock()
        iface.PlaybackStatus = state["status"]
        iface.CanPause = state.get("can_pause", True)

        def _pause():
            state["status"] = "Paused"

        def _play():
            state["status"] = "Playing"

        iface.Pause.side_effect = _pause
        iface.Play.side_effect = _play

        player = MagicMock()
        player.__getitem__ = lambda self, key, _iface=iface: _iface
        return player

    def subscribe(self, sender=None, iface=None, signal=None, object=None,
                  arg0=None, flags=0, signal_fired=None):
        sub = MagicMock()
        entry = (sender, signal_fired)
        self._prop_subs.append(entry)

        def _unsubscribe():
            if entry in self._prop_subs:
                self._prop_subs.remove(entry)
        sub.unsubscribe.side_effect = _unsubscribe
        return sub

    def watch_name(self, name, flags=0, name_appeared=None, name_vanished=None):
        watcher = MagicMock()
        entry = (name, name_vanished)
        self._watchers.append(entry)

        def _unwatch():
            if entry in self._watchers:
                self._watchers.remove(entry)
        watcher.unwatch.side_effect = _unwatch
        return watcher

    def fire_properties_changed(self, name, status):
        """Simulate the real player emitting PropertiesChanged for PlaybackStatus."""
        self.players[name]["status"] = status
        for sender, cb in list(self._prop_subs):
            if sender == name and cb is not None:
                cb(name, media_control._PLAYER_PATH, media_control._PROPS_IFACE,
                   "PropertiesChanged",
                   (media_control._PLAYER_IFACE, {"PlaybackStatus": status}, []))

    def fire_name_vanished(self, name):
        for n, cb in list(self._watchers):
            if n == name and cb is not None:
                cb()


class TestMprisController:
    def test_pause_playing_can_pause(self):
        """Players that are Playing and CanPause get paused."""
        ctrl = MprisController()
        player = _make_player(status="Playing", can_pause=True)
        bus = _make_bus({"org.mpris.MediaPlayer2.foo": player})
        ctrl.set_bus(bus)

        ctrl.pause_all_playing()

        assert "org.mpris.MediaPlayer2.foo" in ctrl._paused_by_us

    def test_skip_cannot_pause(self):
        """Players with CanPause=False are not paused."""
        ctrl = MprisController()
        player = _make_player(status="Playing", can_pause=False)
        bus = _make_bus({"org.mpris.MediaPlayer2.foo": player})
        ctrl.set_bus(bus)

        ctrl.pause_all_playing()

        assert "org.mpris.MediaPlayer2.foo" not in ctrl._paused_by_us

    def test_skip_already_paused(self):
        """Players already Paused are not touched."""
        ctrl = MprisController()
        player = _make_player(status="Paused", can_pause=True)
        bus = _make_bus({"org.mpris.MediaPlayer2.foo": player})
        ctrl.set_bus(bus)

        ctrl.pause_all_playing()

        assert "org.mpris.MediaPlayer2.foo" not in ctrl._paused_by_us

    def test_skip_non_mpris_names(self):
        """Non-MPRIS D-Bus names are ignored."""
        ctrl = MprisController()
        bus = _make_bus({})  # no MPRIS players
        ctrl.set_bus(bus)

        ctrl.pause_all_playing()  # should not raise

        assert len(ctrl._paused_by_us) == 0

    def test_resume_only_still_paused(self):
        """resume_ours only calls Play on players still in Paused state."""
        ctrl = MprisController()
        # Manually populate _paused_by_us to simulate a prior pause
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo", "org.mpris.MediaPlayer2.bar"}

        foo_player = _make_player(status="Paused", can_pause=True)
        bar_player = _make_player(status="Playing", can_pause=True)  # user resumed

        bus = MagicMock()
        bus.get.side_effect = lambda name, path=None: (
            foo_player if "foo" in name else bar_player
        )
        ctrl.set_bus(bus)

        ctrl.resume_ours()

        # _paused_by_us must be cleared after resume
        assert len(ctrl._paused_by_us) == 0

    def test_resume_without_bus_is_noop(self):
        """resume_ours without a bus should not raise."""
        ctrl = MprisController()
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}
        ctrl.resume_ours()  # should not raise

    def test_no_bus_pause_is_noop(self):
        """pause_all_playing without a bus should not raise."""
        ctrl = MprisController()
        ctrl.pause_all_playing()  # should not raise

    def test_clear_discards_without_resuming(self):
        """clear() empties paused_by_us without calling Play."""
        ctrl = MprisController()
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}
        ctrl.clear()
        assert len(ctrl._paused_by_us) == 0

    def test_misbehaving_player_does_not_block(self):
        """An exception from one player does not prevent others from being paused."""
        ctrl = MprisController()

        bad_player = MagicMock()
        bad_player.__getitem__ = MagicMock(side_effect=RuntimeError("D-Bus error"))

        good_player = _make_player(status="Playing", can_pause=True)

        dbus_proxy = MagicMock()
        dbus_proxy.ListNames.return_value = [
            "org.mpris.MediaPlayer2.bad",
            "org.mpris.MediaPlayer2.good",
        ]
        bus = MagicMock()
        bus.get.side_effect = lambda name, path=None: (
            dbus_proxy if name == ".DBus" else (
                bad_player if "bad" in name else good_player
            )
        )
        ctrl.set_bus(bus)

        ctrl.pause_all_playing()  # should not raise
        assert "org.mpris.MediaPlayer2.good" in ctrl._paused_by_us


class TestMprisControllerTestMode:
    """WHISPER_ALOUD_TEST_MODE=1 must disable media control entirely --
    set_bus() refuses to store a real bus, so pause_all_playing()/
    resume_ours() stay inert for the daemon's whole lifetime regardless of
    recording_flow.pause_media (WHISP-46 defense in depth)."""

    def test_set_bus_is_a_noop_in_test_mode(self, monkeypatch):
        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        ctrl = MprisController()
        ctrl.set_bus(MagicMock(name="real-bus"))
        assert ctrl._bus is None

    def test_pause_all_playing_is_inert_in_test_mode(self, monkeypatch):
        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        ctrl = MprisController()
        bus = _make_bus({"org.mpris.MediaPlayer2.foo": _make_player()})
        ctrl.set_bus(bus)  # no-op under test mode

        ctrl.pause_all_playing()  # should not raise, must not touch the bus

        bus.get.assert_not_called()
        assert ctrl._paused_by_us == set()

    def test_set_bus_stores_the_bus_without_test_mode(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        ctrl = MprisController()
        bus = MagicMock(name="real-bus")
        ctrl.set_bus(bus)
        assert ctrl._bus is bus


class TestMprisConfirmation:
    """WHISP-89: pause/resume wait for an actual PlaybackStatus confirmation
    (or name-vanished, or a bounded logged degradation) instead of a fixed
    sleep. All of these exercise the event-driven wait via `_FakeConfirmationBus`,
    without any real GLib main loop running."""

    def test_pause_on_done_fires_only_after_properties_changed_confirms(self, monkeypatch):
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        ctrl.pause_all_playing(on_done=lambda: done.append(True))

        # Pause() was issued synchronously, but on_done must not have fired
        # yet -- confirmation hasn't arrived.
        assert bus.players["org.mpris.MediaPlayer2.foo"]["status"] == "Paused"
        assert done == []

        bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Paused")

        assert done == [True]

    def test_pause_confirms_via_stopped_status_too(self, monkeypatch):
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        ctrl.pause_all_playing(on_done=lambda: done.append(True))
        bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Stopped")

        assert done == [True]

    def test_pause_confirms_via_name_vanished(self, monkeypatch):
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        ctrl.pause_all_playing(on_done=lambda: done.append(True))
        assert done == []

        bus.fire_name_vanished("org.mpris.MediaPlayer2.foo")

        assert done == [True]

    def test_pause_waits_for_every_targeted_player(self, monkeypatch):
        """on_done must not fire until ALL paused players have confirmed."""
        bus = _FakeConfirmationBus({
            "org.mpris.MediaPlayer2.foo": {"status": "Playing"},
            "org.mpris.MediaPlayer2.bar": {"status": "Playing"},
        })
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        ctrl.pause_all_playing(on_done=lambda: done.append(True))

        bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Paused")
        assert done == [], "must wait for bar too"

        bus.fire_properties_changed("org.mpris.MediaPlayer2.bar", "Paused")
        assert done == [True]

    def test_pause_degrades_with_warning_when_never_confirmed(self, caplog):
        """A non-compliant player that never emits PropertiesChanged/vanishes
        must not stall forever -- the bounded safety timeout (captured via
        GLib.timeout_add, fired manually here) proceeds anyway, logging a
        WARNING (never silently)."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        with patch.object(media_control.GLib, "timeout_add") as mock_timeout_add:
            ctrl.pause_all_playing(on_done=lambda: done.append(True))
            assert done == []
            # Never fire fire_properties_changed/fire_name_vanished: simulate
            # a player that just never confirms. Fire the captured safety
            # timeout callback ourselves instead of sleeping real time.
            ms, safety_cb = mock_timeout_add.call_args[0]
            assert ms == media_control._CONFIRM_SAFETY_TIMEOUT_MS
            with caplog.at_level("WARNING"):
                safety_cb()

        assert done == [True]
        assert any("degraded" in r.message or "never confirmed" in r.message
                   for r in caplog.records)

    def test_pause_settle_delay_uses_timeout_add_after_confirmation(self):
        """pre_pause_delay_ms (settle_delay_ms) must be an extra margin
        applied via GLib.timeout_add AFTER confirmation, never time.sleep --
        and media_control no longer imports `time` at all. The always-armed
        confirmation safety-bound timer (a separate, earlier GLib.timeout_add
        call) must not be confused with this settle-delay one."""
        assert not hasattr(media_control, "time")
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        scheduled = []  # every GLib.timeout_add(ms, cb) call, in order

        def _capture_timeout_add(ms, cb):
            scheduled.append((ms, cb))
            return len(scheduled)

        done = []
        with patch.object(media_control.GLib, "timeout_add", side_effect=_capture_timeout_add), \
             patch.object(media_control.GLib, "source_remove", lambda sid: None):
            ctrl.pause_all_playing(on_done=lambda: done.append(True), settle_delay_ms=250)
            bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Paused")

            assert done == [], "on_done must wait for the settle delay too"
            settle_calls = [c for c in scheduled if c[0] == 250]
            assert len(settle_calls) == 1, f"expected exactly one 250ms timer, got {scheduled}"
            _, settle_cb = settle_calls[0]
            settle_cb()

        assert done == [True]

    def test_pause_zero_settle_delay_calls_on_done_inline_not_via_timer(self):
        """settle_delay_ms=0 (the default) must call on_done inline, right
        from the confirmation callback -- never an extra GLib.idle_add/
        timeout_add round trip beyond the one always-armed confirmation
        safety bound. Staying synchronous here (when nothing needs an
        *extra* margin) is what keeps every caller that never has a real
        media player to wait for -- the overwhelming majority of
        StartRecording() calls -- working exactly as before this
        confirmation wait existed."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        timeout_calls = []
        with patch.object(
            media_control.GLib, "timeout_add",
            side_effect=lambda ms, cb: (timeout_calls.append((ms, cb)), len(timeout_calls))[1],
        ), patch.object(media_control.GLib, "source_remove", lambda sid: None):
            done = []
            ctrl.pause_all_playing(on_done=lambda: done.append(True))
            bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Paused")

            assert len(timeout_calls) == 1, "only the safety bound, never a settle timer"
            assert timeout_calls[0][0] == media_control._CONFIRM_SAFETY_TIMEOUT_MS
            assert done == [True], "on_done must have fired inline, no timer needed for it"

    def test_pause_with_nothing_playing_calls_on_done_without_waiting(self, monkeypatch):
        bus = _FakeConfirmationBus({})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        ctrl.pause_all_playing(on_done=lambda: done.append(True))
        assert done == [True]

    def test_resume_on_done_fires_after_confirmation(self, monkeypatch):
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Paused"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}

        done = []
        ctrl.resume_ours(on_done=lambda: done.append(True))
        assert bus.players["org.mpris.MediaPlayer2.foo"]["status"] == "Playing"
        assert done == []

        bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Playing")
        assert done == [True]

    def test_resume_does_not_touch_manually_resumed_player(self, monkeypatch):
        """If the user resumed a player manually before our delay elapsed,
        resume_ours must not call Play() on it -- and since nothing was
        actually resumed, on_done fires right away (nothing to confirm)."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}

        done = []
        ctrl.resume_ours(on_done=lambda: done.append(True))

        assert done == [True]
        assert bus.players["org.mpris.MediaPlayer2.foo"]["status"] == "Playing"

    def test_resume_delay_ms_uses_timeout_add_before_rechecking_status(self):
        """post_resume_delay_ms must delay the re-check+Play via
        GLib.timeout_add, never time.sleep."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Paused"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}

        with patch.object(media_control.GLib, "timeout_add") as mock_timeout_add:
            ctrl.resume_ours(delay_ms=300)
            # Play() must not have been called yet -- still waiting out the delay.
            assert bus.players["org.mpris.MediaPlayer2.foo"]["status"] == "Paused"
            mock_timeout_add.assert_called_once()
            ms, delayed_cb = mock_timeout_add.call_args[0]
            assert ms == 300
            delayed_cb()

        assert bus.players["org.mpris.MediaPlayer2.foo"]["status"] == "Playing"

    def test_pause_confirmation_logs_how_it_arrived(self, monkeypatch, caplog):
        """Observability: INFO log names which players were paused and how
        each one's pause was confirmed."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        with caplog.at_level("INFO"):
            ctrl.pause_all_playing(on_done=lambda: None)
            bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Paused")

        joined = "\n".join(r.message for r in caplog.records)
        assert "org.mpris.MediaPlayer2.foo" in joined
        assert "PropertiesChanged signal" in joined

    def test_resume_confirmation_logs_how_it_arrived(self, monkeypatch, caplog):
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Paused"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}

        with caplog.at_level("INFO"):
            ctrl.resume_ours(on_done=lambda: None)
            bus.fire_name_vanished("org.mpris.MediaPlayer2.foo")

        joined = "\n".join(r.message for r in caplog.records)
        assert "org.mpris.MediaPlayer2.foo" in joined
        assert "name vanished" in joined

    def test_clear_supersedes_pending_pause_confirmation(self, monkeypatch):
        """clear() (e.g. CancelRecording) must stop a pending pause-
        confirmation wait from ever calling the original on_done -- a late
        PropertiesChanged arriving afterwards must be a no-op."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Playing"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)

        done = []
        ctrl.pause_all_playing(on_done=lambda: done.append(True))
        ctrl.clear()

        bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Paused")

        assert done == []

    def test_new_pause_call_supersedes_a_stale_resume_wait(self, monkeypatch):
        """A second pause_all_playing() call must invalidate an in-flight
        resume_ours() confirmation wait from an earlier call (defensive;
        daemon.py never actually calls them concurrently)."""
        bus = _FakeConfirmationBus({"org.mpris.MediaPlayer2.foo": {"status": "Paused"}})
        ctrl = MprisController()
        ctrl.set_bus(bus)
        ctrl._paused_by_us = {"org.mpris.MediaPlayer2.foo"}

        resume_done = []
        ctrl.resume_ours(on_done=lambda: resume_done.append(True))

        pause_done = []
        ctrl.pause_all_playing(on_done=lambda: pause_done.append(True))

        bus.fire_properties_changed("org.mpris.MediaPlayer2.foo", "Playing")
        assert resume_done == [], "superseded resume must never call its on_done"


# =============================================================================
# GainController tests
# =============================================================================

class TestGainControllerTestMode:
    """WHISPER_ALOUD_TEST_MODE=1 must disable wpctl entirely -- no subprocess
    probe, no crash-file read/act -- regardless of whether wpctl is actually
    present on the host (WHISP-46)."""

    def test_gain_controller_skips_wpctl_probe_in_test_mode(self, monkeypatch):
        """WHISPER_ALOUD_TEST_MODE=1 must prevent wpctl detection even if present."""
        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl")
        controller = GainController()
        assert controller._wpctl is None

    def test_gain_controller_restore_from_crash_file_is_noop_in_test_mode(self, monkeypatch, tmp_path):
        """Even if a real recovery file exists, test mode must never read/act on it."""
        monkeypatch.setenv("WHISPER_ALOUD_TEST_MODE", "1")
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl")
        controller = GainController()
        # Should not raise, should not touch _RECOVERY_FILE at all.
        controller.restore_from_crash_file()
        assert controller._snapshot is None

    def test_gain_controller_probes_wpctl_without_test_mode(self, monkeypatch):
        """Unset WHISPER_ALOUD_TEST_MODE reproduces today's behavior exactly."""
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        monkeypatch.setattr(media_control, "is_flatpak_sandbox", lambda: False)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl" if name == "wpctl" else None)
        controller = GainController()
        assert controller._wpctl == "/usr/bin/wpctl"


class TestGainControllerFlatpakSandbox:
    """Inside Flatpak (WHISP-61) wpctl is a host binary: the runtime does not
    ship it and the sandbox has no PipeWire-native socket to reach the
    session manager with. Gain control must be disabled up front --
    no PATH probe, no subprocess, no crash-file access -- even if some
    `wpctl` happened to be on the sandbox PATH."""

    def test_sandbox_disables_gain_control_even_if_wpctl_on_path(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        monkeypatch.setattr(media_control, "is_flatpak_sandbox", lambda: True)
        probed = []
        monkeypatch.setattr(shutil, "which", lambda name: probed.append(name) or "/app/bin/wpctl")
        controller = GainController()
        assert controller._wpctl is None
        assert probed == []

    def test_sandbox_raise_restore_and_crash_recovery_never_spawn(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        monkeypatch.setattr(media_control, "is_flatpak_sandbox", lambda: True)

        def _boom(*a, **kw):
            raise AssertionError("subprocess must not run inside the sandbox")

        monkeypatch.setattr(media_control.subprocess, "run", _boom)
        monkeypatch.setattr(media_control, "open", _boom, raising=False)
        controller = GainController()
        assert controller.raise_to(0.9) is False
        controller.restore()
        controller.restore_from_crash_file()


class TestGainControllerAvailability:
    """WHISP-93: GetStatus surfaces whether gain control is actually usable."""

    def test_gain_control_available_true_when_wpctl_resolved(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        monkeypatch.setattr(media_control, "is_flatpak_sandbox", lambda: False)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl" if name == "wpctl" else None)
        controller = GainController()
        assert controller.gain_control_available is True

    def test_gain_control_available_false_without_wpctl(self, monkeypatch):
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        monkeypatch.setattr(media_control, "is_flatpak_sandbox", lambda: False)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        controller = GainController()
        assert controller.gain_control_available is False

    def test_gain_control_available_false_in_flatpak_sandbox(self, monkeypatch):
        """Flatpak's structural no-op (WHISP-61) must be observable, not
        just silently logged -- this is what the ticket's GetStatus fix
        (mic_gain_control) ultimately reads."""
        monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
        monkeypatch.setattr(media_control, "is_flatpak_sandbox", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wpctl")
        controller = GainController()
        assert controller.gain_control_available is False


class TestGainController:
    def test_no_wpctl_is_noop(self):
        """GainController gracefully does nothing when wpctl is absent."""
        ctrl = GainController()
        ctrl._wpctl = None  # simulate absence

        result = ctrl.raise_to(0.85)
        assert result is False
        ctrl.restore()  # should not raise

    def test_raises_when_below_target(self):
        """gain is raised when current < target."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        snapshot = GainSnapshot(node_id="42", volume=0.5, muted=False)

        with patch.object(ctrl, '_resolve_default_source_id', return_value="42"), \
             patch.object(ctrl, '_get_volume', return_value=snapshot), \
             patch.object(ctrl, '_set_volume', return_value=True) as mock_set, \
             patch.object(ctrl, '_persist_snapshot'):
            result = ctrl.raise_to(0.85)

        assert result is True
        mock_set.assert_called_once_with("42", pytest.approx(0.85, abs=1e-4))

    def test_no_lower_when_above_target(self):
        """Gain is NOT lowered when current >= target."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        snapshot = GainSnapshot(node_id="42", volume=0.95, muted=False)

        with patch.object(ctrl, '_resolve_default_source_id', return_value="42"), \
             patch.object(ctrl, '_get_volume', return_value=snapshot), \
             patch.object(ctrl, '_set_volume', return_value=True) as mock_set, \
             patch.object(ctrl, '_persist_snapshot'):
            result = ctrl.raise_to(0.85)

        assert result is True
        mock_set.assert_not_called()  # current 0.95 >= target 0.85 → no change

    def test_skip_when_muted(self):
        """Gain is not raised when mic is muted."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        snapshot = GainSnapshot(node_id="42", volume=0.5, muted=True)

        with patch.object(ctrl, '_resolve_default_source_id', return_value="42"), \
             patch.object(ctrl, '_get_volume', return_value=snapshot), \
             patch.object(ctrl, '_set_volume') as mock_set:
            result = ctrl.raise_to(0.85)

        assert result is False
        mock_set.assert_not_called()

    def test_restore_sets_original_volume(self):
        """restore() sets volume back to the saved snapshot value."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"
        ctrl._snapshot = GainSnapshot(node_id="42", volume=0.5, muted=False)

        with patch.object(ctrl, '_set_volume', return_value=True) as mock_set, \
             patch.object(ctrl, '_clear_recovery_file'):
            ctrl.restore()

        mock_set.assert_called_once_with("42", 0.5)
        assert ctrl._snapshot is None

    def test_restore_idempotent(self):
        """restore() called twice does not raise and only restores once."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"
        ctrl._snapshot = GainSnapshot(node_id="42", volume=0.5, muted=False)

        with patch.object(ctrl, '_set_volume', return_value=True) as mock_set, \
             patch.object(ctrl, '_clear_recovery_file'):
            ctrl.restore()
            ctrl.restore()  # second call: snapshot is None

        assert mock_set.call_count == 1

    def test_parse_volume_with_muted_token(self):
        """_get_volume parses 'Volume: 0.70 [MUTED]' correctly."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Volume: 0.70 [MUTED]\n",
            )
            result = ctrl._get_volume("42")

        assert result is not None
        assert result.volume == pytest.approx(0.70)
        assert result.muted is True

    def test_parse_volume_plain(self):
        """_get_volume parses 'Volume: 0.90' correctly."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Volume: 0.90\n")
            result = ctrl._get_volume("42")

        assert result is not None
        assert result.volume == pytest.approx(0.90)
        assert result.muted is False

    def test_crash_recovery_restore(self, tmp_path):
        """restore_from_crash_file reads the JSON blob and restores volume."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        recovery_data = {"node_id": "99", "volume": 0.6, "muted": False}
        recovery_file = tmp_path / "whisperaloud-gain.json"
        recovery_file.write_text(json.dumps(recovery_data))

        with patch('whisper_aloud.service.media_control._RECOVERY_FILE', str(recovery_file)), \
             patch.object(ctrl, '_set_volume', return_value=True) as mock_set, \
             patch.object(ctrl, '_clear_recovery_file'):
            ctrl.restore_from_crash_file()

        mock_set.assert_called_once_with("99", pytest.approx(0.6))

    def test_crash_recovery_missing_file_is_noop(self):
        """restore_from_crash_file with no file does not raise."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        with patch('whisper_aloud.service.media_control._RECOVERY_FILE', '/nonexistent/path'):
            ctrl.restore_from_crash_file()  # should not raise

    def test_target_clamped_to_range(self):
        """target_gain_linear is clamped to [0.0, 1.5]."""
        ctrl = GainController()
        ctrl._wpctl = "/usr/bin/wpctl"

        snapshot = GainSnapshot(node_id="42", volume=0.0, muted=False)

        with patch.object(ctrl, '_resolve_default_source_id', return_value="42"), \
             patch.object(ctrl, '_get_volume', return_value=snapshot), \
             patch.object(ctrl, '_set_volume', return_value=True) as mock_set, \
             patch.object(ctrl, '_persist_snapshot'):
            ctrl.raise_to(5.0)  # above max

        called_with = mock_set.call_args[0][1]
        assert called_with <= 1.5


# =============================================================================
# Config integration test
# =============================================================================

def test_recording_flow_config_defaults():
    """RecordingFlowConfig is nested in WhisperAloudConfig with correct defaults."""
    from whisper_aloud.config import RecordingFlowConfig, WhisperAloudConfig

    cfg = WhisperAloudConfig()
    assert hasattr(cfg, 'recording_flow')
    rf = cfg.recording_flow
    assert isinstance(rf, RecordingFlowConfig)
    assert rf.pause_media is True
    assert rf.raise_mic_gain is True
    assert 0.0 < rf.target_gain_linear <= 1.5
    assert rf.pre_pause_delay_ms == 0
    assert rf.post_resume_delay_ms == 0
    assert rf.gain_restore_on_crash is True


def test_recording_flow_config_roundtrip():
    """RecordingFlowConfig survives to_dict/from_dict round-trip."""
    from whisper_aloud.config import WhisperAloudConfig

    cfg = WhisperAloudConfig()
    cfg.recording_flow.pause_media = False
    cfg.recording_flow.raise_mic_gain = True
    cfg.recording_flow.target_gain_linear = 0.75

    data = cfg.to_dict()
    assert "recording_flow" in data
    assert data["recording_flow"]["raise_mic_gain"] is True

    cfg2 = WhisperAloudConfig.from_dict(data)
    assert cfg2.recording_flow.pause_media is False
    assert cfg2.recording_flow.raise_mic_gain is True
    assert cfg2.recording_flow.target_gain_linear == pytest.approx(0.75)
