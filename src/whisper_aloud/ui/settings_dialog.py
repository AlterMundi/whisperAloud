"""Settings dialog for WhisperAloud configuration."""

import logging
from typing import Optional
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from ..config import WhisperAloudConfig, ModelConfig, AudioConfig, ClipboardConfig
from ..audio import DeviceManager
from .error_handler import InputValidator, ValidationError

logger = logging.getLogger(__name__)


class SettingsDialog(Gtk.Window):
    """Settings dialog for configuring WhisperAloud."""

    def __init__(self, parent: Gtk.Window, config: WhisperAloudConfig) -> None:
        """
        Initialize the settings dialog.

        Args:
            parent: Parent window
            config: Current configuration
        """
        super().__init__()

        self.set_title("Settings")
        self.set_default_size(600, 500)
        self.set_modal(True)
        self.set_transient_for(parent)

        # Store configuration
        self._config = config
        self._parent = parent

        # Build UI
        self._build_ui()

        logger.info("Settings dialog initialized")

    def _build_ui(self) -> None:
        """Build the settings dialog UI."""
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(main_box)

        # Header bar
        header_bar = Gtk.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label="Settings"))

        # Cancel button
        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", self._on_cancel_clicked)
        header_bar.pack_start(cancel_button)

        # Save button
        save_button = Gtk.Button(label="Save")
        save_button.add_css_class("suggested-action")
        save_button.connect("clicked", self._on_save_clicked)
        header_bar.pack_end(save_button)

        main_box.append(header_bar)

        # Stack for tabbed interface
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        # Stack switcher
        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(self.stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        stack_switcher.set_margin_top(12)
        stack_switcher.set_margin_bottom(12)
        main_box.append(stack_switcher)

        # Add pages
        self._add_model_page()
        self._add_audio_page()
        self._add_clipboard_page()

        main_box.append(self.stack)

    def _add_model_page(self) -> None:
        """Add model configuration page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)

        # Model name
        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        model_label = Gtk.Label(label="Model:")
        model_label.set_halign(Gtk.Align.START)
        model_label.set_hexpand(True)

        self.model_dropdown = Gtk.DropDown.new_from_strings([
            "tiny", "base", "small", "medium", "large"
        ])
        # Set current value
        models = ["tiny", "base", "small", "medium", "large"]
        if self._config.model.name in models:
            self.model_dropdown.set_selected(models.index(self._config.model.name))

        model_box.append(model_label)
        model_box.append(self.model_dropdown)
        page.append(model_box)

        # Language
        lang_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lang_label = Gtk.Label(label="Language:")
        lang_label.set_halign(Gtk.Align.START)
        lang_label.set_hexpand(True)

        self.language_entry = Gtk.Entry()
        self.language_entry.set_text(self._config.transcription.language or "")
        self.language_entry.set_placeholder_text("Auto-detect")

        lang_box.append(lang_label)
        lang_box.append(self.language_entry)
        page.append(lang_box)

        # Device (CPU/CUDA)
        device_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        device_label = Gtk.Label(label="Compute Device:")
        device_label.set_halign(Gtk.Align.START)
        device_label.set_hexpand(True)

        self.device_dropdown = Gtk.DropDown.new_from_strings(["cpu", "cuda"])
        if self._config.model.device == "cuda":
            self.device_dropdown.set_selected(1)

        device_box.append(device_label)
        device_box.append(self.device_dropdown)
        page.append(device_box)

        # Help text
        help_label = Gtk.Label()
        help_label.set_markup(
            "<small>Model sizes: tiny (fastest), base, small, medium, large (most accurate)\n"
            "Language: Leave blank for auto-detection or use ISO 639-1 code (e.g., 'en', 'es')\n"
            "CUDA requires NVIDIA GPU with drivers installed</small>"
        )
        help_label.set_halign(Gtk.Align.START)
        help_label.add_css_class("dim-label")
        page.append(help_label)

        self.stack.add_titled(page, "model", "Model")

    def _add_audio_page(self) -> None:
        """Add audio configuration page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)

        # Input device selector
        device_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        device_label = Gtk.Label(label="Input Device:")
        device_label.set_halign(Gtk.Align.START)
        device_box.append(device_label)

        # Get available devices
        self._devices = DeviceManager.list_input_devices()
        device_names = [
            f"{d.name} ({d.channels}ch, {d.sample_rate}Hz)"
            + (" [DEFAULT]" if d.is_default else "")
            for d in self._devices
        ]

        self.device_dropdown = Gtk.DropDown.new_from_strings(device_names)

        # Set current device
        current_device_id = self._config.audio.device_id
        if current_device_id is not None:
            for i, device in enumerate(self._devices):
                if device.id == current_device_id:
                    self.device_dropdown.set_selected(i)
                    break
        else:
            # Find default device
            for i, device in enumerate(self._devices):
                if device.is_default:
                    self.device_dropdown.set_selected(i)
                    break

        device_box.append(self.device_dropdown)
        page.append(device_box)

        # Sample rate
        rate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        rate_label = Gtk.Label(label="Sample Rate:")
        rate_label.set_halign(Gtk.Align.START)
        rate_label.set_hexpand(True)

        self.sample_rate_entry = Gtk.Entry()
        self.sample_rate_entry.set_text(str(self._config.audio.sample_rate))

        rate_box.append(rate_label)
        rate_box.append(self.sample_rate_entry)
        page.append(rate_box)

        # VAD enabled
        self.vad_switch = Gtk.Switch()
        self.vad_switch.set_active(self._config.audio.vad_enabled)

        vad_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vad_label = Gtk.Label(label="Voice Activity Detection:")
        vad_label.set_halign(Gtk.Align.START)
        vad_label.set_hexpand(True)

        vad_box.append(vad_label)
        vad_box.append(self.vad_switch)
        page.append(vad_box)

        # VAD threshold
        threshold_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        threshold_label = Gtk.Label(label="VAD Threshold:")
        threshold_label.set_halign(Gtk.Align.START)
        threshold_label.set_hexpand(True)

        self.vad_threshold_entry = Gtk.Entry()
        self.vad_threshold_entry.set_text(str(self._config.audio.vad_threshold))

        threshold_box.append(threshold_label)
        threshold_box.append(self.vad_threshold_entry)
        page.append(threshold_box)

        # Normalize audio
        self.normalize_switch = Gtk.Switch()
        self.normalize_switch.set_active(self._config.audio.normalize_audio)

        normalize_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        normalize_label = Gtk.Label(label="Normalize Audio:")
        normalize_label.set_halign(Gtk.Align.START)
        normalize_label.set_hexpand(True)

        normalize_box.append(normalize_label)
        normalize_box.append(self.normalize_switch)
        page.append(normalize_box)

        # Help text
        help_label = Gtk.Label()
        help_label.set_markup(
            "<small>Sample rate: 16000 Hz recommended for Whisper\n"
            "VAD: Filters out silent portions of audio\n"
            "Normalize: Adjust volume levels automatically</small>"
        )
        help_label.set_halign(Gtk.Align.START)
        help_label.add_css_class("dim-label")
        page.append(help_label)

        self.stack.add_titled(page, "audio", "Audio")

    def _add_clipboard_page(self) -> None:
        """Add clipboard configuration page."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_top(24)
        page.set_margin_bottom(24)

        # Auto-copy
        self.auto_copy_switch = Gtk.Switch()
        self.auto_copy_switch.set_active(self._config.clipboard.auto_copy)

        auto_copy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto_copy_label = Gtk.Label(label="Auto-copy to Clipboard:")
        auto_copy_label.set_halign(Gtk.Align.START)
        auto_copy_label.set_hexpand(True)

        auto_copy_box.append(auto_copy_label)
        auto_copy_box.append(self.auto_copy_switch)
        page.append(auto_copy_box)

        # Auto-paste
        self.auto_paste_switch = Gtk.Switch()
        self.auto_paste_switch.set_active(self._config.clipboard.auto_paste)

        auto_paste_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto_paste_label = Gtk.Label(label="Auto-paste (Ctrl+V simulation):")
        auto_paste_label.set_halign(Gtk.Align.START)
        auto_paste_label.set_hexpand(True)

        auto_paste_box.append(auto_paste_label)
        auto_paste_box.append(self.auto_paste_switch)
        page.append(auto_paste_box)

        # Paste delay
        delay_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        delay_label = Gtk.Label(label="Paste Delay (ms):")
        delay_label.set_halign(Gtk.Align.START)
        delay_label.set_hexpand(True)

        self.paste_delay_entry = Gtk.Entry()
        self.paste_delay_entry.set_text(str(self._config.clipboard.paste_delay_ms))

        delay_box.append(delay_label)
        delay_box.append(self.paste_delay_entry)
        page.append(delay_box)

        # Help text
        help_label = Gtk.Label()
        help_label.set_markup(
            "<small>Auto-copy: Automatically copy transcription to clipboard\n"
            "Auto-paste: Simulate Ctrl+V after copying (requires ydotool/xdotool)\n"
            "Paste delay: Time to wait before simulating paste</small>"
        )
        help_label.set_halign(Gtk.Align.START)
        help_label.add_css_class("dim-label")
        page.append(help_label)

        self.stack.add_titled(page, "clipboard", "Clipboard")

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """
        Handle save button click.

        Args:
            button: The button that was clicked
        """
        logger.info("Saving settings")

        try:
            # Update model config
            models = ["tiny", "base", "small", "medium", "large"]
            self._config.model.name = models[self.model_dropdown.get_selected()]

            # Validate language code
            lang = self.language_entry.get_text().strip()
            validated_lang = InputValidator.validate_language_code(lang)
            self._config.transcription.language = validated_lang if validated_lang else None

            devices = ["cpu", "cuda"]
            self._config.model.device = devices[self.device_dropdown.get_selected()]

            # Update audio config
            selected_device_idx = self.device_dropdown.get_selected()
            if selected_device_idx < len(self._devices):
                self._config.audio.device_id = self._devices[selected_device_idx].id

            # Validate audio settings
            self._config.audio.sample_rate = InputValidator.validate_integer(
                self.sample_rate_entry.get_text(),
                min_value=8000,
                max_value=48000,
                field_name="Sample rate"
            )
            self._config.audio.vad_enabled = self.vad_switch.get_active()
            self._config.audio.vad_threshold = InputValidator.validate_float(
                self.vad_threshold_entry.get_text(),
                min_value=0.0,
                max_value=1.0,
                field_name="VAD threshold"
            )
            self._config.audio.normalize_audio = self.normalize_switch.get_active()

            # Update clipboard config
            self._config.clipboard.auto_copy = self.auto_copy_switch.get_active()
            self._config.clipboard.auto_paste = self.auto_paste_switch.get_active()
            self._config.clipboard.paste_delay_ms = InputValidator.validate_integer(
                self.paste_delay_entry.get_text(),
                min_value=0,
                max_value=5000,
                field_name="Paste delay"
            )

            # Save to file
            self._save_config()

            # Show success message
            self._show_message("Settings saved successfully", Gtk.MessageType.INFO)

            # Close dialog
            self.close()

        except ValidationError as e:
            logger.error(f"Validation error: {e}")
            self._show_message(f"Invalid input: {e}", Gtk.MessageType.ERROR)
        except ValueError as e:
            logger.error(f"Invalid input: {e}")
            self._show_message(f"Invalid input: {e}", Gtk.MessageType.ERROR)
        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
            self._show_message(f"Error saving settings: {e}", Gtk.MessageType.ERROR)

    def _save_config(self) -> None:
        """Save configuration to file."""
        # Determine config path
        config_dir = Path.home() / ".config" / "whisper_aloud"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.json"

        # Convert config to dict
        config_dict = {
            "model": {
                "name": self._config.model.name,
                "device": self._config.model.device,
            },
            "transcription": {
                "language": self._config.transcription.language,
                "task": self._config.transcription.task,
            },
            "audio": {
                "sample_rate": self._config.audio.sample_rate,
                "device_id": self._config.audio.device_id,
                "channels": self._config.audio.channels,
                "chunk_duration_ms": self._config.audio.chunk_duration_ms,
                "vad_enabled": self._config.audio.vad_enabled,
                "vad_threshold": self._config.audio.vad_threshold,
                "normalize_audio": self._config.audio.normalize_audio,
                "max_recording_duration": self._config.audio.max_recording_duration,
            },
            "clipboard": {
                "auto_copy": self._config.clipboard.auto_copy,
                "auto_paste": self._config.clipboard.auto_paste,
                "paste_delay_ms": self._config.clipboard.paste_delay_ms,
                "timeout_seconds": self._config.clipboard.timeout_seconds,
                "fallback_path": self._config.clipboard.fallback_path,
            },
        }

        # Write to file
        import json
        with open(config_path, "w") as f:
            json.dump(config_dict, f, indent=2)

        logger.info(f"Configuration saved to {config_path}")

    def _on_cancel_clicked(self, button: Gtk.Button) -> None:
        """
        Handle cancel button click.

        Args:
            button: The button that was clicked
        """
        logger.info("Settings dialog cancelled")
        self.close()

    def _show_message(self, message: str, message_type: Gtk.MessageType) -> None:
        """
        Show a message dialog.

        Args:
            message: Message to display
            message_type: Type of message (INFO, WARNING, ERROR)
        """
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=message_type,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        dialog.connect("response", lambda d, r: d.close())
        dialog.present()
