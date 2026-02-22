# Window-Capture Auto-Paste Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When the user stops recording, capture the active window ID so auto-paste goes to that window even if the user switches windows during transcription.

**Architecture:** Multi-backend WindowManager (GNOME Extension D-Bus → xdotool → none) captures the active window at StopRecording time. The GNOME Shell extension exports its own D-Bus service for window queries using Gio.DBusProxy (typed calls, no gdbus subprocess parsing). The daemon stores captured WindowInfo per-job and activates it before pasting. Configurable via `paste_to_captured_window` bool (default True).

**Tech Stack:** Python (daemon + WindowManager), GJS (GNOME Shell extension D-Bus server), Gio.DBus (extension D-Bus export + Python typed proxy), xdotool (X11 fallback), GTK4 (settings toggle)

**Codex Review Fixes Applied:**
1. GNOME 45+ D-Bus export: `wrapJSObject` needs `Gio.DBusInterfaceInfo` (parse XML first)
2. Python GNOME backend uses `Gio.DBusProxy` typed calls (no gdbus subprocess + regex)
3. Activation timestamp: `global.display.get_current_time_roundtrip()`
4. Per-job window capture (no global `_captured_window` race)
5. Window capture in auto-stop path too
6. xdotool detection probes actual `getactivewindow` (not just `which`)

---

### Task 1: Add `paste_to_captured_window` to ClipboardConfig

**Files:**
- Modify: `src/whisper_aloud/config.py:122-131` (ClipboardConfig dataclass)
- Modify: `src/whisper_aloud/config.py:239-247` (to_dict clipboard section)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

In `tests/test_config.py`, add:

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
Expected: FAIL — `AttributeError`

**Step 3: Write minimal implementation**

In `ClipboardConfig` (after `fallback_path` field), add:

```python
paste_to_captured_window: bool = True
```

In `to_dict()` clipboard section, add:

```python
"paste_to_captured_window": self.clipboard.paste_to_captured_window,
```

In `_apply_env_overrides()`, add:

```python
self.clipboard.paste_to_captured_window = parse_bool_env(
    'WHISPER_ALOUD_CLIPBOARD_PASTE_TO_CAPTURED_WINDOW',
    self.clipboard.paste_to_captured_window
)
```

**Step 4: Run test to verify it passes**

**Step 5: Commit**

```bash
git add src/whisper_aloud/config.py tests/test_config.py
git commit -m "feat(config): add paste_to_captured_window clipboard option"
```

---

### Task 2: Create WindowManager module with Gio.DBusProxy + xdotool backends

**Files:**
- Create: `src/whisper_aloud/clipboard/window_manager.py`
- Modify: `src/whisper_aloud/clipboard/__init__.py`
- Create: `tests/test_window_manager.py`

