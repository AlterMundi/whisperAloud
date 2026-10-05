"""Preferences "Startup" group: the "Run in Background at Login" row (WHISP-87).

Self-contained so the Preferences dialog only adds ``StartupSection(...).widget``
(an Adw.PreferencesGroup) to a page. Like every preference it applies
immediately, but not through SetConfig: inside Flatpak it opens the system's
consent dialog through the Background portal, so the row's switch uses
Gtk.Switch's delayed-state pattern: the handle moves at once (``active``) but
the green ``state`` and the final position only follow the controller's
confirmed answer. (Adw.SwitchRow has no delayed state, hence an ActionRow
with a Gtk.Switch suffix.)
"""

import logging
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .startup_logic import MODE_PORTAL, AutostartController  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Run in Background at Login"
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

        group = Adw.PreferencesGroup()
        group.set_title("Startup")

        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [TITLE])

        self.row = Adw.ActionRow()
        self.row.set_title(TITLE)
        self.row.set_subtitle(HELP_TEXT)
        self.row.add_suffix(self.spinner)
        self.row.add_suffix(self.switch)
        self.row.set_activatable_widget(self.switch)
        group.add(self.row)

        self.status_label = Gtk.Label(wrap=True, xalign=0)
        self.status_label.set_selectable(True)
        self.status_label.set_margin_top(6)
        group.add(self.status_label)

        self.widget = group

        if controller is None:
            self.switch.set_sensitive(False)
            self.status_label.set_text(UNAVAILABLE_TEXT)
            return

        self.switch.connect("state-set", self._on_state_set)
        self._token = controller.subscribe(self._render)
        group.connect("destroy", lambda *_: self.detach())
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
