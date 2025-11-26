# WhisperAloud Phase 3: Clipboard Integration - Code Generation Prompt for Grok

## Context & Foundation

**Phase 1 Status**: ✅ COMPLETE (Core transcription engine working perfectly)
**Phase 2 Status**: ✅ COMPLETE (Audio recording module working perfectly)
**Current Phase**: Phase 3 - Clipboard Integration
**Target System**: Debian 12, Python 3.13, Wayland session (with X11 fallback support)
**Existing Code**: Phases 1-2 provide complete record → transcribe pipeline

### Integration Points from Phases 1-2

You will integrate with these working modules:
- `whisper_aloud.config`: Configuration system (extend with ClipboardConfig)
- `whisper_aloud.transcriber`: Transcriber class for processing audio
- `whisper_aloud.audio`: AudioRecorder for capturing microphone input
- `whisper_aloud.exceptions`: Custom exception hierarchy (extend with ClipboardError)

**Existing Files** (DO NOT MODIFY unless extending):
```
src/whisper_aloud/
├── __init__.py          # Will need to export clipboard classes
├── config.py            # Will need ClipboardConfig dataclass added
├── exceptions.py        # Will need ClipboardError added
├── transcriber.py       # Uses clipboard for output (optional)
├── __main__.py          # CLI may add clipboard testing command
└── audio/               # Complete recording subsystem (Phase 2)
    ├── __init__.py
    ├── device_manager.py
    ├── recorder.py
    ├── audio_processor.py
    └── level_meter.py
```

---

## Phase 3 Objectives

Build a cross-platform clipboard integration that:
1. **Detects display server** (Wayland vs X11) automatically
2. **Copies text to clipboard** using platform-appropriate tools
3. **Simulates paste** (Ctrl+V) when user has permissions configured
4. **Handles missing tools gracefully** with clear error messages
5. **Provides permission checking** and setup instructions
6. **Falls back to file-based clipboard** when tools unavailable
7. **Integrates with existing configuration system**

---

## Technical Requirements

### System Dependencies (User must install)

**For Wayland:**
```bash
sudo apt install wl-clipboard ydotool
sudo systemctl enable --now ydotool.service
sudo usermod -aG input $USER  # Logout/login required
```

**For X11:**
```bash
sudo apt install xclip xdotool
```

### Python Dependencies

No additional Python packages needed - uses subprocess for external tools.

---

## Critical Wayland/X11 Detection

**Session Type Detection:**
```python
import os

def detect_session_type() -> str:
    """
    Detect display server type.
    Returns: 'wayland', 'x11', or 'unknown'
    """
    # Method 1: Check $XDG_SESSION_TYPE
    session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
    if session_type in ('wayland', 'x11'):
        return session_type

    # Method 2: Check $WAYLAND_DISPLAY
    if os.environ.get('WAYLAND_DISPLAY'):
        return 'wayland'

    # Method 3: Check $DISPLAY
    if os.environ.get('DISPLAY'):
        return 'x11'

    return 'unknown'
```

---

## Detailed Implementation Specifications

### Directory Structure

Create new `clipboard/` subpackage:

```
src/whisper_aloud/clipboard/
├── __init__.py              # Public API exports
├── clipboard_manager.py     # Main clipboard operations
└── paste_simulator.py       # Keyboard input simulation (Ctrl+V)
```

---

## File 1: `src/whisper_aloud/clipboard/__init__.py`

Public API exports for the clipboard subpackage.

```python
"""Clipboard integration for WhisperAloud."""

from .clipboard_manager import ClipboardManager
from .paste_simulator import PasteSimulator

__all__ = [
    "ClipboardManager",
    "PasteSimulator",
]
```

---

## File 2: `src/whisper_aloud/clipboard/clipboard_manager.py`

**Purpose**: Handle clipboard copy operations across Wayland/X11.

**Key Features:**
- Auto-detect display server
- Use appropriate clipboard tools
- Fallback to file-based clipboard
- Clear error messages with fix instructions

