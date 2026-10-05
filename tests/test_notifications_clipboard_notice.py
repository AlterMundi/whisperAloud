"""NotificationManager.show_clipboard_notice (WHISP-119), libnotify mocked."""

from unittest.mock import MagicMock, patch

from whisper_aloud import gnome_integration
from whisper_aloud.config import WhisperAloudConfig


def _manager(config=None):
    with patch.object(gnome_integration, "HAS_NOTIFY", True), \
            patch.object(gnome_integration, "Notify", MagicMock(), create=True):
        return gnome_integration.NotificationManager(config or WhisperAloudConfig())


def test_shows_the_given_message():
    notify = MagicMock()
    mgr = _manager()
    with patch.object(gnome_integration, "HAS_NOTIFY", True), \
            patch.object(gnome_integration, "Notify", notify, create=True):
        mgr.show_clipboard_notice("Text copied — press Ctrl+V to paste")
    args = notify.Notification.new.call_args.args
    assert args[1] == "Text copied — press Ctrl+V to paste"
    notify.Notification.new.return_value.show.assert_called_once()


def test_respects_disabled_notifications():
    config = WhisperAloudConfig()
    config.notifications.transcription_completed = False
    notify = MagicMock()
    mgr = _manager(config)
    with patch.object(gnome_integration, "HAS_NOTIFY", True), \
            patch.object(gnome_integration, "Notify", notify, create=True):
        mgr.show_clipboard_notice("x")
    notify.Notification.new.assert_not_called()
