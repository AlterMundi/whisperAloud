"""Keyboard paste simulation for WhisperAloud."""

import grp
import logging
import os
import subprocess
import time
from typing import Any, Dict

from ..config import ClipboardConfig

logger = logging.getLogger(__name__)


class PasteSimulator:
    """Keyboard input simulation for paste operations."""

    def __init__(self, session_type: str, config: ClipboardConfig):
        """
        Initialize paste simulator.

        Args:
            session_type: Session type ('wayland', 'x11', or 'unknown')
            config: Clipboard configuration
        """
        self.session_type = session_type
        self.config = config
        logger.debug(f"PasteSimulator initialized for {session_type}")

    def _ydotool_keys(self) -> list:
        """Return ydotool key sequence for the configured paste shortcut."""
        if getattr(self.config, 'paste_shortcut', 'ctrl+v') == 'ctrl+shift+v':
            # Ctrl=29, Shift=42, V=47
            return ['29:1', '42:1', '47:1', '47:0', '42:0', '29:0']
        # Default: Ctrl+V
        return ['29:1', '47:1', '47:0', '29:0']

    def _xdotool_shortcut(self) -> str:
        """Return xdotool shortcut string for the configured paste shortcut."""
        shortcut = getattr(self.config, 'paste_shortcut', 'ctrl+v')
        if shortcut == 'ctrl+shift+v':
            return 'ctrl+shift+v'
        return 'ctrl+v'

    def simulate_paste(self) -> bool:
        """
        Simulate Ctrl+V keypress.

        Returns:
            True if successful, False otherwise
        """
        # Add delay before paste if configured
        if self.config.paste_delay_ms > 0:
            delay_sec = self.config.paste_delay_ms / 1000.0
            logger.debug(f"Waiting {delay_sec}s before paste")
            time.sleep(delay_sec)

        if self.session_type == 'wayland':
            return self._paste_wayland()
        elif self.session_type == 'x11':
            return self._paste_x11()
        else:
            logger.error("Cannot simulate paste: unknown session type")
            return False

    def _paste_wayland(self) -> bool:
        """
        Simulate paste using ydotool.

        Returns:
            True if successful, False otherwise
        """
        try:
            # ydotool keycodes: 29=Ctrl, 47=V
            # Format: keycode:1 (press), keycode:0 (release)
            subprocess.run(
                ['ydotool', 'key'] + self._ydotool_keys(),
                timeout=self.config.timeout_seconds,
                check=True,
                capture_output=True
            )
            logger.info("✓ Paste simulated via ydotool")
            return True
        except FileNotFoundError:
            logger.warning("ydotool not found. Install: sudo apt install ydotool")
            return False
        except PermissionError as e:
            logger.error(f"ydotool permission denied: {e}")
            logger.error("See setup instructions for ydotool permissions")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"ydotool timeout after {self.config.timeout_seconds}s")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"ydotool failed with exit code {e.returncode}")
            if e.stderr:
                logger.error(f"ydotool stderr: {e.stderr.decode('utf-8', errors='ignore')}")
            return False
        except Exception as e:
            logger.error(f"ydotool failed: {e}")
            return False

    def _paste_x11(self) -> bool:
        """
        Simulate paste using xdotool.

        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                ['xdotool', 'key', self._xdotool_shortcut()],
                timeout=self.config.timeout_seconds,
                check=True,
                capture_output=True
            )
            logger.info("✓ Paste simulated via xdotool")
            return True
        except FileNotFoundError:
            logger.warning("xdotool not found. Install: sudo apt install xdotool")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"xdotool timeout after {self.config.timeout_seconds}s")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"xdotool failed with exit code {e.returncode}")
            if e.stderr:
                logger.error(f"xdotool stderr: {e.stderr.decode('utf-8', errors='ignore')}")
            return False
        except Exception as e:
            logger.error(f"xdotool failed: {e}")
            return False

    def check_availability(self) -> Dict[str, Any]:
        """
        Check if paste simulation is available.

        Returns:
            Dictionary with keys:
            - available (bool): Whether paste is available
            - reason (str): Reason if not available (empty if available)
            - fix (str): Instructions to fix (empty if available)
        """
        tool = 'ydotool' if self.session_type == 'wayland' else 'xdotool'

        # Check if tool exists
        try:
            result = subprocess.run(
                ['which', tool],
                capture_output=True,
                timeout=2
            )
            if result.returncode != 0:
                return {
                    'available': False,
                    'reason': f'{tool} not installed',
                    'fix': f'Install with: sudo apt install {tool}'
                }
        except subprocess.TimeoutExpired:
            return {
                'available': False,
                'reason': f'Timeout checking for {tool}',
                'fix': 'Check system configuration'
            }
        except Exception as e:
            return {
                'available': False,
                'reason': f'Error checking {tool}: {e}',
                'fix': 'Check system configuration'
            }

        # For Wayland, check additional permissions
        if self.session_type == 'wayland':
            return self._check_ydotool_permissions()
        else:
            return {'available': True, 'reason': '', 'fix': ''}

    def _check_ydotool_permissions(self) -> Dict[str, Any]:
        """
        Check ydotool service and permissions (Wayland only).

        Returns:
            Dictionary with availability status
        """
        # Check if ydotool service is running
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'is-active', 'ydotool.service'],
                capture_output=True,
                timeout=2
            )
            if result.returncode != 0:
                # Try system service
                result = subprocess.run(
                    ['systemctl', 'is-active', 'ydotool.service'],
                    capture_output=True,
                    timeout=2
                )
                if result.returncode != 0:
                    return {
                        'available': False,
                        'reason': 'ydotool service not running',
                        'fix': 'Run: sudo systemctl enable --now ydotool.service'
                    }
        except subprocess.TimeoutExpired:
            logger.warning("Timeout checking ydotool service status")
        except FileNotFoundError:
            logger.warning("systemctl not found, cannot check ydotool service")
        except Exception as e:
            logger.warning(f"Error checking ydotool service: {e}")

        # Check input group membership
        try:
            user_groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
            if 'input' not in user_groups:
                return {
                    'available': False,
                    'reason': 'User not in input group',
                    'fix': 'Run: sudo usermod -aG input $USER (then logout/login)'
                }
        except Exception as e:
            logger.warning(f"Error checking group membership: {e}")

        return {'available': True, 'reason': '', 'fix': ''}
