"""Controller for "Start in background at login" (WHISP-87), GTK-free.

One instance lives for the whole GUI process (owned by the main window), not
by the settings dialog: inside Flatpak the Background portal's consent
dialog can be answered after the settings dialog was closed, and that answer
must still be recorded. Views ``subscribe()`` to state changes and
``unsubscribe()`` when they go away.

- Flatpak (``sandboxed=True``): ``set_enabled()`` issues RequestBackground
  and returns at once; the state only changes when the portal answers.
  While a request is pending a second one is refused (the switch is
  insensitive), and an answer to a superseded request is ignored.
- Native: ``set_enabled()`` writes or removes the XDG autostart entry
  synchronously. The file on disk is the truth; the config is reconciled to
  it at construction.

``persist(enabled)`` stores the confirmed state in the config (the GUI
routes it through the daemon's SetConfig, so GetStatus "autostart" follows).
"""

import logging
from pathlib import Path
from typing import Callable, Optional

from .. import autostart as native_autostart

logger = logging.getLogger(__name__)

PORTAL_REASON = (
    "Start WhisperAloud in the background at login, without opening its window, "
    "so the global shortcut can dictate right away."
)

MODE_PORTAL = "portal"
MODE_NATIVE = "native"


class AutostartController:
    def __init__(
        self,
        *,
        sandboxed: bool,
        initial: bool,
        persist: Callable[[bool], None],
        portal_factory: Optional[Callable[[], object]] = None,
        native_dir: Optional[Path] = None,
        native_command: Optional[str] = None,
    ) -> None:
        self.mode = MODE_PORTAL if sandboxed else MODE_NATIVE
        self.enabled = bool(initial)
        self.pending = False
        self.message = ""
        self.error = False
        self._persist = persist
        self._portal_factory = portal_factory or _default_portal_factory
        self._portal = None
        self._native_dir = native_dir
        self._native_command = native_command
        self._seq = 0
        self._listeners: dict = {}
        self._next_token = 1

        if self.mode == MODE_NATIVE:
            self._reconcile_native()

    # ─── listeners ─────────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[["AutostartController"], None]) -> int:
        token = self._next_token
        self._next_token += 1
        self._listeners[token] = callback
        return token

    def unsubscribe(self, token: int) -> None:
        self._listeners.pop(token, None)

    def _notify(self) -> None:
        for callback in list(self._listeners.values()):
            try:
                callback(self)
            except Exception:
                logger.exception("Autostart listener failed")

    # ─── public API ────────────────────────────────────────────────────────

    def set_enabled(self, want: bool) -> bool:
        """Request a change. Returns False if refused (pending or no change)."""
        want = bool(want)
        if self.pending:
            logger.info("Autostart change refused: a portal request is still pending")
            return False
        if want == self.enabled:
            return False
        if self.mode == MODE_PORTAL:
            self._request_portal(want)
        else:
            self._apply_native(want)
        return True

    def close(self) -> None:
        if self._portal is not None:
            try:
                self._portal.close()
            except Exception:
                logger.debug("Background portal close failed", exc_info=True)

    # ─── Flatpak: Background portal ────────────────────────────────────────

    def _request_portal(self, want: bool) -> None:
        self._seq += 1
        seq = self._seq
        try:
            if self._portal is None:
                self._portal = self._portal_factory()
            self.pending = True
            self.error = False
            self.message = "Waiting for your answer in the system dialog…"
            self._notify()
            self._portal.request(
                want,
                PORTAL_REASON,
                lambda result: self._on_portal_result(seq, want, result),
                previous=self.enabled,
            )
        except Exception as e:
            logger.warning("Could not ask the Background portal: %s", e)
            self.pending = False
            self._set_message(f"Could not ask the system for permission: {e}", error=True)
            self._notify()

    def _on_portal_result(self, seq: int, want: bool, result) -> None:
        if seq != self._seq:
            logger.info("Ignoring answer to a superseded RequestBackground (#%d)", seq)
            return
        self.pending = False
        self.enabled = bool(result.autostart)
        outcome = result.outcome
        if outcome == "applied":
            text = (
                "WhisperAloud will start in the background at login."
                if self.enabled
                else "WhisperAloud will no longer start at login."
            )
            self._set_message(text, error=False)
        elif outcome == "denied":
            self._set_message(
                "Running in the background was not allowed. You can allow it in "
                "Settings → Apps → WhisperAloud.",
                error=True,
            )
        elif outcome == "cancelled":
            self._set_message("Cancelled; nothing was changed.", error=False)
        else:
            detail = f" ({result.detail})" if getattr(result, "detail", "") else ""
            self._set_message(f"The system could not change the login setting{detail}.", error=True)
        self._store(self.enabled)
        self._notify()

    # ─── Native: XDG autostart entry ───────────────────────────────────────

    def _dir(self) -> Path:
        return self._native_dir or native_autostart.autostart_dir()

    def _reconcile_native(self) -> None:
        state = native_autostart.read_state(self._dir())
        if state.enabled != self.enabled:
            self.enabled = state.enabled
            self._store(self.enabled)
        self._report_others(state.others)

    def _apply_native(self, want: bool) -> None:
        try:
            if want:
                state = native_autostart.enable(self._dir(), command=self._native_command)
            else:
                state = native_autostart.disable(self._dir())
        except (OSError, ValueError) as e:
            logger.warning("Could not change the autostart entry: %s", e)
            self._set_message(f"Could not change the autostart entry: {e}", error=True)
            self._notify()
            return
        self.enabled = state.enabled
        self._set_message(
            "WhisperAloud will start in the background at login."
            if self.enabled
            else "WhisperAloud will no longer start at login.",
            error=False,
        )
        self._store(self.enabled)
        self._report_others(state.others)
        self._notify()

    def _report_others(self, others) -> None:
        if not others:
            return
        names = ", ".join(str(p) for p in others)
        if self.enabled:
            self._set_message(
                f"{self.message} Another login entry also starts WhisperAloud: {names}. "
                "It is harmless, but you can remove it.".strip(),
                error=self.error,
            )
        else:
            self._set_message(
                f"Another login entry still starts WhisperAloud: {names}. "
                "Remove it to stop starting at login.",
                error=True,
            )

    # ─── helpers ───────────────────────────────────────────────────────────

    def _set_message(self, text: str, *, error: bool) -> None:
        self.message = text
        self.error = error

    def _store(self, enabled: bool) -> None:
        try:
            self._persist(enabled)
        except Exception as e:
            logger.warning("Could not save startup.autostart_background: %s", e)
            self._set_message(f"{self.message} (Could not save the setting: {e})".strip(), error=True)


def _default_portal_factory():
    from ..background_portal import BackgroundPortal

    return BackgroundPortal()