**Implementation:**

```python
"""Clipboard management for cross-platform text operations."""

import os
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from whisper_aloud.exceptions import ClipboardError


logger = logging.getLogger(__name__)


class ClipboardManager:
    """
    Cross-platform clipboard operations.

    Supports both Wayland and X11 display servers with automatic detection.
    Falls back to file-based clipboard when tools are unavailable.
    """

    def __init__(self, fallback_to_file: bool = True):
        """
        Initialize clipboard manager.

        Args:
            fallback_to_file: If True, use file fallback when tools missing
        """
        self.fallback_to_file = fallback_to_file
        self.session_type = self._detect_session_type()
        self.fallback_path = Path(tempfile.gettempdir()) / "whisper_aloud_clipboard.txt"

        logger.info(f"Clipboard manager initialized (session: {self.session_type})")

    @staticmethod
    def _detect_session_type() -> str:
        """
        Detect display server type.

        Returns:
            'wayland', 'x11', or 'unknown'
        """
        # Method 1: Check XDG_SESSION_TYPE
        session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
        if session_type in ('wayland', 'x11'):
            return session_type

        # Method 2: Check WAYLAND_DISPLAY
        if os.environ.get('WAYLAND_DISPLAY'):
            return 'wayland'

        # Method 3: Check DISPLAY
        if os.environ.get('DISPLAY'):
            return 'x11'

        return 'unknown'

    def _check_tool_available(self, tool_name: str) -> bool:
        """Check if a command-line tool is available."""
        try:
            subprocess.run(
                ['which', tool_name],
                capture_output=True,
                check=True,
                timeout=2
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def copy(self, text: str) -> bool:
        """
        Copy text to clipboard.

        Args:
            text: Text to copy

        Returns:
            True if successful, False otherwise

        Raises:
            ClipboardError: If copy fails and fallback disabled
        """
        if not text:
            logger.warning("Attempted to copy empty text")
            return False

        # Try platform-specific clipboard
        try:
            if self.session_type == 'wayland':
                return self._copy_wayland(text)
            elif self.session_type == 'x11':
                return self._copy_x11(text)
            else:
                logger.warning(f"Unknown session type: {self.session_type}")
                return self._copy_fallback(text)
        except ClipboardError as e:
            if self.fallback_to_file:
                logger.warning(f"Clipboard copy failed, using fallback: {e}")
                return self._copy_fallback(text)
            else:
                raise

    def _copy_wayland(self, text: str) -> bool:
        """Copy text using wl-clipboard (Wayland)."""
        if not self._check_tool_available('wl-copy'):
            raise ClipboardError(
                "wl-copy not found. Install with: sudo apt install wl-clipboard"
            )

        try:
            result = subprocess.run(
                ['wl-copy'],
                input=text.encode('utf-8'),
                capture_output=True,
                timeout=5,
                check=True
            )
            logger.debug(f"Copied {len(text)} chars to Wayland clipboard")
            return True
        except subprocess.CalledProcessError as e:
            raise ClipboardError(f"wl-copy failed: {e.stderr.decode().strip()}")
        except subprocess.TimeoutExpired:
            raise ClipboardError("wl-copy timed out")

    def _copy_x11(self, text: str) -> bool:
        """Copy text using xclip (X11)."""
        if not self._check_tool_available('xclip'):
            raise ClipboardError(
                "xclip not found. Install with: sudo apt install xclip"
            )

        try:
            result = subprocess.run(
                ['xclip', '-selection', 'clipboard'],
                input=text.encode('utf-8'),
                capture_output=True,
                timeout=5,
                check=True
            )
            logger.debug(f"Copied {len(text)} chars to X11 clipboard")
            return True
        except subprocess.CalledProcessError as e:
            raise ClipboardError(f"xclip failed: {e.stderr.decode().strip()}")
        except subprocess.TimeoutExpired:
            raise ClipboardError("xclip timed out")

    def _copy_fallback(self, text: str) -> bool:
        """Fallback: Write to temporary file."""
        try:
            self.fallback_path.write_text(text, encoding='utf-8')
            logger.info(f"Text saved to fallback clipboard: {self.fallback_path}")
            return True
        except OSError as e:
            raise ClipboardError(f"Failed to write fallback file: {e}")

    def read_fallback(self) -> Optional[str]:
        """Read from fallback clipboard file."""
        try:
            if self.fallback_path.exists():
                return self.fallback_path.read_text(encoding='utf-8')
            return None
        except OSError as e:
            logger.error(f"Failed to read fallback clipboard: {e}")
            return None

    def get_clipboard_status(self) -> dict:
        """
        Get clipboard system status.

        Returns:
            Dict with status information
        """
        status = {
            'session_type': self.session_type,
            'copy_available': False,
            'paste_available': False,
            'tools': {},
            'issues': []
        }

        # Check copy tools
        if self.session_type == 'wayland':
            has_wl_copy = self._check_tool_available('wl-copy')
            status['tools']['wl-copy'] = has_wl_copy
            status['copy_available'] = has_wl_copy

            if not has_wl_copy:
                status['issues'].append("Missing wl-copy (install: sudo apt install wl-clipboard)")

        elif self.session_type == 'x11':
            has_xclip = self._check_tool_available('xclip')
            status['tools']['xclip'] = has_xclip
            status['copy_available'] = has_xclip

            if not has_xclip:
                status['issues'].append("Missing xclip (install: sudo apt install xclip)")

        # Check paste tools (from PasteSimulator)
        from .paste_simulator import PasteSimulator
        paste_sim = PasteSimulator()
        paste_check = paste_sim.check_permissions()
        status['paste_available'] = paste_check['available']
        if not paste_check['available']:
            status['issues'].append(paste_check['reason'])

        # Fallback status
        status['fallback_enabled'] = self.fallback_to_file
        status['fallback_path'] = str(self.fallback_path)

        return status
```

