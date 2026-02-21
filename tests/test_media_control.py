"""Tests for MprisController and GainController (WHISP-15)."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Optional, Set
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# MprisController tests
# =============================================================================

from whisper_aloud.service.media_control import GainController, GainSnapshot, MprisController


def _make_player(status="Playing", can_pause=True):
    """Build a mock MPRIS player proxy."""
    props = {"PlaybackStatus": status, "CanPause": can_pause}
    player = MagicMock()
    player.__getitem__ = lambda self, key: MagicMock(**{
        "get.side_effect": lambda k, d=None: props.get(k, d),
        "Pause": MagicMock(),
        "Play": MagicMock(),
    })
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
    return bus


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

        foo_props = {"PlaybackStatus": "Paused", "CanPause": True}
        bar_props = {"PlaybackStatus": "Playing", "CanPause": True}  # user resumed

        foo_player = MagicMock()
        foo_player.__getitem__ = lambda s, k: MagicMock(
            get=lambda key, d=None: foo_props.get(key, d),
            Play=MagicMock(),
        )
        bar_player = MagicMock()
        bar_player.__getitem__ = lambda s, k: MagicMock(
            get=lambda key, d=None: bar_props.get(key, d),
            Play=MagicMock(),
        )

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


# =============================================================================
# GainController tests
# =============================================================================

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
