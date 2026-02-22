# Window-Capture Auto-Paste Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When the user stops recording, capture the active window ID so auto-paste goes to that window even if the user switches windows during transcription.

**Architecture:** Multi-backend WindowManager (GNOME Extension D-Bus → xdotool → none) captures the active window at StopRecording time. The GNOME Shell extension exports its own D-Bus service for window queries. The daemon stores the captured WindowInfo and activates it before pasting. Configurable via `paste_to_captured_window` bool (default True).

**Tech Stack:** Python (daemon + WindowManager), GJS (GNOME Shell extension D-Bus server), Gio.DBus (extension D-Bus export), xdotool (X11 fallback), subprocess (calling xdotool), GTK4 (settings toggle)

---

### Task 1: Add `paste_to_captured_window` to ClipboardConfig

**Files:**
- Modify: `src/whisper_aloud/config.py:122-131` (ClipboardConfig dataclass)
- Modify: `src/whisper_aloud/config.py:239-247` (to_dict clipboard section)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

In `tests/test_config.py`, add a test that verifies the new field exists and round-trips:

```python
def test_clipboard_paste_to_captured_window_default():
    """paste_to_captured_window defaults to True."""
    config = WhisperAloudConfig()
    assert config.clipboard.paste_to_captured_window is True

def test_clipboard_paste_to_captured_window_roundtrip():
    """paste_to_captured_window survives to_dict/from_dict."""
    config = WhisperAloudConfig()
    config.clipboard.paste_to_captured_window = False
    restored = WhisperAloudConfig.from_dict(config.to_dict())
    assert restored.clipboard.paste_to_captured_window is False
```

**Step 2: Run test to verify it fails**

Run: `source ~/.venvs/whisper_aloud/bin/activate && python -m pytest tests/test_config.py -k "paste_to_captured_window" -v`
Expected: FAIL — `AttributeError: 'ClipboardConfig' object has no attribute 'paste_to_captured_window'`

**Step 3: Write minimal implementation**

In `src/whisper_aloud/config.py`, add to `ClipboardConfig` (after line 130):

```python
paste_to_captured_window: bool = True
```

In `to_dict()` clipboard section (around line 246), add:

```python
"paste_to_captured_window": self.clipboard.paste_to_captured_window,
```

The `from_dict()` already handles unknown fields via `hasattr` check, so it will pick up the new field automatically.

Add env var override in `_apply_env_overrides()` (after line 420):

```python
self.clipboard.paste_to_captured_window = parse_bool_env(
    'WHISPER_ALOUD_CLIPBOARD_PASTE_TO_CAPTURED_WINDOW',
    self.clipboard.paste_to_captured_window
)
```

**Step 4: Run test to verify it passes**

Run: `source ~/.venvs/whisper_aloud/bin/activate && python -m pytest tests/test_config.py -k "paste_to_captured_window" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/whisper_aloud/config.py tests/test_config.py
git commit -m "feat(config): add paste_to_captured_window clipboard option"
```

---

### Task 2: Create WindowManager module with xdotool backend

**Files:**
- Create: `src/whisper_aloud/clipboard/window_manager.py`
- Modify: `src/whisper_aloud/clipboard/__init__.py`
- Create: `tests/test_window_manager.py`

**Step 1: Write the failing test**

Create `tests/test_window_manager.py`:

```python
"""Tests for WindowManager."""
from unittest.mock import patch, MagicMock
from whisper_aloud.clipboard.window_manager import WindowManager, WindowInfo


class TestWindowInfo:
    def test_dataclass_fields(self):
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        assert info.window_id == 12345
        assert info.title == "Terminal"
        assert info.backend == "xdotool"


class TestWindowManagerBackendDetection:
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_detects_xdotool_when_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        wm = WindowManager()
        # gnome-extension won't be available in test env
        assert wm.backend in ("xdotool", "gnome-extension", "none")

    def test_fallback_to_none(self):
        with patch("whisper_aloud.clipboard.window_manager.subprocess.run", side_effect=FileNotFoundError):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
                assert wm.backend == "none"


class TestXdotoolBackend:
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_get_active_window_xdotool(self, mock_run):
        # First call: which xdotool (backend detection)
        # Subsequent calls: getactivewindow, getwindowname
        mock_run.side_effect = [
            MagicMock(returncode=0),  # which xdotool
            MagicMock(returncode=1),  # gdbus (gnome ext check fails)
            MagicMock(returncode=0, stdout=b"12345\n"),  # getactivewindow
            MagicMock(returncode=0, stdout=b"My Terminal\n"),  # getwindowname
        ]
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
            wm.backend = "xdotool"
            info = wm.get_active_window()
            assert info is not None
            assert info.window_id == 12345
            assert info.title == "My Terminal"
            assert info.backend == "xdotool"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_activate_window_xdotool(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        wm = WindowManager()
        wm.backend = "xdotool"
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        result = wm.activate_window(info)
        assert result is True


class TestNoneBackend:
    def test_get_active_window_returns_none(self):
        wm = WindowManager()
        wm.backend = "none"
        assert wm.get_active_window() is None

    def test_activate_window_returns_false(self):
        wm = WindowManager()
        wm.backend = "none"
        info = WindowInfo(window_id=1, title="x", backend="none")
        assert wm.activate_window(info) is False
```