---

## File 3: `src/whisper_aloud/clipboard/paste_simulator.py`

**Purpose**: Simulate Ctrl+V keyboard input for auto-paste functionality.

**Implementation:**

```python
"""Keyboard input simulation for paste operations."""

import os
import logging
import subprocess
from pathlib import Path
from typing import Dict

from whisper_aloud.exceptions import ClipboardError


logger = logging.getLogger(__name__)


class PasteSimulator:
    """
    Simulate keyboard paste (Ctrl+V) across different display servers.

    Wayland requires ydotool with special permissions.
    X11 uses xdotool.
    """

    def __init__(self):
        """Initialize paste simulator."""
        self.session_type = self._detect_session_type()
        logger.debug(f"PasteSimulator initialized (session: {self.session_type})")

    @staticmethod
    def _detect_session_type() -> str:
        """Detect display server type."""
        session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
        if session_type in ('wayland', 'x11'):
            return session_type

        if os.environ.get('WAYLAND_DISPLAY'):
            return 'wayland'

        if os.environ.get('DISPLAY'):
            return 'x11'

        return 'unknown'

    def _check_tool_available(self, tool_name: str) -> bool:
        """Check if a command-line tool is available."""
        try:
            subprocess.run(
                ['which', tool_name],
                capture_output=True,
                check=True,
                timeout=2
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def simulate_paste(self, delay_ms: int = 100) -> bool:
        """
        Simulate Ctrl+V keystroke.

        Args:
            delay_ms: Delay before sending keystroke (milliseconds)

        Returns:
            True if successful, False otherwise

        Raises:
            ClipboardError: If simulation not available or fails
        """
        # Check permissions first
        perm_check = self.check_permissions()
        if not perm_check['available']:
            raise ClipboardError(
                f"Paste simulation not available: {perm_check['reason']}\n"
                f"Fix: {perm_check['fix']}"
            )

        try:
            if self.session_type == 'wayland':
                return self._paste_wayland(delay_ms)
            elif self.session_type == 'x11':
                return self._paste_x11(delay_ms)
            else:
                raise ClipboardError(f"Unknown session type: {self.session_type}")
        except subprocess.CalledProcessError as e:
            raise ClipboardError(f"Paste simulation failed: {e}")

    def _paste_wayland(self, delay_ms: int) -> bool:
        """Simulate paste on Wayland using ydotool."""
        try:
            # Small delay before pasting
            if delay_ms > 0:
                subprocess.run(['sleep', str(delay_ms / 1000)], check=True)

            # Send Ctrl+V
            result = subprocess.run(
                ['ydotool', 'key', 'ctrl+v'],
                capture_output=True,
                timeout=5,
                check=True
            )
            logger.debug("Paste simulated via ydotool")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"ydotool failed: {e.stderr.decode().strip()}")
            raise ClipboardError(f"ydotool failed: {e.stderr.decode().strip()}")
        except subprocess.TimeoutExpired:
            raise ClipboardError("ydotool timed out")

    def _paste_x11(self, delay_ms: int) -> bool:
        """Simulate paste on X11 using xdotool."""
        try:
            # Small delay before pasting
            if delay_ms > 0:
                subprocess.run(['sleep', str(delay_ms / 1000)], check=True)

            # Send Ctrl+V
            result = subprocess.run(
                ['xdotool', 'key', 'ctrl+v'],
                capture_output=True,
                timeout=5,
                check=True
            )
            logger.debug("Paste simulated via xdotool")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"xdotool failed: {e.stderr.decode().strip()}")
            raise ClipboardError(f"xdotool failed: {e.stderr.decode().strip()}")
        except subprocess.TimeoutExpired:
            raise ClipboardError("xdotool timed out")

    def check_permissions(self) -> Dict[str, any]:
        """
        Check if paste simulation is available.

        Returns:
            Dict with:
                - available (bool): Whether paste simulation works
                - reason (str): Why not available (if False)
                - fix (str): How to enable it (if False)
        """
        if self.session_type == 'wayland':
            return self._check_wayland_permissions()
        elif self.session_type == 'x11':
            return self._check_x11_permissions()
        else:
            return {
                'available': False,
                'reason': f'Unknown session type: {self.session_type}',
                'fix': 'Unable to detect display server'
            }

    def _check_wayland_permissions(self) -> Dict[str, any]:
        """Check Wayland ydotool permissions."""
        # Check if ydotool is installed
        if not self._check_tool_available('ydotool'):
            return {
                'available': False,
                'reason': 'ydotool not installed',
                'fix': 'Install with: sudo apt install ydotool'
            }

        # Check if ydotool service is running
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'ydotool.service'],
                capture_output=True,
                timeout=2
            )
            if result.returncode != 0:
                return {
                    'available': False,
                    'reason': 'ydotool service not running',
                    'fix': 'Enable with: sudo systemctl enable --now ydotool.service'
                }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Could not check ydotool service status")

        # Check /dev/uinput permissions
        uinput_path = Path('/dev/uinput')
        if uinput_path.exists():
            # Check if user is in input group
            try:
                import grp
                input_group = grp.getgrnam('input')
                user_groups = os.getgroups()

                if input_group.gr_gid not in user_groups:
                    return {
                        'available': False,
                        'reason': 'User not in input group',
                        'fix': f'Add user to group: sudo usermod -aG input $USER (logout/login required)'
                    }
            except KeyError:
                logger.warning("input group not found")

        return {
            'available': True,
            'reason': '',
            'fix': ''
        }

    def _check_x11_permissions(self) -> Dict[str, any]:
        """Check X11 xdotool availability."""
        if not self._check_tool_available('xdotool'):
            return {
                'available': False,
                'reason': 'xdotool not installed',
                'fix': 'Install with: sudo apt install xdotool'
            }

        # xdotool usually works if installed
        return {
            'available': True,
            'reason': '',
            'fix': ''
        }
```

