"""Daemon wiring of input normalization (docs/design/input-gain-loop.md):
config reaches the recorder, GetStatus reports it, nothing touches device gain."""

import logging
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.test_daemon import _build_stub_modules, _import_daemon_module, _make_daemon
from whisper_aloud.audio.input_gain import NormalizationStats
from whisper_aloud.config import WhisperAloudConfig


@contextmanager
def _service_factory():
    """Yield (daemon_module, AudioRecorder mock) with every external dep mocked."""
    stub_modules = _build_stub_modules()
    daemon_module = _import_daemon_module(stub_modules)
    with (
        patch.dict(sys.modules, stub_modules),
        patch.object(daemon_module, "SessionBus"),
        patch.object(daemon_module, "AudioRecorder") as mock_ar,
        patch.object(daemon_module, "create_engine"),
        patch.object(daemon_module, "NotificationManager"),
        patch.object(daemon_module, "HistoryManager"),
        patch.object(daemon_module, "ClipboardManager"),
        patch.object(daemon_module, "WindowManager"),
        patch.object(daemon_module, "build_clipboard_manager"),
        patch.object(daemon_module, "WhisperAloudIndicator"),
        patch.object(daemon_module, "HotkeyManager"),
        patch.object(daemon_module, "GLib") as mock_glib,
    ):
        mock_glib.Variant = lambda t, v: v
        yield daemon_module, mock_ar


@pytest.fixture
def daemon():
    return _make_daemon()


def test_recorder_gets_input_gain_config():
    with _service_factory() as (daemon_module, mock_ar):
        config = WhisperAloudConfig()
        config.input_gain.target_dbfs = -23.0
        daemon_module.WhisperAloudService(config=config)
        _, kwargs = mock_ar.call_args
        assert kwargs["input_gain_config"] == config.input_gain


def test_get_status_reports_input_gain(daemon):
    info = daemon.GetStatus()["input_gain"]
    assert info["mode"] == "digital"
    assert bool(info["enabled"]) is True
    assert bool(info["hardware_control"]) is False
    assert info["target_dbfs"] == pytest.approx(-20.0)
    assert info["max_gain_db"] == pytest.approx(30.0)


def test_get_status_input_gain_off_when_disabled(daemon):
    daemon.config.input_gain.enabled = False
    info = daemon.GetStatus()["input_gain"]
    assert info["mode"] == "off"
    assert bool(info["enabled"]) is False


def test_get_status_reports_last_gain(daemon):
    daemon.recorder.last_input_gain_stats = NormalizationStats(
        final_gain_db=24.5, speech_before_dbfs=-44.0
    )
    info = daemon.GetStatus()["input_gain"]
    assert info["last_gain_db"] == pytest.approx(24.5)
    assert info["last_speech_dbfs"] == pytest.approx(-44.0)


def test_get_status_last_gain_nan_before_first_recording(daemon):
    daemon.recorder.last_input_gain_stats = None
    info = daemon.GetStatus()["input_gain"]
    assert np.isnan(info["last_gain_db"]) and np.isnan(info["last_speech_dbfs"])


def test_get_status_without_recorder(daemon):
    daemon.recorder = None
    info = daemon.GetStatus()["input_gain"]
    assert np.isnan(info["last_gain_db"])


def test_input_gain_variants_are_typed(daemon):
    with patch.object(daemon._daemon_module, "GLib") as mock_glib:
        mock_glib.Variant = MagicMock(side_effect=lambda t, v: (t, v))
        status = daemon.GetStatus()
    sig, inner = status["input_gain"]
    assert sig == "a{sv}"
    assert inner["mode"] == ("s", "digital")
    assert inner["enabled"] == ("b", True)
    assert inner["hardware_control"] == ("b", False)
    for key in ("target_dbfs", "max_gain_db", "last_gain_db", "last_speech_dbfs"):
        assert inner[key][0] == "d", key


def test_set_config_input_gain_recreates_recorder(daemon):
    daemon._transcribing = False
    with patch.object(daemon._daemon_module, "AudioRecorder") as mock_ar:
        assert daemon.SetConfig({"input_gain.target_dbfs": -24.0}) is True
    _, kwargs = mock_ar.call_args
    assert kwargs["input_gain_config"].target_dbfs == pytest.approx(-24.0)


def test_hardware_control_logs_warning_and_stays_digital(caplog):
    with caplog.at_level(logging.WARNING), _service_factory() as (daemon_module, _):
        config = WhisperAloudConfig()
        config.input_gain.hardware_control = True
        service = daemon_module.WhisperAloudService(config=config)
        assert service.GetStatus()["input_gain"]["mode"] == "digital"
    assert any("hardware_control" in r.getMessage() for r in caplog.records)