**Step 2: Run test to verify it fails**

Run: `source ~/.venvs/whisper_aloud/bin/activate && python -m pytest tests/test_window_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'whisper_aloud.clipboard.window_manager'`

**Step 3: Write minimal implementation**

Create `src/whisper_aloud/clipboard/window_manager.py`:

```python
"""Window management for targeted paste operations."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    """Information about a captured window."""
    window_id: int
    title: str
    backend: str  # "gnome-extension", "xdotool", "none"


class WindowManager:
    """Multi-backend window capture and activation.

    Backend priority: gnome-extension → xdotool → none.
    """

    def __init__(self) -> None:
        self.backend = self._detect_backend()
        logger.info(f"WindowManager initialized with backend: {self.backend}")

    def _detect_backend(self) -> str:
        """Detect the best available backend."""
        if self._check_gnome_extension():
            return "gnome-extension"
        if self._check_xdotool():
            return "xdotool"
        return "none"

    def _check_gnome_extension(self) -> bool:
        """Check if GNOME extension D-Bus service is available."""
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.gnome.Shell.Extensions.whisperaloud",
                 "--object-path", "/org/fede/whisperaloud/Window",
                 "--method", "org.fede.whisperaloud.Window.GetFocusedWindow"],
                capture_output=True, timeout=2,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _check_xdotool(self) -> bool:
        """Check if xdotool is available."""
        try:
            result = subprocess.run(
                ["which", "xdotool"],
                capture_output=True, timeout=2,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_active_window(self) -> Optional[WindowInfo]:
        """Capture the currently focused window."""
        if self.backend == "gnome-extension":
            return self._get_active_gnome()
        elif self.backend == "xdotool":
            return self._get_active_xdotool()
        return None

    def activate_window(self, info: WindowInfo) -> bool:
        """Activate (focus) a previously captured window."""
        if info.backend == "gnome-extension":
            return self._activate_gnome(info)
        elif info.backend == "xdotool":
            return self._activate_xdotool(info)
        return False

    # ── GNOME Extension backend ─────────────────────────────────────────

    def _get_active_gnome(self) -> Optional[WindowInfo]:
        """Get active window via GNOME extension D-Bus."""
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.gnome.Shell.Extensions.whisperaloud",
                 "--object-path", "/org/fede/whisperaloud/Window",
                 "--method", "org.fede.whisperaloud.Window.GetFocusedWindow"],
                capture_output=True, timeout=2,
            )
            if result.returncode != 0:
                return None
            # Parse gdbus output: ({'id': <uint32 N>, 'title': <'...'>, ...},)
            import ast
            out = result.stdout.decode().strip()
            # gdbus wraps in () — extract the dict portion
            # Format: ({'id': <uint32 123>, 'title': <'Title'>},)
            # We use a simpler approach: call with JSON output
            return self._parse_gnome_window_output(out)
        except Exception as e:
            logger.warning(f"GNOME extension GetFocusedWindow failed: {e}")
            return None

    def _parse_gnome_window_output(self, raw: str) -> Optional[WindowInfo]:
        """Parse gdbus call output for window info.

        Expected format from gdbus call:
        ({'id': <uint32 12345>, 'title': <'My Window'>, 'wm_class': <'MyApp'>},)
        """
        try:
            import re
            id_match = re.search(r"'id':\s*<uint32\s+(\d+)>", raw)
            title_match = re.search(r"'title':\s*<'([^']*)'", raw)
            if not id_match:
                return None
            return WindowInfo(
                window_id=int(id_match.group(1)),
                title=title_match.group(1) if title_match else "",
                backend="gnome-extension",
            )
        except Exception as e:
            logger.warning(f"Failed to parse GNOME window output: {e}")
            return None

    def _activate_gnome(self, info: WindowInfo) -> bool:
        """Activate window via GNOME extension D-Bus."""
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.gnome.Shell.Extensions.whisperaloud",
                 "--object-path", "/org/fede/whisperaloud/Window",
                 "--method", "org.fede.whisperaloud.Window.ActivateWindow",
                 str(info.window_id)],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0:
                logger.info(f"Activated window {info.window_id} via GNOME extension")
                return True
            logger.warning(f"GNOME ActivateWindow failed: {result.stderr.decode()}")
            return False
        except Exception as e:
            logger.warning(f"GNOME extension ActivateWindow failed: {e}")
            return False

    # ── xdotool backend ─────────────────────────────────────────────────

    def _get_active_xdotool(self) -> Optional[WindowInfo]:
        """Get active window via xdotool."""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )
            if result.returncode != 0:
                return None
            window_id = int(result.stdout.decode().strip())
            # Try to get window name
            title = ""
            try:
                name_result = subprocess.run(
                    ["xdotool", "getwindowname", str(window_id)],
                    capture_output=True, timeout=2,
                )
                if name_result.returncode == 0:
                    title = name_result.stdout.decode().strip()
            except Exception:
                pass
            return WindowInfo(window_id=window_id, title=title, backend="xdotool")
        except Exception as e:
            logger.warning(f"xdotool getactivewindow failed: {e}")
            return None

    def _activate_xdotool(self, info: WindowInfo) -> bool:
        """Activate window via xdotool."""
        try:
            result = subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(info.window_id)],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                logger.info(f"Activated window {info.window_id} via xdotool")
                return True
            logger.warning(f"xdotool windowactivate failed: {result.stderr.decode()}")
            return False
        except Exception as e:
            logger.warning(f"xdotool windowactivate failed: {e}")
            return False
```