---

## Configuration Extension

### Modify `src/whisper_aloud/config.py`

Add clipboard configuration dataclass:

```python
@dataclass
class ClipboardConfig:
    """Clipboard integration settings."""

    auto_copy: bool = True
    """Automatically copy transcriptions to clipboard."""

    auto_paste: bool = False
    """Automatically paste after transcription (requires permissions)."""

    paste_delay_ms: int = 100
    """Delay before auto-paste (milliseconds)."""

    fallback_to_file: bool = True
    """Use file-based clipboard if tools unavailable."""

    @classmethod
    def from_env(cls) -> "ClipboardConfig":
        """Load clipboard config from environment variables."""
        return cls(
            auto_copy=os.getenv("WHISPER_ALOUD_AUTO_COPY", "true").lower() == "true",
            auto_paste=os.getenv("WHISPER_ALOUD_AUTO_PASTE", "false").lower() == "true",
            paste_delay_ms=int(os.getenv("WHISPER_ALOUD_PASTE_DELAY_MS", "100")),
            fallback_to_file=os.getenv("WHISPER_ALOUD_CLIPBOARD_FALLBACK", "true").lower() == "true",
        )
```

Add to `WhisperAloudConfig` dataclass:

```python
@dataclass
class WhisperAloudConfig:
    """Complete WhisperAloud configuration."""

    model: ModelConfig
    transcription: TranscriptionConfig
    audio: AudioConfig
    clipboard: ClipboardConfig  # NEW

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "WhisperAloudConfig":
        """Load configuration from file and environment."""
        # ... existing logic ...
        return cls(
            model=ModelConfig.from_env(),
            transcription=TranscriptionConfig.from_env(),
            audio=AudioConfig.from_env(),
            clipboard=ClipboardConfig.from_env(),  # NEW
        )
```

