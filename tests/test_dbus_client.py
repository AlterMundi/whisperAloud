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

    def test_search_history_calls_daemon(self):
        """search_history should call proxy.SearchHistory."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.SearchHistory.return_value = [{"text": "needle"}]
        result = client.search_history("needle", limit=10)
        mock_proxy.SearchHistory.assert_called_once_with("needle", 10)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_get_favorite_history_calls_daemon(self):
        """get_favorite_history should call proxy.GetFavoriteHistory."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.GetFavoriteHistory.return_value = [{"favorite": True}]
        result = client.get_favorite_history(limit=20)
        mock_proxy.GetFavoriteHistory.assert_called_once_with(20)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_toggle_history_favorite_calls_daemon(self):
        """toggle_history_favorite should call proxy.ToggleHistoryFavorite."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.ToggleHistoryFavorite.return_value = True
        result = client.toggle_history_favorite(7)
        mock_proxy.ToggleHistoryFavorite.assert_called_once_with(7)
        assert result is True

    def test_delete_history_entry_calls_daemon(self):
        """delete_history_entry should call proxy.DeleteHistoryEntry."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.DeleteHistoryEntry.return_value = True
        result = client.delete_history_entry(9)
        mock_proxy.DeleteHistoryEntry.assert_called_once_with(9)
        assert result is True

    def test_get_config_calls_daemon(self):
        """get_config should call proxy.GetConfig."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.GetConfig.return_value = {"model.name": "base"}
        result = client.get_config()
        mock_proxy.GetConfig.assert_called_once()
        assert isinstance(result, dict)

    def test_set_config_calls_daemon(self):
        """set_config should call proxy.SetConfig with each value wrapped as
        a GLib.Variant (a{sv}), not the raw Python value -- pydbus rejects a
        plain dict for an a{sv} argument."""
        client_module, client, _, mock_proxy = self._make_client()
        mock_proxy.SetConfig.return_value = True
        result = client.set_config({"model.name": "small"})
        expected = client_module._wrap_config_changes({"model.name": "small"})
        mock_proxy.SetConfig.assert_called_once_with(expected)
        assert result is True

    def test_set_config_rejects_unsupported_value_type(self):
        """An unsupported value type must not reach the proxy; set_config
        should report the TypeError the same way as any other failure."""
        _, client, _, mock_proxy = self._make_client()
        result = client.set_config({"bad.key": ["not", "a", "scalar"]})
        mock_proxy.SetConfig.assert_not_called()
        assert result is False

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

    def test_get_api_version_reads_property(self):
        """get_api_version should read the ApiVersion property (not call it)."""
        _, client, _, mock_proxy = self._make_client()
        mock_proxy.ApiVersion = "1.0.0"
        result = client.get_api_version()
        assert result == "1.0.0"

    def test_get_api_version_returns_empty_when_disconnected(self):
        """get_api_version should return "" when not connected."""
        _, client, _, _ = self._make_client(daemon_available=False)
        assert client.get_api_version() == ""

    def test_get_api_version_handles_property_read_exception(self):
        """get_api_version should catch exceptions and return ""."""

        class _RaisingProxy:
            @property
            def ApiVersion(self):
                raise Exception("no such property")

        client_module = _import_client_module()
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_bus.get.return_value = _RaisingProxy()
            client = client_module.WhisperAloudClient()
            assert client.get_api_version() == ""

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
        assert client.search_history("hello") == []
        assert client.get_favorite_history() == []
        assert client.toggle_history_favorite(1) is False
        assert client.delete_history_entry(1) is False
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

    def test_signal_subscription_partial_transcript(self):
        """on_partial_transcript should subscribe callback to signal (WHISP-57)."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_partial_transcript(callback)
        mock_proxy.PartialTranscript.connect.assert_called_once_with(callback)

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

    def test_signal_subscription_model_state_changed(self):
        """on_model_state_changed should subscribe callback to the 1.6.0 signal."""
        _, client, _, mock_proxy = self._make_client()
        callback = MagicMock()
        client.on_model_state_changed(callback)
        mock_proxy.ModelStateChanged.connect.assert_called_once_with(callback)

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


