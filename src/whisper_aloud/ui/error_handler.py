"""Error handling utilities for the UI."""

import logging
from enum import Enum
from typing import Callable, List, Optional, Tuple

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import dialogs

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ErrorDialog:
    """Helper for creating error dialogs with recovery options."""

    @staticmethod
    def _build_responses(
        recovery_action: Optional[Tuple[str, Callable]],
    ) -> List[Tuple[str, str, "Adw.ResponseAppearance"]]:
        """Pure response-list builder (WHISP-80), kept separate from dialog
        construction so the button set is unit-testable without GTK/a
        display.

        Returns ``(response_id, label, appearance)`` tuples in presentation
        order: the optional recovery action first, then "Close" last --
        Close is always present and is always the default/close response.
        """
        responses = []
        if recovery_action is not None:
            label, _callback = recovery_action
            responses.append(("recovery", label, Adw.ResponseAppearance.SUGGESTED))
        responses.append(("close", "Close", Adw.ResponseAppearance.DEFAULT))
        return responses

    @staticmethod
    def show_error(
        parent: Gtk.Window,
        title: str,
        message: str,
        details: Optional[str] = None,
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        recovery_action: Optional[Tuple[str, Callable]] = None
    ):
        """
        Show an error dialog with optional details and recovery action.

        Uses dialogs.show_dialog (WHISP-80) -- Adw.AlertDialog on libadwaita
        >= 1.5, falling back to the deprecated Adw.MessageDialog on older
        libadwaita, same as confirm(). Note severity no longer changes the
        dialog's icon/styling: neither AlertDialog nor MessageDialog expose
        the Gtk.MessageType concept Gtk.MessageDialog had; ``severity`` is
        kept for API compatibility with callers.

        Args:
            parent: Parent window
            title: Dialog title
            message: Main error message
            details: Optional detailed error information
            severity: Error severity level (currently unused for styling)
            recovery_action: Optional tuple of (button_label, callback)

        Returns the constructed dialog (useful for tests).
        """
        del severity  # kept for API compatibility; no Adw equivalent to apply it to

        extra_child = None
        if details:
            expander = Gtk.Expander(label="Show Details")
            details_view = Gtk.TextView()
            details_view.set_editable(False)
            details_view.set_wrap_mode(Gtk.WrapMode.WORD)
            details_view.get_buffer().set_text(details)
            details_view.set_margin_start(6)
            details_view.set_margin_end(6)
            details_view.set_margin_top(6)
            details_view.set_margin_bottom(6)

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_min_content_height(150)
            scrolled.set_child(details_view)

            expander.set_child(scrolled)
            extra_child = expander

        def _on_response(response: str) -> None:
            if response == "recovery" and recovery_action:
                _, callback = recovery_action
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Recovery action failed: {e}", exc_info=True)

        return dialogs.show_dialog(
            parent=parent,
            heading=title,
            body=message,
            responses=ErrorDialog._build_responses(recovery_action),
            default_response="close",
            close_response="close",
            on_response=_on_response,
            extra_child=extra_child,
        )


class ValidationError(Exception):
    """Raised when user input validation fails."""
    pass


class InputValidator:
    """Utilities for validating user input."""

    @staticmethod
    def validate_integer(
        value: str,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        field_name: str = "Value"
    ) -> int:
        """
        Validate integer input.

        Args:
            value: String value to validate
            min_value: Optional minimum value
            max_value: Optional maximum value
            field_name: Field name for error messages

        Returns:
            Validated integer value

        Raises:
            ValidationError: If validation fails
        """
        try:
            int_value = int(value)
        except ValueError:
            raise ValidationError(f"{field_name} must be a valid integer")

        if min_value is not None and int_value < min_value:
            raise ValidationError(f"{field_name} must be at least {min_value}")

        if max_value is not None and int_value > max_value:
            raise ValidationError(f"{field_name} must be at most {max_value}")

        return int_value

    @staticmethod
    def validate_float(
        value: str,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        field_name: str = "Value"
    ) -> float:
        """
        Validate float input.

        Args:
            value: String value to validate
            min_value: Optional minimum value
            max_value: Optional maximum value
            field_name: Field name for error messages

        Returns:
            Validated float value

        Raises:
            ValidationError: If validation fails
        """
        try:
            float_value = float(value)
        except ValueError:
            raise ValidationError(f"{field_name} must be a valid number")

        if min_value is not None and float_value < min_value:
            raise ValidationError(f"{field_name} must be at least {min_value}")

        if max_value is not None and float_value > max_value:
            raise ValidationError(f"{field_name} must be at most {max_value}")

        return float_value

    @staticmethod
    def validate_language_code(value: str) -> str:
        """
        Validate language code (ISO 639-1).

        Args:
            value: Language code to validate

        Returns:
            Validated language code

        Raises:
            ValidationError: If validation fails
        """
        if not value:
            return value  # Empty is OK (auto-detect)

        # Basic validation: 2-3 lowercase letters
        if not (2 <= len(value) <= 3 and value.isalpha() and value.islower()):
            raise ValidationError(
                "Language code must be 2-3 lowercase letters (e.g., 'en', 'es')"
            )

        return value


def handle_audio_device_error(parent: Gtk.Window, error: Exception) -> None:
    """
    Handle audio device errors with helpful recovery options.

    Args:
        parent: Parent window
        error: Audio device error
    """
    ErrorDialog.show_error(
        parent=parent,
        title="Audio Device Error",
        message="Failed to access audio device. Please check your microphone is connected and working.",
        details=str(error),
        severity=ErrorSeverity.ERROR,
        recovery_action=("Open Settings", lambda: logger.info("Settings recovery action"))
    )


def handle_model_load_error(parent: Gtk.Window, error: Exception) -> None:
    """
    Handle model loading errors.

    Args:
        parent: Parent window
        error: Model loading error
    """
    error_msg = str(error)

    # Check for common issues
    if "CUDA" in error_msg or "cuda" in error_msg:
        message = (
            "Failed to load model with CUDA. This usually means:\n"
            "• NVIDIA drivers are not installed\n"
            "• GPU is not available\n"
            "• CUDA is not properly configured\n\n"
            "Try using CPU mode in settings instead."
        )
    elif "download" in error_msg.lower() or "network" in error_msg.lower():
        message = (
            "Failed to download model. Please check:\n"
            "• Internet connection is active\n"
            "• Firewall is not blocking downloads\n"
            "• Sufficient disk space available"
        )
    else:
        message = "Failed to load Whisper model. See details for more information."

    ErrorDialog.show_error(
        parent=parent,
        title="Model Loading Error",
        message=message,
        details=error_msg,
        severity=ErrorSeverity.ERROR
    )


def handle_transcription_error(parent: Gtk.Window, error: Exception) -> None:
    """
    Handle transcription errors.

    Args:
        parent: Parent window
        error: Transcription error
    """
    ErrorDialog.show_error(
        parent=parent,
        title="Transcription Error",
        message="Failed to transcribe audio. This may be due to:\n"
                "• No speech detected in the recording\n"
                "• Audio quality is too poor\n"
                "• Model encountered an error",
        details=str(error),
        severity=ErrorSeverity.ERROR
    )


def handle_clipboard_error(parent: Gtk.Window, error: Exception) -> None:
    """
    Handle clipboard errors.

    Args:
        parent: Parent window
        error: Clipboard error
    """
    ErrorDialog.show_error(
        parent=parent,
        title="Clipboard Error",
        message="Failed to copy to clipboard. Text has been saved to fallback file:\n"
                "/tmp/whisper_aloud_clipboard.txt",
        details=str(error),
        severity=ErrorSeverity.WARNING
    )