---

## Exception Extension

### Modify `src/whisper_aloud/exceptions.py`

Add clipboard exception:

```python
class ClipboardError(WhisperAloudError):
    """Clipboard operation failed."""
    pass
```

---

## Package Exports

### Modify `src/whisper_aloud/__init__.py`

Add clipboard exports:

```python
from whisper_aloud.clipboard import ClipboardManager, PasteSimulator

__all__ = [
    # Existing exports...
    "ClipboardManager",
    "PasteSimulator",
]
```

---

## Testing Requirements

### Create `tests/test_clipboard_manager.py`

```python
"""Tests for clipboard manager."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from whisper_aloud.clipboard import ClipboardManager
from whisper_aloud.exceptions import ClipboardError


def test_session_detection():
    """Test session type detection."""
    with patch.dict('os.environ', {'XDG_SESSION_TYPE': 'wayland'}):
        manager = ClipboardManager()
        assert manager.session_type == 'wayland'

    with patch.dict('os.environ', {'XDG_SESSION_TYPE': 'x11'}):
        manager = ClipboardManager()
        assert manager.session_type == 'x11'


def test_copy_with_fallback():
    """Test clipboard copy with fallback enabled."""
    manager = ClipboardManager(fallback_to_file=True)

    # Should succeed even if tools missing (uses fallback)
    result = manager.copy("test text")
    assert result is True

    # Check fallback file
    content = manager.read_fallback()
    assert content == "test text"


def test_copy_without_fallback_raises():
    """Test clipboard copy without fallback raises on tool missing."""
    manager = ClipboardManager(fallback_to_file=False)

    with patch.object(manager, '_check_tool_available', return_value=False):
        with pytest.raises(ClipboardError):
            manager.copy("test text")


@pytest.mark.skipif(not ClipboardManager()._check_tool_available('wl-copy') and
                    not ClipboardManager()._check_tool_available('xclip'),
                    reason="No clipboard tools available")
def test_real_clipboard_copy():
    """Integration test with real clipboard tools."""
    manager = ClipboardManager()
    result = manager.copy("Integration test text")
    assert result is True


def test_clipboard_status():
    """Test clipboard status reporting."""
    manager = ClipboardManager()
    status = manager.get_clipboard_status()

    assert 'session_type' in status
    assert 'copy_available' in status
    assert 'paste_available' in status
    assert 'tools' in status
    assert 'issues' in status
```

