"""Confirmation and generic dialog helpers (WHISP-78, WHISP-80).

Adw.MessageDialog was deprecated in libadwaita 1.5 in favor of Adw.AlertDialog.
Debian/Ubuntu LTS can still ship an older libadwaita where AlertDialog does not
exist, so the choice has to be made at runtime rather than at import time.

``confirm()`` is the entry point for a two-response (Cancel / accept)
confirmation -- it picks AlertDialog when available and transparently falls
back to MessageDialog otherwise, keeping the calling code identical either
way.

``show_dialog()`` generalizes the same runtime selection to an arbitrary set
of responses (e.g. a single "OK", or a variable "recovery action + Close"
set) and an optional extra child widget (e.g. a details expander), so the
rest of the UI never has to construct Gtk.MessageDialog directly.
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

logger = logging.getLogger(__name__)

# Adw.AlertDialog was introduced in libadwaita 1.5.
_ALERT_DIALOG_MIN_ADW_MINOR = 5


def _select_dialog_class(adw_minor_version: int):
    """Pure selection logic, kept separate from Adw.get_minor_version() so it is
    trivially unit-testable with a plain integer."""
    if adw_minor_version >= _ALERT_DIALOG_MIN_ADW_MINOR:
        return Adw.AlertDialog
    return Adw.MessageDialog


def _dialog_class():
    """The dialog class to use on this system, read from the installed
    libadwaita's runtime minor version."""
    return _select_dialog_class(Adw.get_minor_version())


def confirm(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    accept_label: str,
    on_accept: Callable[[], None],
    *,
    destructive: bool = False,
    cancel_label: str = "Cancel",
    body_use_markup: bool = False,
    on_response: Optional[Callable[[str], None]] = None,
):
    """Show a two-response (cancel/accept) confirmation dialog.

    Uses Adw.AlertDialog on libadwaita >= 1.5, and falls back to the
    deprecated Adw.MessageDialog on older libadwaita so the app keeps working
    on distros that haven't updated yet (e.g. Debian/Ubuntu LTS).

    Only the accept response ever triggers ``on_accept`` -- Cancel, Escape and
    closing the dialog are all no-ops as far as the caller's action is
    concerned. ``on_response`` is optional and, when given, runs for *every*
    response (accept or cancel) before ``on_accept`` -- useful for
    bookkeeping (e.g. clearing an "is a child dialog open" flag) that must
    happen regardless of which button was chosen.

    Returns the constructed dialog (useful for tests).
    """
    dialog_cls = _dialog_class()

    kwargs = {"heading": heading, "body": body, "body_use_markup": body_use_markup}
    if dialog_cls is Adw.MessageDialog:
        # Adw.MessageDialog (unlike Adw.Dialog subclasses) takes its parent at
        # construction time rather than via present(parent).
        kwargs["transient_for"] = parent
        kwargs["modal"] = True

    dialog = dialog_cls(**kwargs)
    dialog.add_response("cancel", cancel_label)
    dialog.add_response("accept", accept_label)
    dialog.set_response_appearance(
        "accept",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def _on_response(_dialog: object, response: str) -> None:
        if on_response is not None:
            on_response(response)
        if response == "accept":
            on_accept()

    dialog.connect("response", _on_response)

    if dialog_cls is Adw.AlertDialog:
        dialog.present(parent)
    else:
        dialog.present()

    return dialog


def show_dialog(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    responses: List[Tuple[str, str, "Adw.ResponseAppearance"]],
    default_response: str,
    close_response: str,
    on_response: Callable[[str], None],
    *,
    extra_child: Optional[Gtk.Widget] = None,
    body_use_markup: bool = False,
):
    """Show a dialog with an arbitrary set of responses (WHISP-80).

    Generalizes ``confirm()`` -- same Adw.AlertDialog / Adw.MessageDialog
    runtime selection -- to any number of responses, so call sites that used
    to build a raw ``Gtk.MessageDialog`` (a single "OK", or a variable
    "recovery action + Close" set) can migrate without hand-rolling the
    version-selection dance themselves.

    ``responses`` is a list of ``(response_id, label, appearance)`` tuples,
    added to the dialog in the given order. ``on_response`` is called with
    the response id for *every* response, including ``close_response`` (the
    one used for Escape/window-close) -- unlike ``confirm()``, there is no
    built-in notion of "the accepting response", since an arbitrary dialog
    may have more than two responses.

    ``extra_child``, when given, is attached via ``set_extra_child`` --
    both Adw.AlertDialog and Adw.MessageDialog support it directly, so no
    fallback branching is needed for it (unlike the constructor kwargs
    above).

    Returns the constructed dialog (useful for tests).
    """
    dialog_cls = _dialog_class()

    kwargs = {"heading": heading, "body": body, "body_use_markup": body_use_markup}
    if dialog_cls is Adw.MessageDialog:
        # See confirm(): Adw.MessageDialog takes its parent at construction
        # time rather than via present(parent).
        kwargs["transient_for"] = parent
        kwargs["modal"] = True

    dialog = dialog_cls(**kwargs)
    for response_id, label, appearance in responses:
        dialog.add_response(response_id, label)
        dialog.set_response_appearance(response_id, appearance)
    dialog.set_default_response(default_response)
    dialog.set_close_response(close_response)

    if extra_child is not None:
        dialog.set_extra_child(extra_child)

    def _on_response(_dialog: object, response: str) -> None:
        on_response(response)

    dialog.connect("response", _on_response)

    if dialog_cls is Adw.AlertDialog:
        dialog.present(parent)
    else:
        dialog.present()

    return dialog
