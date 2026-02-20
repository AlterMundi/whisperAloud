"""GTK4 application class for WhisperAloud."""

import logging
from typing import Optional

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, Gtk

from .main_window import MainWindow
from .styles import install_app_css

logger = logging.getLogger(__name__)


class WhisperAloudApp(Gtk.Application):
    """Main application class managing lifecycle and window."""

    def __init__(self) -> None:
        """Initialize the application."""
        super().__init__(
            application_id='org.fede.whisperaloud.Gui',
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.window: Optional[MainWindow] = None
        logger.info("WhisperAloud application initialized")

    def do_startup(self) -> None:
        """
        Called on application startup (once).

        Sets up actions, keyboard shortcuts, and other one-time initialization.
        """
        Gtk.Application.do_startup(self)
        logger.info("Application startup")
        install_app_css()

        # Create actions
        self._create_actions()

        # Set up keyboard shortcuts
        self._setup_shortcuts()

    def do_activate(self) -> None:
        """
        Called when application is activated.

        Creates window if it doesn't exist, or presents existing window.
        """
        logger.info("Application activated")

        # Get existing window or create new one
        if not self.window:
            logger.info("Creating main window")
            self.window = MainWindow(application=self)

        # Present window to user
        self.window.present()

    def do_shutdown(self) -> None:
        """
        Called on application shutdown.

        Clean up resources before exit.
        """
        logger.info("Application shutting down")

        # Cleanup window resources if it exists
        if self.window:
            self.window.cleanup()

        Gtk.Application.do_shutdown(self)
        logger.info("Application shutdown complete")

    def _create_actions(self) -> None:
        """Create application actions."""
        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit_action)
        self.add_action(quit_action)

        logger.debug("Application actions created")

    def _setup_shortcuts(self) -> None:
        """Set up keyboard shortcuts."""
        # Ctrl+Q to quit
        self.set_accels_for_action("app.quit", ["<Ctrl>Q"])

        logger.debug("Keyboard shortcuts configured")

    def _on_quit_action(self, action: Gio.SimpleAction, parameter: None) -> None:
        """
        Handle quit action.

        Args:
            action: The action that was activated
            parameter: Action parameter (unused)
        """
        logger.info("Quit action triggered")
        self.quit()