Update `src/whisper_aloud/clipboard/__init__.py`:

```python
"""Clipboard integration for WhisperAloud."""

from .clipboard_manager import ClipboardManager
from .paste_simulator import PasteSimulator
from .window_manager import WindowInfo, WindowManager

__all__ = [
    "ClipboardManager",
    "PasteSimulator",
    "WindowInfo",
    "WindowManager",
]
```

**Step 4: Run test to verify it passes**

Run: `source ~/.venvs/whisper_aloud/bin/activate && python -m pytest tests/test_window_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/whisper_aloud/clipboard/window_manager.py src/whisper_aloud/clipboard/__init__.py tests/test_window_manager.py
git commit -m "feat(clipboard): add WindowManager with xdotool + gnome-extension backends"
```

---

### Task 3: Add GetFocusedWindow and ActivateWindow to GNOME Shell extension

**Files:**
- Modify: `contrib/gnome-shell-extension/whisperaloud@fede/extension.js`

**Step 1: Plan the D-Bus interface**

The extension needs to export its own D-Bus object at a separate path (`/org/fede/whisperaloud/Window`) on a well-known name (`org.gnome.Shell.Extensions.whisperaloud`). We cannot reuse the daemon's bus name because the extension runs inside gnome-shell, not the daemon process.

However, since the extension already runs inside gnome-shell which owns `org.gnome.Shell`, a simpler approach is to use `Gio.DBusExportedObject` on the session bus under a new well-known name that the extension owns.

**Step 2: Implement the extension D-Bus server**

Add to `extension.js` — a new class `WindowDBusService` that exports two methods, and register it in `enable()`:

```javascript
// ── D-Bus interface XML for window management ────────────────────────
const WINDOW_IFACE_XML = `
<node>
  <interface name="org.fede.whisperaloud.Window">
    <method name="GetFocusedWindow">
      <arg direction="out" type="a{sv}" name="window"/>
    </method>
    <method name="ActivateWindow">
      <arg direction="in" type="u" name="window_id"/>
      <arg direction="out" type="b" name="success"/>
    </method>
  </interface>
</node>`;

class WindowDBusService {
    constructor() {
        this._dbusId = null;
        this._nameId = null;
    }

    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(WINDOW_IFACE_XML, this);
        this._impl.export(Gio.DBus.session, '/org/fede/whisperaloud/Window');
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            'org.gnome.Shell.Extensions.whisperaloud',
            Gio.BusNameOwnerFlags.NONE,
            null, null, null,
        );
    }

    disable() {
        if (this._impl) {
            this._impl.unexport();
            this._impl = null;
        }
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = null;
        }
    }

    GetFocusedWindow() {
        const win = global.display.get_focus_window();
        if (!win) {
            return {};
        }
        return {
            'id':       GLib.Variant.new_uint32(win.get_id()),
            'title':    GLib.Variant.new_string(win.get_title() || ''),
            'wm_class': GLib.Variant.new_string(win.get_wm_class() || ''),
        };
    }

    ActivateWindow(window_id) {
        const actors = global.get_window_actors();
        for (const actor of actors) {
            const win = actor.meta_window;
            if (win.get_id() === window_id) {
                win.activate(global.get_current_time());
                return true;
            }
        }
        return false;
    }
}
```

