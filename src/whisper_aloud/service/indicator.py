"""System tray indicator using AyatanaAppIndicator3."""

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Try to import GTK3 and AyatanaAppIndicator3
try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3, Gtk
    HAS_INDICATOR = True
except (ImportError, ValueError) as e:
    AyatanaAppIndicator3 = None
    Gtk = None
    HAS_INDICATOR = False
    logger.info(f"AppIndicator not available: {e}")


class WhisperAloudIndicator:
    """System tray indicator for WhisperAloud.

    Shows recording state via icon changes and provides a context menu
    for quick actions. Uses AyatanaAppIndicator3 (GTK3).

    If AyatanaAppIndicator3 is not available, creates a no-op indicator
    that silently ignores all calls.

    Args:
        on_toggle: Callback for toggle recording action.
        on_open_gui: Callback to open/raise the GUI window.
        on_quit: Callback to quit the application.
    """

    # Icon names for each state (standard freedesktop icon names)
    ICONS = {
        "idle": "audio-input-microphone-symbolic",
        "recording": "media-record-symbolic",
        "transcribing": "system-run-symbolic",
        "error": "dialog-error-symbolic",
    }

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_open_gui: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ):
        self._on_toggle = on_toggle
        self._on_open_gui = on_open_gui
        self._on_quit = on_quit
        self._indicator = None
        self._menu = None
        self._last_text_item = None
        self._last_text = ""
        self._available = HAS_INDICATOR

        if not self._available:
            logger.warning("AppIndicator not available, tray icon disabled")
            return

        try:
            self._indicator = AyatanaAppIndicator3.Indicator.new(
                "whisper-aloud",
                self.ICONS["idle"],
                AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
            self._indicator.set_title("WhisperAloud")
            self._build_menu()
            logger.info("AppIndicator created successfully")
        except Exception as e:
            logger.warning(f"Failed to create AppIndicator: {e}")
            self._available = False

    @property
    def available(self) -> bool:
        """Whether the indicator is available and active."""
        return self._available

    def _build_menu(self) -> None:
        """Build the context menu."""
        self._menu = Gtk.Menu()

        # Toggle Recording
        toggle_item = Gtk.MenuItem(label="Toggle Recording")
        toggle_item.connect("activate", lambda _: self._on_toggle())
        self._menu.append(toggle_item)

        # Open WhisperAloud
        if self._on_open_gui:
            open_item = Gtk.MenuItem(label="Open WhisperAloud")
            open_item.connect("activate", lambda _: self._on_open_gui())
            self._menu.append(open_item)

        # Separator
        self._menu.append(Gtk.SeparatorMenuItem())

        # Last transcription
        self._last_text_item = Gtk.MenuItem(label="(No recent transcription)")
        self._last_text_item.set_sensitive(False)
        self._menu.append(self._last_text_item)

        # Separator
        self._menu.append(Gtk.SeparatorMenuItem())

        # Quit
        if self._on_quit:
            quit_item = Gtk.MenuItem(label="Quit")
            quit_item.connect("activate", lambda _: self._on_quit())
            self._menu.append(quit_item)

        self._menu.show_all()
        self._indicator.set_menu(self._menu)

    def set_state(self, state: str) -> None:
        """Update indicator icon for given state.

        Args:
            state: One of "idle", "recording", "transcribing", "error".
        """
        if not self._available:
            return
        icon = self.ICONS.get(state, self.ICONS["idle"])
        self._indicator.set_icon_full(icon, f"WhisperAloud: {state}")

    def set_last_text(self, text: str) -> None:
        """Update the 'last transcription' menu item.

        Args:
            text: The transcription text. Truncated to 50 chars for display.
        """
        if not self._available or not self._last_text_item:
            return
        self._last_text = text
        display = text[:50] + "..." if len(text) > 50 else text
        self._last_text_item.set_label(f'Last: "{display}"')
        self._last_text_item.set_sensitive(True)
