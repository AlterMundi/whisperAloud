"""Clipboard management for WhisperAloud."""

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..config import ClipboardConfig

logger = logging.getLogger(__name__)


class ClipboardManager:
    """Cross-platform clipboard operations with automatic fallback."""

    def __init__(self, config: ClipboardConfig):
        """
        Initialize clipboard manager.

        Args:
            config: Clipboard configuration
        """
        self.config = config
        self._session_type = self.detect_session_type()
        logger.info(f"Clipboard manager initialized for {self._session_type} session")

    @staticmethod
    def detect_session_type() -> str:
        """
        Detect the current display server session type.

        Returns:
            'wayland', 'x11', or 'unknown'
        """
        wayland_display = (os.getenv('WAYLAND_DISPLAY') or "").strip()
        x11_display = (os.getenv('DISPLAY') or "").strip()

        if wayland_display:
            return 'wayland'
        if x11_display:
            return 'x11'

        # Fallback for user services where DISPLAY/WAYLAND_DISPLAY is missing
        # but the desktop session type is still exported.
        session_type = (os.getenv('XDG_SESSION_TYPE') or "").strip().lower()
        if session_type in {'wayland', 'x11'}:
            return session_type

        runtime_dir = (os.getenv('XDG_RUNTIME_DIR') or "").strip()
        if runtime_dir:
            try:
                if any(Path(runtime_dir).glob("wayland-*")):
                    return 'wayland'
            except Exception:
                pass

        return 'unknown'

    def copy(
        self,
        text: str,
        on_complete: Optional[Callable[[bool], None]] = None,
    ) -> bool:
        """
        Copy text to clipboard with automatic fallback.

        Args:
            text: Text to copy
            on_complete: Optional callback invoked with the *real* outcome of
                the copy once the underlying clipboard tool has confirmed
                selection ownership (wl-copy/xclip process exit). Callers
                that must not act (e.g. trigger an auto-paste) until the
                selection is actually owned -- not merely dispatched -- MUST
                use this instead of trusting the synchronous return value.

                For Wayland, the callback fires from a background thread
                (the wait for wl-copy's exit happens off the calling
                thread/main loop); callers that need to resume on the GLib
                main loop must hop back themselves (e.g. via
                ``GLib.idle_add``) inside the callback.

        Returns:
            True if the copy was *dispatched* successfully (including
            fallback-only paths), False otherwise. This does NOT guarantee
            the real clipboard selection is owned yet -- use `on_complete`
            for that.
        """
        if not text:
            logger.warning("Empty text provided to clipboard copy")
            if on_complete is not None:
                on_complete(False)
            return False

        if self._session_type == 'wayland':
            return self._copy_wayland(text, on_complete)
        elif self._session_type == 'x11':
            return self._copy_x11(text, on_complete)
        else:
            logger.warning("Unknown session type, using fallback only")
            result = self._copy_fallback(text)
            if on_complete is not None:
                on_complete(result)
            return result

    def _notify_complete(
        self, on_complete: Optional[Callable[[bool], None]], success: bool
    ) -> None:
        """Invoke `on_complete(success)`, never letting it raise past us."""
        if on_complete is None:
            return
        try:
            on_complete(success)
        except Exception as e:
            logger.error(f"clipboard on_complete callback raised: {e}")

    def _copy_wayland(
        self, text: str, on_complete: Optional[Callable[[bool], None]] = None
    ) -> bool:
        """
        Copy using wl-copy, waiting for it to confirm selection ownership.

        Field bug fix: wl-copy (without --foreground) reads stdin, takes the
        Wayland selection, THEN forks to the background and the original
        process exits -- that exit is the event that means "selection set".
        Previously we reported success and triggered an auto-paste the
        instant stdin was closed, racing the fork; a manual Ctrl+V moments
        later would see the new text (fork had completed by then) but the
        automated paste usually won the race against it and pasted the
        *previous* clipboard owner's content.

        `--paste-once` is also removed: it made wl-copy serve exactly one
        paste and then exit, so a clipboard manager (or anything else that
        reads the selection first) would consume it, leaving later pastes
        with stale content. Normal wl-copy keeps serving the selection
        until another client takes it over, which is the expected desktop
        clipboard behavior.

        This wait never happens on the GLib main loop: when `on_complete`
        is given (the daemon's auto-paste path) the whole wait runs on a
        background thread and the real result is delivered via the
        callback; only a synchronous caller with no `on_complete` blocks
        the calling thread (matching the existing, already-blocking x11
        path).

        Returns:
            Dispatched-successfully flag when `on_complete` is given
            (always True once the thread is started -- the real result
            comes via the callback); otherwise the real wl-copy success
            (clipboard or fallback).
        """

        def _run() -> bool:
            try:
                # stdout=DEVNULL: the backgrounded (forked) wl-copy child
                # inherits no pipe, so nothing can block on an undrained
                # buffer; stderr is only read from the parent below, after
                # it has exited, for diagnostics.
                process = subprocess.Popen(
                    ['wl-copy'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError:
                logger.warning("wl-copy not found. Install: sudo apt install wl-clipboard")
                logger.info("Using fallback file instead")
                fallback_ok = self._copy_fallback(text)
                self._notify_complete(on_complete, False)
                return fallback_ok
            except Exception as e:
                logger.error(f"wl-copy failed: {e}")
                logger.info("Using fallback file instead")
                fallback_ok = self._copy_fallback(text)
                self._notify_complete(on_complete, False)
                return fallback_ok

            try:
                process.stdin.write(text.encode('utf-8'))
                process.stdin.close()
            except (OSError, IOError) as e:
                logger.error(f"Failed to write to wl-copy stdin: {e}")
                logger.info("Using fallback file instead")
                try:
                    process.kill()
                except Exception:
                    pass
                fallback_ok = self._copy_fallback(text)
                self._notify_complete(on_complete, False)
                return fallback_ok

            # The event that actually means "selection set": the wl-copy
            # parent process exiting (it forks into the background only
            # after it owns the selection). A bounded wait is a logged
            # degradation to clipboard-only (no paste) on timeout -- never
            # a green light to paste.
            try:
                returncode = process.wait(timeout=self.config.timeout_seconds)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"wl-copy did not exit within {self.config.timeout_seconds}s "
                    "(selection ownership unconfirmed); degrading to "
                    "clipboard-only, no auto-paste"
                )
                try:
                    process.kill()
                except Exception:
                    pass
                fallback_ok = self._copy_fallback(text)
                self._notify_complete(on_complete, False)
                return fallback_ok

            if returncode == 0:
                logger.info("✓ Copied to clipboard via wl-copy")
                self._copy_fallback(text)
                self._notify_complete(on_complete, True)
                return True

            stderr = b""
            try:
                if process.stderr:
                    stderr = process.stderr.read()
            except Exception:
                pass
            logger.warning(f"wl-copy exited with code {returncode}: {stderr!r}")
            fallback_ok = self._copy_fallback(text)
            self._notify_complete(on_complete, False)
            return fallback_ok

        if on_complete is not None:
            threading.Thread(target=_run, daemon=True, name="wl-copy-wait").start()
            return True  # dispatched; real result arrives via on_complete
        return _run()

    def _copy_x11(
        self, text: str, on_complete: Optional[Callable[[bool], None]] = None
    ) -> bool:
        """
        Copy using xclip with fallback.

        xclip also forks into the background after it owns the selection,
        but `subprocess.run()` already waits for the foreground process to
        exit -- which happens right after that fork -- so ownership is
        already confirmed by the time this returns. No extra waiting is
        needed here, unlike the wl-copy path above.

        Args:
            text: Text to copy

        Returns:
            True if successful (clipboard or fallback)
        """
        try:
            subprocess.run(
                ['xclip', '-selection', 'clipboard'],
                input=text.encode('utf-8'),
                timeout=self.config.timeout_seconds,
                check=True,
                capture_output=True
            )
            logger.info("✓ Copied to clipboard via xclip")
            # Write fallback for redundancy
            self._copy_fallback(text)
            self._notify_complete(on_complete, True)
            return True
        except FileNotFoundError:
            logger.warning("xclip not found. Install: sudo apt install xclip")
            logger.info("Using fallback file instead")
            result = self._copy_fallback(text)
            self._notify_complete(on_complete, False)
            return result
        except subprocess.TimeoutExpired:
            logger.error(f"xclip timeout after {self.config.timeout_seconds}s")
            logger.info("Using fallback file instead")
            result = self._copy_fallback(text)
            self._notify_complete(on_complete, False)
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"xclip failed with exit code {e.returncode}")
            logger.info("Using fallback file instead")
            result = self._copy_fallback(text)
            self._notify_complete(on_complete, False)
            return result
        except Exception as e:
            logger.error(f"xclip failed: {e}")
            logger.info("Using fallback file instead")
            result = self._copy_fallback(text)
            self._notify_complete(on_complete, False)
            return result

    def _copy_fallback(self, text: str) -> bool:
        """
        Fallback: write to temp file (ALWAYS succeeds).

        Args:
            text: Text to write

        Returns:
            True if successful, False only in catastrophic failure
        """
        try:
            fallback_path = Path(self.config.fallback_path)
            fallback_path.write_text(text, encoding='utf-8')
            logger.info(f"💾 Text saved to {fallback_path}")
            return True
        except Exception as e:
            logger.critical(f"CRITICAL: Fallback copy failed: {e}")
            # Last resort: try /tmp with generic name
            try:
                emergency_path = Path("/tmp/whisper_clipboard.txt")
                emergency_path.write_text(text, encoding='utf-8')
                logger.warning(f"Emergency backup to {emergency_path}")
                return True
            except Exception as e2:
                logger.critical(f"EMERGENCY FALLBACK FAILED: {e2}")
                return False

    def check_paste_permissions(self) -> Dict[str, Any]:
        """
        Check if paste simulation is available.

        Returns:
            Dictionary with keys:
            - available (bool): Whether paste simulation is available
            - reason (str): Reason if not available
            - fix (str): Instructions to enable paste
        """
        from .paste_selection import build_paste_simulator
        simulator = build_paste_simulator(self._session_type, self.config)
        return simulator.check_availability()