**Codex fixes applied here:**
- GNOME backend uses `Gio.DBusProxy.new_sync()` + typed `call_sync()` — no gdbus subprocess, no regex parsing
- xdotool detection probes actual `getactivewindow` (not just `which`)
- Gio import is try/except guarded (graceful when not available)

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
    def test_fallback_to_none_when_nothing_available(self):
        with patch("whisper_aloud.clipboard.window_manager.subprocess.run", side_effect=FileNotFoundError):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
                assert wm.backend == "none"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_xdotool_probes_getactivewindow(self, mock_run):
        """xdotool detection runs actual getactivewindow, not just 'which'."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b"12345\n")
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
            assert wm.backend == "xdotool"
            # Verify it called xdotool getactivewindow, not which
            mock_run.assert_called_once_with(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )


class TestXdotoolBackend:
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_get_active_window(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"12345\n"),  # getactivewindow
            MagicMock(returncode=0, stdout=b"My Terminal\n"),  # getwindowname
        ]
        wm = WindowManager()
        wm.backend = "xdotool"
        info = wm.get_active_window()
        assert info is not None
        assert info.window_id == 12345
        assert info.title == "My Terminal"
        assert info.backend == "xdotool"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_activate_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        wm = WindowManager()
        wm.backend = "xdotool"
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        assert wm.activate_window(info) is True


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

**Step 3: Write minimal implementation**

Create `src/whisper_aloud/clipboard/window_manager.py`:

```python
"""Window management for targeted paste operations."""

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import Gio for typed D-Bus calls (GNOME backend)
try:
    from gi.repository import Gio, GLib
    _HAS_GIO = True
except ImportError:
    _HAS_GIO = False


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
        self._gnome_proxy: Optional[object] = None
        self.backend = self._detect_backend()
        logger.info(f"WindowManager initialized with backend: {self.backend}")

    def _detect_backend(self) -> str:
        if self._check_gnome_extension():
            return "gnome-extension"
        if self._check_xdotool():
            return "xdotool"
        return "none"

    def _check_gnome_extension(self) -> bool:
        """Check if GNOME extension D-Bus service is available via typed proxy."""
        if not _HAS_GIO:
            return False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                "org.gnome.Shell.Extensions.whisperaloud",
                "/org/fede/whisperaloud/Window",
                "org.fede.whisperaloud.Window", None,
            )
            res = proxy.call_sync(
                "GetFocusedWindow", None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            if res is not None:
                self._gnome_proxy = proxy
                return True
            return False
        except Exception:
            return False

    def _check_xdotool(self) -> bool:
        """Probe xdotool by running actual getactivewindow (not just 'which')."""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_active_window(self) -> Optional[WindowInfo]:
        if self.backend == "gnome-extension":
            return self._get_active_gnome()
        elif self.backend == "xdotool":
            return self._get_active_xdotool()
        return None

    def activate_window(self, info: WindowInfo) -> bool:
        if info.backend == "gnome-extension":
            return self._activate_gnome(info)
        elif info.backend == "xdotool":
            return self._activate_xdotool(info)
        return False

    # ── GNOME Extension backend (Gio.DBusProxy typed calls) ─────────────

    def _ensure_gnome_proxy(self) -> bool:
        if self._gnome_proxy is not None:
            return True
        if not _HAS_GIO:
            return False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._gnome_proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                "org.gnome.Shell.Extensions.whisperaloud",
                "/org/fede/whisperaloud/Window",
                "org.fede.whisperaloud.Window", None,
            )
            return True
        except Exception:
            return False

    def _get_active_gnome(self) -> Optional[WindowInfo]:
        if not self._ensure_gnome_proxy():
            return None
        try:
            res = self._gnome_proxy.call_sync(
                "GetFocusedWindow", None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            d = res.get_child_value(0)
            wid_v = d.lookup_value("id", None)
            if wid_v is None:
                return None
            title_v = d.lookup_value("title", None)
            return WindowInfo(
                window_id=int(wid_v.unpack()),
                title=title_v.unpack() if title_v else "",
                backend="gnome-extension",
            )
        except Exception as e:
            logger.warning(f"GNOME GetFocusedWindow failed: {e}")
            return None

    def _activate_gnome(self, info: WindowInfo) -> bool:
        if not self._ensure_gnome_proxy():
            return False
        try:
            v = GLib.Variant("(u)", (int(info.window_id),))
            self._gnome_proxy.call_sync(
                "ActivateWindow", v,
                Gio.DBusCallFlags.NONE, 3000, None,
            )
            logger.info(f"Activated window {info.window_id} via GNOME extension")
            return True
        except Exception as e:
            logger.warning(f"GNOME ActivateWindow failed: {e}")
            return False

    # ── xdotool backend ─────────────────────────────────────────────────

    def _get_active_xdotool(self) -> Optional[WindowInfo]:
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )
            if result.returncode != 0:
                return None
            window_id = int(result.stdout.decode().strip())
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

**Step 4: Run tests**

**Step 5: Commit**

```bash
git add src/whisper_aloud/clipboard/window_manager.py src/whisper_aloud/clipboard/__init__.py tests/test_window_manager.py
git commit -m "feat(clipboard): add WindowManager with Gio.DBusProxy + xdotool backends"
```

---

### Task 3: Add GetFocusedWindow and ActivateWindow to GNOME Shell extension

**Files:**
- Modify: `contrib/gnome-shell-extension/whisperaloud@fede/extension.js`

**Codex fixes applied here:**
- Parse XML to `Gio.DBusNodeInfo` then get `.interfaces[0]` for `wrapJSObject`
- Use `global.display.get_current_time_roundtrip()` for activation timestamp
- Add `GLib` import

**Step 1: Implement the extension D-Bus server**

Add `GLib` import at top of `extension.js`:

```javascript
import GLib from 'gi://GLib';
```

Add D-Bus interface XML and `WindowDBusService` class before `WhisperAloudExtension`:

```javascript
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

// GNOME 45+ ESM: wrapJSObject requires Gio.DBusInterfaceInfo, not raw XML
const WINDOW_NODE  = Gio.DBusNodeInfo.new_for_xml(WINDOW_IFACE_XML);
const WINDOW_IFACE = WINDOW_NODE.interfaces[0];

class WindowDBusService {
    constructor() {
        this._impl   = null;
        this._nameId = null;
    }

    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(WINDOW_IFACE, this);
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
        if (!win) return {};
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
                // Use roundtrip timestamp to avoid focus-steal prevention
                win.activate(global.display.get_current_time_roundtrip());
                return true;
            }
        }
        return false;
    }
}
```

Update `WhisperAloudExtension.enable()` and `disable()` to manage the window service.

**Step 2: Test manually**

```bash
gnome-extensions disable whisperaloud@fede && gnome-extensions enable whisperaloud@fede
gdbus call --session \
  --dest org.gnome.Shell.Extensions.whisperaloud \
  --object-path /org/fede/whisperaloud/Window \
  --method org.fede.whisperaloud.Window.GetFocusedWindow
```

**Step 3: Commit**

```bash
git add contrib/gnome-shell-extension/whisperaloud@fede/extension.js
git commit -m "feat(gnome-ext): add D-Bus GetFocusedWindow/ActivateWindow for window capture"
```

---

### Task 4: Integrate WindowManager into daemon (per-job capture, both stop paths)

**Files:**
- Modify: `src/whisper_aloud/service/daemon.py`

**Codex fixes applied here:**
- Per-job window capture via `_captured_by_job` dict (not single global)
- Capture in both `StopRecording()` and auto-stop path (`_on_recorder_auto_stop` or equivalent)
- Consume atomically before paste
- Configurable focus-settle delay uses `paste_delay_ms`

**Step 1: Implementation**

In `daemon.py` imports, add:

```python
from ..clipboard import WindowManager
```

In init/startup, after clipboard_manager:

```python
self.window_manager = WindowManager()
self._captured_by_job: dict = {}  # job_id → WindowInfo
```

In `StopRecording()` — generate a `job_id`, capture window, pass to transcription:

```python
def StopRecording(self) -> str:
    # ... existing checks ...
    job_id = uuid.uuid4().hex
    captured = None
    if self.config.clipboard.paste_to_captured_window:
        try:
            captured = self.window_manager.get_active_window()
            if captured:
                logger.info(f"Captured window for job {job_id[:8]}: "
                            f"{captured.title} (id={captured.window_id})")
        except Exception as e:
            logger.warning(f"Failed to capture window: {e}")
    if captured:
        self._captured_by_job[job_id] = captured
    # ... stop recorder, start transcription ...
    self.executor.submit(self._transcribe_and_emit, audio_data, job_id)
    return "transcribing"
```

Do the same in the auto-stop path (when max_recording_duration triggers).

Update `_transcribe_and_emit` signature to accept `job_id`:

```python
def _transcribe_and_emit(self, audio_data, job_id: str = "") -> None:
```

In `_emit_success`, consume captured window for this job:

```python
captured = self._captured_by_job.pop(job_id, None)

def _do_paste():
    if captured and self.config.clipboard.paste_to_captured_window:
        try:
            self.window_manager.activate_window(captured)
            time.sleep(self.config.clipboard.paste_delay_ms / 1000.0)
        except Exception as e:
            logger.warning(f"Failed to activate captured window: {e}")
    simulator.simulate_paste()

_threading.Thread(target=_do_paste, daemon=True).start()
```

**Step 2: Run full test suite**

**Step 3: Commit**

```bash
git add src/whisper_aloud/service/daemon.py
git commit -m "feat(daemon): per-job window capture at StopRecording + auto-stop for targeted paste"
```

---

### Task 5: Add settings toggle for paste_to_captured_window

**Files:**
- Modify: `src/whisper_aloud/ui/settings_dialog.py`

**Step 1: Add toggle after auto_paste switch**

```python
captured_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
captured_label = Gtk.Label(label="Paste to captured window")
captured_label.set_hexpand(True)
captured_label.set_halign(Gtk.Align.START)
captured_label.set_tooltip_text(
    "Paste goes to the window active when you stopped recording, "
    "even if you switch windows during transcription."
)
self._paste_captured_switch = Gtk.Switch()
self._paste_captured_switch.set_active(self._config.clipboard.paste_to_captured_window)
captured_row.append(captured_label)
captured_row.append(self._paste_captured_switch)
clipboard_box.append(captured_row)
```

**Step 2: Add to save logic**

```python
self._config.clipboard.paste_to_captured_window = self._paste_captured_switch.get_active()
```

**Step 3: Commit**

```bash
git add src/whisper_aloud/ui/settings_dialog.py
git commit -m "feat(ui): add paste-to-captured-window toggle in settings"
```

---

### Task 6: Install, reload extension, end-to-end test

1. Copy extension files + reload
2. `gdbus call` to verify GetFocusedWindow works
3. E2E: record in editor → switch to terminal → stop → verify paste goes to editor
4. Test fallback with extension disabled

---

### Task 7: Full test suite + lint

```bash
python -m pytest -v
black --check src/
ruff check src/
```
