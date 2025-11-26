"""Clipboard management for WhisperAloud."""

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Any

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
        if os.getenv('WAYLAND_DISPLAY'):
            return 'wayland'
        elif os.getenv('DISPLAY'):
            return 'x11'
        else:
            return 'unknown'

    def copy(self, text: str) -> bool:
        """
        Copy text to clipboard with automatic fallback.

        Args:
            text: Text to copy

        Returns:
            True if successful (including fallback), False otherwise
        """
        if not text:
            logger.warning("Empty text provided to clipboard copy")
            return False

        if self._session_type == 'wayland':
            return self._copy_wayland(text)
        elif self._session_type == 'x11':
            return self._copy_x11(text)
        else:
            logger.warning("Unknown session type, using fallback only")
            return self._copy_fallback(text)

    def _copy_wayland(self, text: str) -> bool:
        """
        Copy using wl-copy with guaranteed fallback.

        Args:
            text: Text to copy

        Returns:
            True if successful (clipboard or fallback)
        """
        try:
            # Run wl-copy as a background process (it needs to stay running to serve clipboard)
            # Use --paste-once so it exits after the first paste operation
            process = subprocess.Popen(
                ['wl-copy', '--paste-once'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            # Write the text and close stdin (non-blocking)
            try:
                process.stdin.write(text.encode('utf-8'))
                process.stdin.close()
            except (OSError, IOError) as e:
                logger.error(f"Failed to write to wl-copy stdin: {e}")
                logger.info("Using fallback file instead")
                return self._copy_fallback(text)
            # Don't wait for process to finish - it needs to stay running
            logger.info("✓ Copied to clipboard via wl-copy (background process)")
            # Still write fallback for redundancy
            self._copy_fallback(text)
            return True
        except FileNotFoundError:
            logger.warning("wl-copy not found. Install: sudo apt install wl-clipboard")
            logger.info("Using fallback file instead")
            return self._copy_fallback(text)
        except Exception as e:
            logger.error(f"wl-copy failed: {e}")
            logger.info("Using fallback file instead")
            return self._copy_fallback(text)

    def _copy_x11(self, text: str) -> bool:
        """
        Copy using xclip with fallback.

        Args:
            text: Text to copy

        Returns:
            True if successful (clipboard or fallback)
        """
        try:
            result = subprocess.run(
                ['xclip', '-selection', 'clipboard'],
                input=text.encode('utf-8'),
                timeout=self.config.timeout_seconds,
                check=True,
                capture_output=True
            )
            logger.info("✓ Copied to clipboard via xclip")
            # Write fallback for redundancy
            self._copy_fallback(text)
            return True
        except FileNotFoundError:
            logger.warning("xclip not found. Install: sudo apt install xclip")
            logger.info("Using fallback file instead")
            return self._copy_fallback(text)
        except subprocess.TimeoutExpired:
            logger.error(f"xclip timeout after {self.config.timeout_seconds}s")
            logger.info("Using fallback file instead")
            return self._copy_fallback(text)
        except subprocess.CalledProcessError as e:
            logger.error(f"xclip failed with exit code {e.returncode}")
            logger.info("Using fallback file instead")
            return self._copy_fallback(text)
        except Exception as e:
            logger.error(f"xclip failed: {e}")
            logger.info("Using fallback file instead")
            return self._copy_fallback(text)

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
        from .paste_simulator import PasteSimulator
        simulator = PasteSimulator(self._session_type, self.config)
        return simulator.check_availability()
