"""Settings "Startup" section: the "Start in background at login" switch (WHISP-87).

Self-contained so SettingsDialog only appends ``StartupSection(...).widget``.
The switch applies immediately (it is not part of the dialog's Save/Cancel
batch): inside Flatpak it opens the system's consent dialog through the
Background portal, so it uses Gtk.Switch's delayed-state pattern: the
handle moves at once (``active``) but the green ``state`` and the final
position only follow the controller's confirmed answer.
"""

import logging
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .startup_logic import MODE_PORTAL, AutostartController  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Start in background at login"
HELP_TEXT = (
    "Starts only the background service when you log in, without opening this "
    "window: the model loads and the global shortcut dictates right away."
)
UNAVAILABLE_TEXT = "Available once the background service is connected."


class StartupSection:
    def __init__(self, controller: Optional[AutostartController]) -> None:
        self._controller = controller
        self._token: Optional[int] = None
        self._syncing = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Label(label="Startup")
        heading.set_halign(Gtk.Align.START)
        heading.add_css_class("heading")
        heading.add_css_class("wa-section-title")
        heading.set_margin_top(12)
        box.append(heading)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("wa-setting-row")
        label = Gtk.Label(label=TITLE)
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        self.spinner = Gtk.Spinner()
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [TITLE])
        row.append(label)
        row.append(self.spinner)
        row.append(self.switch)
        box.append(row)

        help_label = Gtk.Label(label=HELP_TEXT, wrap=True, xalign=0)
        help_label.add_css_class("dim-label")
        box.append(help_label)

        self.status_label = Gtk.Label(wrap=True, xalign=0)
        self.status_label.set_selectable(True)
        box.append(self.status_label)

        self.widget = box

        if controller is None:
            self.switch.set_sensitive(False)
            self.status_label.set_text(UNAVAILABLE_TEXT)
            return

        self.switch.connect("state-set", self._on_state_set)
        self._token = controller.subscribe(self._render)
        box.connect("destroy", lambda *_: self.detach())
        self._render(controller)

    def detach(self) -> None:
        """Stop listening (the controller outlives this view)."""
        if self._controller is not None and self._token is not None:
            self._controller.unsubscribe(self._token)
            self._token = None

    def _on_state_set(self, switch: Gtk.Switch, want: bool) -> bool:
        if self._syncing:
            return False
        started = self._controller.set_enabled(want)
        if not started:
            self._render(self._controller)
        # True: we own `state`; _render() sets it when the answer is known.
        return True

    def _render(self, controller: AutostartController) -> None:
        self._syncing = True
        try:
            if controller.pending:
                # Keep the handle where the user put it, show we're waiting.
                self.switch.set_state(controller.enabled)
            else:
                self.switch.set_active(controller.enabled)
                self.switch.set_state(controller.enabled)
            self.switch.set_sensitive(not controller.pending)
            self.spinner.set_visible(controller.pending)
            if controller.pending:
                self.spinner.start()
            else:
                self.spinner.stop()
            self.status_label.set_text(controller.message)
            self.status_label.set_visible(bool(controller.message))
            if controller.error:
                self.status_label.add_css_class("error")
            else:
                self.status_label.remove_css_class("error")
            if controller.mode == MODE_PORTAL:
                self.switch.set_tooltip_text("Asks the system for permission (Background portal)")
            else:
                self.switch.set_tooltip_text("Adds or removes an entry in ~/.config/autostart")
        finally:
            self._syncing = False
