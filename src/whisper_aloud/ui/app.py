"""GTK4 application class for WhisperAloud."""

import logging
from typing import Optional

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib

from .file_transcription_logic import pick_first_supported_path
from .main_window import MainWindow
from .styles import install_app_css

logger = logging.getLogger(__name__)


class WhisperAloudApp(Adw.Application):
    """Main application class managing lifecycle and window.

    An Adw.Application (HIG-01): its startup initializes libadwaita, which
    loads the Adwaita stylesheet and lets Adw.StyleManager follow the
    desktop's light/dark preference. The colour scheme is left at its
    default (follow the system) on purpose.
    """

    def __init__(self) -> None:
        """Initialize the application."""
        super().__init__(
            application_id='org.fede.whisperaloud.Gui',
            # HANDLES_OPEN (WHISP-96): "Open With WhisperAloud" on an audio/
            # video file, or `whisper-aloud-gui <file>` from a shell, invokes
            # do_open() below with that file instead of (or in addition to)
            # do_activate() -- see data/org.fede.whisperaloud.desktop's
            # Exec=...%F and MimeType=.
            flags=Gio.ApplicationFlags.HANDLES_OPEN
        )
        self.window: Optional[MainWindow] = None
        logger.info("WhisperAloud application initialized")

    def do_startup(self) -> None:
        """
        Called on application startup (once).

        Sets up actions, keyboard shortcuts, and other one-time initialization.
        """
        Adw.Application.do_startup(self)
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

    def do_open(self, files, n_files: int, hint: str) -> None:
        """Called (instead of/alongside do_activate) when GApplication is
        asked to open one or more files -- "Open With WhisperAloud" on an
        audio/video file, or `whisper-aloud-gui <file>` (WHISP-96).

        Only the first supported audio/video file among `files` is used;
        see pick_first_supported_path()'s docstring for why a single file
        is this ticket's deliberate scope.
        """
        logger.info(f"Application asked to open {n_files} file(s)")

        if not self.window:
            logger.info("Creating main window (opened via file)")
            self.window = MainWindow(application=self)
        self.window.present()

        paths = [f.get_path() for f in files]
        path = pick_first_supported_path(paths)
        if path is None:
            logger.warning(f"No supported audio/video file among opened paths: {paths!r}")
            return
        GLib.idle_add(self.window.open_file_for_transcription, path)

    def do_shutdown(self) -> None:
        """
        Called on application shutdown.

        Clean up resources before exit.
        """
        logger.info("Application shutting down")

        # Cleanup window resources if it exists
        if self.window:
            self.window.cleanup()

        Adw.Application.do_shutdown(self)
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