class TestClipboardStub:
    """Test-only replacement for ClipboardManager under WHISPER_ALOUD_TEST_MODE=1.

    Incident (WHISP-46): a real e2e run copied text via a real ClipboardManager
    and overwrote the operator's real Wayland clipboard, because unsetting
    WAYLAND_DISPLAY alone does not stop detect_session_type()'s XDG_RUNTIME_DIR
    fallback scan from finding a real wayland-* socket, nor stop wl-copy itself
    from connecting to the real compositor. This stub never imports or calls
    `subprocess` at all -- it only appends the copied text to an in-memory list
    and, if $WHISPER_ALOUD_TEST_CLIPBOARD_FILE is set, to that file, so e2e
    tests can assert on copy behavior without ever touching a real clipboard,
    wl-copy, xclip, xsel, xdotool, ydotool, or gio.
    """

    def __init__(self, config: ClipboardConfig):
        self.config = config
        self._session_type = "test"
        self.copied: list[str] = []

    def copy(
        self,
        text: str,
        on_complete: Optional[Callable[[bool], None]] = None,
    ) -> bool:
        if not text:
            logger.warning("Empty text provided to clipboard copy")
            if on_complete is not None:
                on_complete(False)
            return False
        self.copied.append(text)
        target = os.environ.get("WHISPER_ALOUD_TEST_CLIPBOARD_FILE")
        if target:
            try:
                with open(target, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception as e:
                logger.warning(f"TestClipboardStub: failed to write {target}: {e}")
        # No real subprocess involved: the "copy" is already complete by
        # the time we get here, so the completion callback (if any) fires
        # synchronously/immediately -- never from a background thread.
        if on_complete is not None:
            on_complete(True)
        return True

    def check_paste_permissions(self) -> Dict[str, Any]:
        return {
            "available": False,
            "reason": "WHISPER_ALOUD_TEST_MODE=1: paste simulation is never available",
            "fix": "",
        }


def build_clipboard_manager(config: ClipboardConfig):
    """Factory: returns a TestClipboardStub under WHISPER_ALOUD_TEST_MODE=1,
    a real ClipboardManager otherwise. The daemon must always go through this
    rather than constructing ClipboardManager directly (WHISP-46)."""
    if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
        return TestClipboardStub(config)
    return ClipboardManager(config)