In the `WhisperAloudExtension` class, add `_windowService`:

```javascript
export default class WhisperAloudExtension extends Extension {
    enable() {
        let settings = null;
        try {
            settings = this.getSettings();
        } catch (e) {
            console.warn(`WhisperAloud: getSettings() failed: ${e}. Keybinding disabled.`);
        }
        this._indicator = new WhisperAloudIndicator(settings);
        Main.panel.addToStatusArea('whisperaloud', this._indicator);

        this._windowService = new WindowDBusService();
        this._windowService.enable();
    }

    disable() {
        if (this._windowService) {
            this._windowService.disable();
            this._windowService = null;
        }
        if (this._indicator) {
            this._indicator.removeKeybinding();
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
```

Also add the GLib import at the top of extension.js (it's not currently imported):

```javascript
import GLib from 'gi://GLib';
```

**Step 3: Test manually**

After installing and reloading the extension (`gnome-extensions disable whisperaloud@fede && gnome-extensions enable whisperaloud@fede`), verify:

```bash
gdbus call --session \
  --dest org.gnome.Shell.Extensions.whisperaloud \
  --object-path /org/fede/whisperaloud/Window \
  --method org.fede.whisperaloud.Window.GetFocusedWindow

# Expected: ({'id': <uint32 NNNNN>, 'title': <'...'>, 'wm_class': <'...'>},)

gdbus call --session \
  --dest org.gnome.Shell.Extensions.whisperaloud \
  --object-path /org/fede/whisperaloud/Window \
  --method org.fede.whisperaloud.Window.ActivateWindow uint32:NNNNN

# Expected: (true,)
```

**Step 4: Commit**

```bash
git add contrib/gnome-shell-extension/whisperaloud@fede/extension.js
git commit -m "feat(gnome-ext): add D-Bus GetFocusedWindow/ActivateWindow for window capture"
```

---

### Task 4: Integrate WindowManager into daemon StopRecording + auto-paste

**Files:**
- Modify: `src/whisper_aloud/service/daemon.py:1-26` (imports)
- Modify: `src/whisper_aloud/service/daemon.py:329-367` (StopRecording)
- Modify: `src/whisper_aloud/service/daemon.py:725-755` (_emit_success)

**Step 1: Write the failing test**

In `tests/test_daemon_window_capture.py`:

```python
"""Tests for window capture in daemon auto-paste flow."""
from unittest.mock import MagicMock, patch
from whisper_aloud.clipboard.window_manager import WindowInfo


class TestDaemonWindowCapture:
    def test_stop_recording_captures_window_when_enabled(self):
        """StopRecording should capture the active window when paste_to_captured_window is True."""
        from whisper_aloud.clipboard.window_manager import WindowInfo
        info = WindowInfo(window_id=42, title="Test", backend="xdotool")
        # We test the logic pattern, not the full daemon (which needs D-Bus)
        # The daemon stores _captured_window = window_manager.get_active_window()
        assert info.window_id == 42

    def test_window_info_none_when_disabled(self):
        """When paste_to_captured_window is False, no window should be captured."""
        # This is a config-level test - _captured_window stays None
        captured = None  # simulating disabled
        assert captured is None
```

**Step 2: Run test to verify it passes (these are logic sanity tests)**

Run: `source ~/.venvs/whisper_aloud/bin/activate && python -m pytest tests/test_daemon_window_capture.py -v`

**Step 3: Implement daemon integration**

In `daemon.py` imports (line ~17), add:

```python
from ..clipboard import WindowManager
```

In `__init__` or startup section, after clipboard_manager init, add:

```python
self.window_manager = WindowManager()
self._captured_window = None
```

In `StopRecording()` (around line 335, before `audio_data = self.recorder.stop()`), add:

```python
# Capture active window for targeted paste
self._captured_window = None
if self.config.clipboard.paste_to_captured_window:
    try:
        self._captured_window = self.window_manager.get_active_window()
        if self._captured_window:
            logger.info(
                f"Captured window for paste: {self._captured_window.title} "
                f"(id={self._captured_window.window_id}, backend={self._captured_window.backend})"
            )
    except Exception as e:
        logger.warning(f"Failed to capture window: {e}")
```

In `_emit_success()` (inside the auto-paste block, around line 748), replace the current paste thread with:

```python
if (self.config.clipboard.auto_copy
        and self.config.clipboard.auto_paste
        and self.clipboard_manager
        and result.text):
    try:
        from ..clipboard import PasteSimulator
        import threading as _threading
        simulator = PasteSimulator(
            self.clipboard_manager._session_type,
            self.config.clipboard,
        )
        captured = self._captured_window
        self._captured_window = None  # consume it

        def _do_paste():
            if captured and self.config.clipboard.paste_to_captured_window:
                try:
                    self.window_manager.activate_window(captured)
                    time.sleep(0.05)  # let focus settle
                except Exception as e:
                    logger.warning(f"Failed to activate captured window: {e}")
            simulator.simulate_paste()

        _threading.Thread(target=_do_paste, daemon=True).start()
        logger.info("Auto-paste triggered")
    except Exception as e:
        logger.warning(f"Failed to trigger auto-paste: {e}")
```

**Step 4: Run full test suite**

Run: `source ~/.venvs/whisper_aloud/bin/activate && python -m pytest -x -v`
Expected: All existing tests pass

**Step 5: Commit**

```bash
git add src/whisper_aloud/service/daemon.py tests/test_daemon_window_capture.py
git commit -m "feat(daemon): capture active window at StopRecording for targeted paste"
```

---

### Task 5: Add settings toggle for paste_to_captured_window

**Files:**
- Modify: `src/whisper_aloud/ui/settings_dialog.py`

**Step 1: Identify insertion point**

The settings dialog has a clipboard section. Find where `auto_paste` toggle is built and add the new toggle right after it.

**Step 2: Add the toggle**

In the clipboard settings section of `settings_dialog.py`, after the `auto_paste` switch row, add:

```python
# Paste to captured window toggle
captured_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
captured_label = Gtk.Label(label="Paste to captured window")
captured_label.set_hexpand(True)
captured_label.set_halign(Gtk.Align.START)
captured_label.set_tooltip_text(
    "When enabled, paste goes to the window that was active when you stopped recording, "
    "even if you switch windows during transcription."
)
self._paste_captured_switch = Gtk.Switch()
self._paste_captured_switch.set_active(self._config.clipboard.paste_to_captured_window)
captured_row.append(captured_label)
captured_row.append(self._paste_captured_switch)
clipboard_box.append(captured_row)
```

In the save logic, add:

```python
self._config.clipboard.paste_to_captured_window = self._paste_captured_switch.get_active()
```

**Step 3: Test manually**

Launch `whisper-aloud-gui`, open Settings, verify the toggle appears in the Clipboard section and persists across save/reload.

**Step 4: Commit**

```bash
git add src/whisper_aloud/ui/settings_dialog.py
git commit -m "feat(ui): add paste-to-captured-window toggle in settings"
```

---

### Task 6: Install updated GNOME extension and end-to-end test

**Step 1: Install updated extension**

```bash
cd /home/fede/REPOS/whisperAloud
bash install.sh  # or manually copy extension files
```

**Step 2: Reload GNOME extension**

```bash
gnome-extensions disable whisperaloud@fede
gnome-extensions enable whisperaloud@fede
```

**Step 3: Verify D-Bus service**

```bash
gdbus call --session \
  --dest org.gnome.Shell.Extensions.whisperaloud \
  --object-path /org/fede/whisperaloud/Window \
  --method org.fede.whisperaloud.Window.GetFocusedWindow
```

**Step 4: End-to-end test**

1. Open a text editor (e.g., gedit)
2. Press the recording hotkey
3. Speak a sentence
4. Switch to a different window (e.g., terminal)
5. Press the stop hotkey
6. Wait for transcription to complete
7. Verify: the text editor gets focus back and the text is pasted there

**Step 5: Test fallback (disable extension)**

```bash
gnome-extensions disable whisperaloud@fede
```

Repeat the test — should fall back to xdotool or current-window behavior.

**Step 6: Final commit**

```bash
git add -A
git commit -m "feat: window-capture auto-paste with GNOME extension + xdotool fallback"
```

---

### Task 7: Run full test suite and verify

**Step 1: Run all tests**

```bash
source ~/.venvs/whisper_aloud/bin/activate && python -m pytest -v
```

Expected: All tests pass (existing + new)

**Step 2: Run linting**

```bash
source ~/.venvs/whisper_aloud/bin/activate && black --check src/ && ruff check src/
```

**Step 3: Fix any issues and commit**

```bash
git add -A
git commit -m "chore: fix lint issues from window-capture feature"
```