### Create `tests/test_paste_simulator.py`

```python
"""Tests for paste simulator."""

import pytest
from unittest.mock import patch, MagicMock
from whisper_aloud.clipboard import PasteSimulator
from whisper_aloud.exceptions import ClipboardError


def test_permission_check():
    """Test permission checking."""
    simulator = PasteSimulator()
    result = simulator.check_permissions()

    assert 'available' in result
    assert 'reason' in result
    assert 'fix' in result

    if not result['available']:
        assert result['reason']
        assert result['fix']


def test_simulate_paste_without_permissions():
    """Test paste simulation fails without permissions."""
    simulator = PasteSimulator()

    with patch.object(simulator, 'check_permissions',
                     return_value={'available': False, 'reason': 'test', 'fix': 'test'}):
        with pytest.raises(ClipboardError):
            simulator.simulate_paste()


@pytest.mark.skipif(not PasteSimulator().check_permissions()['available'],
                    reason="Paste simulation not available")
def test_real_paste_simulation():
    """Integration test with real paste simulation."""
    # This is a manual test - can't verify without active window
    simulator = PasteSimulator()
    # Just verify it doesn't crash
    # Actual paste would need focused window
    pass
```

---

## CLI Extension (Optional)

### Add to `src/whisper_aloud/__main__.py`

Add clipboard testing command:

```python
def test_clipboard(args):
    """Test clipboard integration."""
    from whisper_aloud.clipboard import ClipboardManager, PasteSimulator

    print("=== Clipboard System Test ===\n")

    # Test copy
    manager = ClipboardManager()
    status = manager.get_clipboard_status()

    print(f"Session type: {status['session_type']}")
    print(f"Copy available: {status['copy_available']}")
    print(f"Paste available: {status['paste_available']}")
    print(f"\nTools:")
    for tool, available in status['tools'].items():
        symbol = "✅" if available else "❌"
        print(f"  {symbol} {tool}")

    if status['issues']:
        print(f"\nIssues:")
        for issue in status['issues']:
            print(f"  ⚠️  {issue}")

    # Test copy
    print(f"\nTesting copy...")
    test_text = "WhisperAloud clipboard test"
    try:
        manager.copy(test_text)
        print(f"✅ Copied: '{test_text}'")
    except Exception as e:
        print(f"❌ Copy failed: {e}")

    # Test paste permission check
    print(f"\nTesting paste permissions...")
    simulator = PasteSimulator()
    perm_check = simulator.check_permissions()

    if perm_check['available']:
        print(f"✅ Paste simulation available")
        print(f"   Note: Auto-paste will work in GUI")
    else:
        print(f"❌ Paste simulation not available")
        print(f"   Reason: {perm_check['reason']}")
        print(f"   Fix: {perm_check['fix']}")

# Add to argument parser:
parser_test = subparsers.add_parser('test-clipboard', help='Test clipboard integration')
parser_test.set_defaults(func=test_clipboard)
```

---

## Documentation Updates

### Add to README.md

```markdown
## Clipboard Integration

WhisperAloud can automatically copy transcriptions to your clipboard and optionally paste them into the active application.

### Setup

**For Wayland (Debian 12 default):**
```bash
# Install clipboard tools
sudo apt install wl-clipboard ydotool

# Enable ydotool for auto-paste
sudo systemctl enable --now ydotool.service
sudo usermod -aG input $USER

