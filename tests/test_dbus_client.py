"""Tests for WhisperAloud D-Bus client wrapper."""

import importlib
import sys
from unittest.mock import MagicMock, patch


def _import_client_module():
    """Import client module with local D-Bus/GLib stubs."""
    fake_pydbus = MagicMock()
    fake_pydbus.SessionBus = MagicMock
    fake_pydbus_generic = MagicMock()

    fake_gi = MagicMock()
    fake_gi_repository = MagicMock()
    fake_gi.repository = fake_gi_repository

    with patch.dict(
        sys.modules,
        {
            "pydbus": fake_pydbus,
            "pydbus.generic": fake_pydbus_generic,
            "gi": fake_gi,
            "gi.repository": fake_gi_repository,
        },
    ):
        sys.modules.pop("whisper_aloud.service.client", None)
        module = importlib.import_module("whisper_aloud.service.client")
    return module


class TestWhisperAloudClient:
    """Tests for WhisperAloudClient D-Bus wrapper."""

    def _make_client(self, daemon_available=True):
        """Create a client with mocked D-Bus bus."""
        client_module = _import_client_module()
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_proxy = MagicMock()
            if daemon_available:
                mock_bus.get.return_value = mock_proxy
            else:
                mock_bus.get.side_effect = Exception("org.fede.whisperaloud not found")

            client = client_module.WhisperAloudClient()
            return client_module, client, mock_bus, mock_proxy

    # ─── Connection tests ────────────────────────────────────────────────

    def test_client_connects_to_daemon(self):
        """Client should call bus.get with correct bus name."""
        _, client, mock_bus, _ = self._make_client(daemon_available=True)
        mock_bus.get.assert_called_once_with("org.fede.whisperaloud")
        assert client.is_connected is True

    def test_client_handles_daemon_unavailable(self):
        """Client should set is_connected=False when daemon is not running."""
        _, client, _, _ = self._make_client(daemon_available=False)
        assert client.is_connected is False

    def test_connect_retries_on_failure(self):
        """connect() should return False when daemon is unavailable."""
        client_module, client, _, _ = self._make_client(daemon_available=False)
        assert client.is_connected is False
        # Reconnect attempt also fails
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus2 = MagicMock()
            mock_bus_cls.return_value = mock_bus2
            mock_bus2.get.side_effect = Exception("still unavailable")
            result = client.connect()
            assert result is False
            assert client.is_connected is False

    def test_connect_succeeds_on_retry(self):
        """connect() should return True when daemon becomes available."""
        client_module, client, _, _ = self._make_client(daemon_available=False)
        assert client.is_connected is False
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus2 = MagicMock()
            mock_bus_cls.return_value = mock_bus2
            new_proxy = MagicMock()
            mock_bus2.get.return_value = new_proxy
            result = client.connect()
            assert result is True
            assert client.is_connected is True

    # ─── Method wrapper tests ────────────────────────────────────────────

    def test_toggle_calls_daemon(self):
        """toggle_recording should call proxy.ToggleRecording."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.ToggleRecording.return_value = "recording"
        result = client.toggle_recording()
        mock_proxy.ToggleRecording.assert_called_once()
        assert result == "recording"

    def test_start_recording_calls_daemon(self):
        """start_recording should call proxy.StartRecording."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.StartRecording.return_value = True
        result = client.start_recording()
        mock_proxy.StartRecording.assert_called_once()
        assert result is True

    def test_stop_recording_calls_daemon(self):
        """stop_recording should call proxy.StopRecording."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.StopRecording.return_value = "transcribing"
        result = client.stop_recording()
        mock_proxy.StopRecording.assert_called_once()
        assert result == "transcribing"

    def test_cancel_recording_calls_daemon(self):
        """cancel_recording should call proxy.CancelRecording."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.CancelRecording.return_value = True
        result = client.cancel_recording()
        mock_proxy.CancelRecording.assert_called_once()
        assert result is True

    def test_get_status_calls_daemon(self):
        """get_status should call proxy.GetStatus and return dict."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.GetStatus.return_value = {"state": "idle", "version": "1.0"}
        result = client.get_status()
        mock_proxy.GetStatus.assert_called_once()
        assert isinstance(result, dict)
        assert result["state"] == "idle"

    def test_get_history_calls_daemon(self):
        """get_history should call proxy.GetHistory with limit."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.GetHistory.return_value = [{"text": "hello"}]
        result = client.get_history(limit=25)
        mock_proxy.GetHistory.assert_called_once_with(25)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_get_config_calls_daemon(self):
        """get_config should call proxy.GetConfig."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.GetConfig.return_value = {"model.name": "base"}
        result = client.get_config()
        mock_proxy.GetConfig.assert_called_once()
        assert isinstance(result, dict)

    def test_set_config_calls_daemon(self):
        """set_config should call proxy.SetConfig with changes dict."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.SetConfig.return_value = True
        result = client.set_config({"model.name": "small"})
        mock_proxy.SetConfig.assert_called_once_with({"model.name": "small"})
        assert result is True

    def test_reload_config_calls_daemon(self):
        """reload_config should call proxy.ReloadConfig."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.ReloadConfig.return_value = True
        result = client.reload_config()
        mock_proxy.ReloadConfig.assert_called_once()
        assert result is True

    def test_quit_daemon_calls_daemon(self):
        """quit_daemon should call proxy.Quit."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.Quit.return_value = True
        result = client.quit_daemon()
        mock_proxy.Quit.assert_called_once()
        assert result is True

    # ─── Error handling tests ────────────────────────────────────────────

    def test_method_returns_false_when_disconnected(self):
        """Methods should return False/empty when not connected."""
        _, client, _, _ = self._make_client(daemon_available=False)
        assert client.start_recording() is False
        assert client.stop_recording() == ""
        assert client.toggle_recording() == ""
        assert client.cancel_recording() is False
        assert client.get_status() == {}
        assert client.get_history() == []
        assert client.get_config() == {}
        assert client.set_config({}) is False
        assert client.reload_config() is False
        assert client.quit_daemon() is False

    def test_method_handles_proxy_exception(self):
        """Methods should catch exceptions from proxy calls and return defaults."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.StartRecording.side_effect = Exception("D-Bus error")
        result = client.start_recording()
        assert result is False

    def test_toggle_handles_proxy_exception(self):
        """toggle_recording should return empty string on error."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.ToggleRecording.side_effect = Exception("D-Bus error")
        result = client.toggle_recording()
        assert result == ""

    # ─── Signal subscription tests ───────────────────────────────────────

    def test_signal_subscription_transcription_ready(self):
        """on_transcription_ready should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_transcription_ready(callback)
        mock_proxy.TranscriptionReady.connect.assert_called_once_with(callback)

    def test_signal_subscription_recording_started(self):
        """on_recording_started should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_recording_started(callback)
        mock_proxy.RecordingStarted.connect.assert_called_once_with(callback)

    def test_signal_subscription_recording_stopped(self):
        """on_recording_stopped should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_recording_stopped(callback)
        mock_proxy.RecordingStopped.connect.assert_called_once_with(callback)

    def test_signal_subscription_level_update(self):
        """on_level_update should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_level_update(callback)
        mock_proxy.LevelUpdate.connect.assert_called_once_with(callback)

    def test_signal_subscription_status_changed(self):
        """on_status_changed should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_status_changed(callback)
        mock_proxy.StatusChanged.connect.assert_called_once_with(callback)

    def test_signal_subscription_config_changed(self):
        """on_config_changed should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_config_changed(callback)
        mock_proxy.ConfigChanged.connect.assert_called_once_with(callback)

    def test_signal_subscription_error(self):
        """on_error should subscribe callback to signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_error(callback)
        mock_proxy.Error.connect.assert_called_once_with(callback)

    def test_signal_subscription_when_disconnected(self):
        """Signal subscriptions should not raise when disconnected."""
        _, client, _, _ = self._make_client(daemon_available=False)
        callback = MagicMock()
        # Should not raise
        client.on_transcription_ready(callback)
        client.on_recording_started(callback)
        client.on_level_update(callback)
        client.on_error(callback)

    # ─── Disconnect tests ────────────────────────────────────────────────

    def test_disconnect_cleans_up(self):
        """disconnect should set is_connected=False and clear proxy."""
        _, client, _, _ = self._make_client()
        assert client.is_connected is True
        client.disconnect()
        assert client.is_connected is False

    def test_disconnect_when_already_disconnected(self):
        """disconnect when already disconnected should not raise."""
        _, client, _, _ = self._make_client(daemon_available=False)
        assert client.is_connected is False
        client.disconnect()  # Should not raise
        assert client.is_connected is False