class TestIsStartingErrorClassification:
    """Field bug 1: distinguish "daemon mid-startup" from "truly unavailable"."""

    def _make_client_with_error(self, client_module, message: str):
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_bus.get.side_effect = KeyError(message)
            return client_module.WhisperAloudClient()

    def test_is_starting_true_for_pydbus_no_interfaces_error(self):
        client_module = _import_client_module()
        client = self._make_client_with_error(
            client_module,
            "object does not export any interfaces; you might need to pass "
            "object path as the 2nd argument for get()",
        )
        assert client.is_connected is False
        assert client.is_starting is True

    def test_is_starting_false_for_unrelated_error(self):
        client_module = _import_client_module()
        client = self._make_client_with_error(
            client_module, "no such object; you might need to pass object path"
        )
        assert client.is_connected is False
        assert client.is_starting is False

    def test_is_starting_false_for_generic_connection_error(self):
        client_module = _import_client_module()
        client = self._make_client_with_error(client_module, "Connection refused")
        assert client.is_connected is False
        assert client.is_starting is False

    def test_is_starting_resets_to_false_on_successful_connect(self):
        client_module = _import_client_module()
        client = self._make_client_with_error(
            client_module, "object does not export any interfaces"
        )
        assert client.is_starting is True

        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_bus.get.return_value = MagicMock()
            assert client.connect() is True

        assert client.is_starting is False

    def test_classify_helper_directly(self):
        client_module = _import_client_module()
        assert client_module._is_starting_error(
            KeyError("object does not export any interfaces; you might need to pass")
        )
        assert not client_module._is_starting_error(KeyError("no such object"))
        assert not client_module._is_starting_error(Exception("timeout"))


class TestConnectWithActivationWait:
    """Field bug 1: CLI-style blocking wait while the daemon is starting."""

    def _client_that_is_starting(self, client_module):
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_bus.get.side_effect = KeyError("object does not export any interfaces")
            client = client_module.WhisperAloudClient()
        assert client.is_starting is True
        return client, mock_bus_cls

    def test_returns_true_immediately_if_already_connected(self):
        client_module = _import_client_module()
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_bus.get.return_value = MagicMock()
            client = client_module.WhisperAloudClient()

        sleeps = []
        result = client.connect_with_activation_wait(sleep=sleeps.append)
        assert result is True
        assert sleeps == []

    def test_returns_false_immediately_when_not_starting(self):
        client_module = _import_client_module()
        with patch.object(client_module, "SessionBus") as mock_bus_cls:
            mock_bus = MagicMock()
            mock_bus_cls.return_value = mock_bus
            mock_bus.get.side_effect = Exception("Connection refused")
            client = client_module.WhisperAloudClient()

        sleeps = []
        result = client.connect_with_activation_wait(
            max_wait_seconds=30.0, sleep=sleeps.append
        )
        assert result is False
        assert sleeps == []  # never worth retrying a non-"starting" failure

    def test_retries_while_starting_then_succeeds(self):
        client_module = _import_client_module()
        client, mock_bus_cls = self._client_that_is_starting(client_module)

        attempts = {"n": 0}

        def _fake_get(name):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise KeyError("object does not export any interfaces")
            return MagicMock()

        with patch.object(client_module, "SessionBus") as mock_bus_cls2:
            mock_bus2 = MagicMock()
            mock_bus_cls2.return_value = mock_bus2
            mock_bus2.get.side_effect = _fake_get

            fake_time = {"t": 0.0}
            sleeps = []

            def _fake_sleep(seconds):
                sleeps.append(seconds)
                fake_time["t"] += seconds

            result = client.connect_with_activation_wait(
                max_wait_seconds=10.0,
                poll_interval_seconds=1.0,
                sleep=_fake_sleep,
                clock=lambda: fake_time["t"],
            )

        assert result is True
        assert client.is_connected is True
        assert len(sleeps) == 3  # 2 failed retries, 3rd succeeds

    def test_gives_up_after_max_wait_seconds_while_still_starting(self):
        client_module = _import_client_module()
        client, _ = self._client_that_is_starting(client_module)

        with patch.object(client_module, "SessionBus") as mock_bus_cls2:
            mock_bus2 = MagicMock()
            mock_bus_cls2.return_value = mock_bus2
            mock_bus2.get.side_effect = KeyError("object does not export any interfaces")

            fake_time = {"t": 0.0}

            def _fake_sleep(seconds):
                fake_time["t"] += seconds

            result = client.connect_with_activation_wait(
                max_wait_seconds=5.0,
                poll_interval_seconds=1.0,
                sleep=_fake_sleep,
                clock=lambda: fake_time["t"],
            )

        assert result is False
        assert client.is_connected is False
        assert fake_time["t"] >= 5.0