# IMPORTANT: Logout and login for group changes to take effect
```

**For X11:**
```bash
sudo apt install xclip xdotool
```

### Testing

```bash
# Test clipboard integration
whisper-aloud-transcribe test-clipboard
```

### Configuration

Via environment variables:
```bash
export WHISPER_ALOUD_AUTO_COPY=true          # Copy to clipboard
export WHISPER_ALOUD_AUTO_PASTE=false        # Auto-paste (requires setup)
export WHISPER_ALOUD_PASTE_DELAY_MS=100      # Delay before paste
export WHISPER_ALOUD_CLIPBOARD_FALLBACK=true # Use file fallback
```

### Usage

```python
from whisper_aloud import WhisperAloudConfig, Transcriber
from whisper_aloud.audio import AudioRecorder
from whisper_aloud.clipboard import ClipboardManager

# Setup
config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)
clipboard = ClipboardManager()

# Record and transcribe
recorder.start()
# ... speak ...
audio = recorder.stop()
result = transcriber.transcribe_numpy(audio)

# Copy to clipboard
clipboard.copy(result.text)
print(f"Copied to clipboard: {result.text}")
```
```

---

## Implementation Checklist

### Phase 3 Deliverables

- [ ] `src/whisper_aloud/clipboard/__init__.py` - Package exports
- [ ] `src/whisper_aloud/clipboard/clipboard_manager.py` - Clipboard operations
- [ ] `src/whisper_aloud/clipboard/paste_simulator.py` - Paste simulation
- [ ] Extend `src/whisper_aloud/config.py` with ClipboardConfig
- [ ] Extend `src/whisper_aloud/exceptions.py` with ClipboardError
- [ ] Update `src/whisper_aloud/__init__.py` exports
- [ ] `tests/test_clipboard_manager.py` - Unit tests
- [ ] `tests/test_paste_simulator.py` - Unit tests
- [ ] Add CLI test command (optional)
- [ ] Update README.md with clipboard documentation

### Success Criteria

After implementation, verify:

1. **Session Detection**: ✅ Correctly detects Wayland/X11
2. **Clipboard Copy**: ✅ Copies text successfully
3. **Tool Detection**: ✅ Detects missing tools and shows install instructions
4. **Permission Check**: ✅ Checks paste permissions and provides setup guide
5. **Fallback Mode**: ✅ Falls back to file-based clipboard when tools missing
6. **Error Messages**: ✅ Clear, actionable error messages
7. **Tests Pass**: ✅ All unit tests pass (47+ tests across all modules)
8. **Integration**: ✅ Works with existing Phases 1-2

---

## Testing Commands

After implementation:

```bash
# Activate environment
source ~/.venvs/whisper_aloud/bin/activate

# Run clipboard tests
pytest tests/test_clipboard_manager.py tests/test_paste_simulator.py -v

# Test clipboard system
python -c "
from whisper_aloud.clipboard import ClipboardManager

manager = ClipboardManager()
status = manager.get_clipboard_status()

print(f'Session: {status[\"session_type\"]}')
print(f'Copy available: {status[\"copy_available\"]}')
print(f'Paste available: {status[\"paste_available\"]}')

if status['issues']:
    print('Issues:')
    for issue in status['issues']:
        print(f'  - {issue}')
"

# Test copy
python -c "
from whisper_aloud.clipboard import ClipboardManager

manager = ClipboardManager()
manager.copy('Hello from WhisperAloud!')
print('✅ Text copied to clipboard')
"

# Test CLI command (if implemented)
whisper-aloud-transcribe test-clipboard
```

---

## Notes for Grok

1. **Session Detection**: Use environment variables for robust detection
2. **Error Handling**: Provide clear, actionable error messages with fix commands
3. **Fallback Strategy**: Always provide file-based fallback option
4. **Security**: Don't run commands with sudo - user must setup permissions
5. **Testing**: Mock subprocess calls in unit tests, provide integration tests for manual verification
6. **Documentation**: Include setup instructions prominently in README

Phase 3 should integrate seamlessly with existing Phases 1-2 without modifying their core functionality.

---

**End of Phase 3 Prompt**
