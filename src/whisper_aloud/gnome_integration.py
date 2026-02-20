"""GNOME integration utilities for WhisperAloud."""

import logging
from typing import Optional

from .config import WhisperAloudConfig

logger = logging.getLogger(__name__)

# Import GObject libraries conditionally
try:
    import gi
    gi.require_version('Notify', '0.7')
    from gi.repository import Notify, GLib
    HAS_NOTIFY = True
except ImportError:
    HAS_NOTIFY = False
    logger.warning("libnotify not available, notifications disabled")


class NotificationManager:
    """Manages desktop notifications for WhisperAloud."""

    def __init__(self, config: Optional[WhisperAloudConfig] = None):
        """Initialize notification manager."""
        self.config = config or WhisperAloudConfig.load()

        if not HAS_NOTIFY:
            logger.warning("Notifications not available")
            return

        # Initialize libnotify
        if not Notify.is_initted():
            Notify.init("WhisperAloud")

        self.app_name = "WhisperAloud"
        logger.info("NotificationManager initialized")

    def _is_enabled(self, notification_type: str) -> bool:
        """Return whether a notification type is enabled in config."""
        notifications = self.config.notifications
        if not notifications.enabled:
            return False
        return bool(getattr(notifications, notification_type, True))

    def show_recording_started(self) -> None:
        """Show notification when recording starts."""
        if not HAS_NOTIFY or not self._is_enabled("recording_started"):
            return

        notification = Notify.Notification.new(
            self.app_name,
            "Recording started...",
            "audio-input-microphone-symbolic"
        )
        notification.set_urgency(Notify.Urgency.NORMAL)
        notification.show()

    def show_recording_stopped(self) -> None:
        """Show notification when recording stops."""
        if not HAS_NOTIFY or not self._is_enabled("recording_stopped"):
            return

        notification = Notify.Notification.new(
            self.app_name,
            "Recording stopped, transcribing...",
            "audio-input-microphone-symbolic"
        )
        notification.set_urgency(Notify.Urgency.NORMAL)
        notification.show()

    def show_transcription_completed(self, text: str) -> None:
        """Show notification when transcription completes."""
        if not HAS_NOTIFY or not self._is_enabled("transcription_completed"):
            return

        # Truncate text for notification
        preview = text[:100] + "..." if len(text) > 100 else text

        notification = Notify.Notification.new(
            self.app_name,
            f"Transcription complete: {preview}",
            "text-x-generic-symbolic"
        )
        notification.set_urgency(Notify.Urgency.NORMAL)

        # Add action to copy to clipboard
        notification.add_action(
            "copy",
            "Copy to Clipboard",
            self._on_copy_action,
            text
        )

        notification.show()

    def show_error(self, error_message: str) -> None:
        """Show error notification."""
        if not HAS_NOTIFY or not self._is_enabled("error"):
            return

        notification = Notify.Notification.new(
            self.app_name,
            f"Error: {error_message}",
            "dialog-error-symbolic"
        )
        notification.set_urgency(Notify.Urgency.CRITICAL)
        notification.show()

    def _on_copy_action(self, notification, action, text):
        """Handle copy action from notification."""
        # Import here to avoid circular imports
        from .clipboard.clipboard_manager import ClipboardManager

        try:
            clipboard = ClipboardManager(self.config.clipboard)
            if clipboard.copy(text):
                logger.info("Text copied to clipboard from notification")
            else:
                logger.warning("Failed to copy text to clipboard")
        except Exception as e:
            logger.error(f"Failed to copy from notification: {e}")

    def cleanup(self) -> None:
        """Clean up notification resources."""
        if HAS_NOTIFY and Notify.is_initted():
            Notify.uninit()
