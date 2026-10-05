"""Preferences group: "Top Bar Indicator" (GNOME Shell extension).

Optional component. Shows whether the whisperaloud@fede extension is
installed/enabled/up to date, the host command to install it, and (native
installs only) an Enable/Disable button. All decisions live in
whisper_aloud.shell_extension; this module is GTK + Gio glue.

D-Bus queries live in ui/shell_extension_probe.py (Gio only).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Mapping, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from .. import shell_extension as se  # noqa: E402
from ..sandbox import is_flatpak_sandbox  # noqa: E402
from .shell_extension_probe import ShellExtensionProbe  # noqa: E402

logger = logging.getLogger(__name__)

SECTION_TITLE = "Top Bar Indicator"
SECTION_DESCRIPTION = (
    "Optional. A WhisperAloud icon in the GNOME top bar shows at a glance whether "
    "the service is running, the speech model is ready and the global hotkey is "
    "registered, and lets you start/stop dictation without opening this window. "
    "While it is active, the tray icon is hidden to avoid a duplicate."
)
LIMITS_NOTE = (
    "GNOME extensions cannot be installed from inside an app: run the command in a "
    "terminal on your computer. The extension is not yet published on "
    "extensions.gnome.org."
)


def _copy_to_clipboard(text: str) -> None:
    display = Gdk.Display.get_default()
    if display is not None:
        display.get_clipboard().set(text)


class ShellExtensionSection:
    """The section's widgets. ``widget`` is an Adw.PreferencesGroup to add to a
    preferences page (rows first, then the command boxes and notes)."""

    def __init__(
        self,
        *,
        probe=None,
        sandboxed: Optional[bool] = None,
        env: Optional[Mapping[str, str]] = None,
        data_home: Optional[Path] = None,
        bundled_zip: Optional[Path] = None,
        copy: Callable[[str], None] = _copy_to_clipboard,
    ):
        env = os.environ if env is None else env
        self._sandboxed = is_flatpak_sandbox() if sandboxed is None else sandboxed
        self._is_gnome = se.is_gnome_desktop(env)
        self._data_home = Path(
            data_home
            or env.get("XDG_DATA_HOME")
            or Path.home() / ".local" / "share"
        )
        self._bundled_zip = (
            bundled_zip if bundled_zip is not None
            else se.find_bundled_zip(sandboxed=self._sandboxed)
        )
        self._bundled_version = (
            se.read_zip_version(self._bundled_zip) if self._bundled_zip else None
        )
        self._copy = copy
        self._probe = probe if probe is not None else ShellExtensionProbe(sandboxed=self._sandboxed)
        self._report: Optional[se.ExtensionReport] = None
        self.view: Optional[se.SectionView] = None

        self._build()
        self._unwatch = self._probe.watch(self.refresh) if self._is_gnome else (lambda: None)
        self.widget.connect("destroy", self._on_destroy)
        self.refresh()

    # ── widgets ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        group = Adw.PreferencesGroup()
        group.set_title(SECTION_TITLE)
        group.set_description(SECTION_DESCRIPTION)
        self.widget = group

        self.status_label = Gtk.Label(label="Checking…")
        self.status_label.set_halign(Gtk.Align.END)
        self.status_label.add_css_class("dim-label")
        self.toggle_button = Gtk.Button(label="Enable")
        self.toggle_button.set_valign(Gtk.Align.CENTER)
        self.toggle_button.set_visible(False)
        self.toggle_button.connect("clicked", self._on_toggle_clicked)
        self.refresh_button = Gtk.Button(label="Refresh")
        self.refresh_button.set_valign(Gtk.Align.CENTER)
        self.refresh_button.connect("clicked", lambda _b: self.refresh())
        self.status_row = Adw.ActionRow()
        self.status_row.set_title("Status")
        self.status_row.add_suffix(self.status_label)
        self.status_row.add_suffix(self.toggle_button)
        self.status_row.add_suffix(self.refresh_button)
        group.add(self.status_row)
        box = group

        self.summary_label = self._wrapped_label("", dim=True)
        self.summary_label.set_margin_top(6)
        box.add(self.summary_label)

        self.install_box, self.install_label = self._command_box(
            "Install or update (then log out and back in):")
        self.enable_box, self.enable_label = self._command_box("Enable:")
        self.disable_box, self.disable_label = self._command_box("Disable:")
        for b in (self.install_box, self.enable_box, self.disable_box):
            b.set_margin_top(6)
            box.add(b)

        self.package_missing_label = self._wrapped_label(
            "The extension package is not part of this installation. From a source "
            "checkout, build it with scripts/pack_shell_extension.py OUT.zip, or use "
            "install.sh.", dim=True)
        self.package_missing_label.set_visible(False)
        box.add(self.package_missing_label)

        self.limits_label = self._wrapped_label(LIMITS_NOTE, dim=True)
        self.limits_label.set_margin_top(6)
        box.add(self.limits_label)

    @staticmethod
    def _wrapped_label(text: str, dim: bool = False) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.set_xalign(0.0)
        label.set_wrap(True)
        if dim:
            label.add_css_class("dim-label")
        return label

    def _command_box(self, caption: str):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self._wrapped_label(caption, dim=True))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cmd = Gtk.Label(label="")
        cmd.set_selectable(True)
        cmd.set_wrap(True)
        cmd.set_xalign(0.0)
        cmd.set_hexpand(True)
        cmd.add_css_class("monospace")
        copy_btn = Gtk.Button(label="Copy")
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.connect("clicked", lambda _b: self._copy(cmd.get_text()))
        row.append(cmd)
        row.append(copy_btn)
        box.append(row)
        box.set_visible(False)
        return box, cmd

    # ── behaviour ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        if not self._is_gnome:
            self._apply(None, None)
            return
        self.status_label.set_text("Checking…")
        self._probe.query(self._apply)

    def _apply(self, shell_info, running_info) -> None:
        report = se.classify(
            is_gnome=self._is_gnome,
            sandboxed=self._sandboxed,
            shell_info=shell_info,
            running_info=running_info,
            bundled_version=self._bundled_version,
        )
        install_zip = None
        if report.show_install_command:
            # Exports the zip to a host-visible path only when it is needed.
            install_zip = se.resolve_install_zip(
                sandboxed=self._sandboxed, data_home=self._data_home, bundled=self._bundled_zip)
        view = se.section_view(report, install_zip)
        self._report, self.view = report, view

        self.status_label.set_text(view.title)
        self.summary_label.set_text(view.summary)
        for box, label, text in (
            (self.install_box, self.install_label, view.install_command),
            (self.enable_box, self.enable_label, view.enable_command),
            (self.disable_box, self.disable_label, view.disable_command),
        ):
            label.set_text(text or "")
            box.set_visible(bool(text))
        self.package_missing_label.set_visible(view.package_missing)
        self.toggle_button.set_visible(view.toggle_label is not None)
        self.toggle_button.set_sensitive(True)
        if view.toggle_label:
            self.toggle_button.set_label(view.toggle_label)
        self.refresh_button.set_visible(self._is_gnome)

    def _on_toggle_clicked(self, _button) -> None:
        if self._report is None or self._report.toggle_action is None:
            return
        self.toggle_button.set_sensitive(False)
        self._probe.set_enabled(self._report.toggle_action == "enable",
                                lambda _ok: self.refresh())

    def detach(self) -> None:
        """Stop watching the extension (the Preferences dialog was closed)."""
        self._on_destroy()

    def _on_destroy(self, *_args) -> None:
        self._unwatch()
        self._unwatch = lambda: None
        close = getattr(self._probe, "close", None)
        if close is not None:
            close()
