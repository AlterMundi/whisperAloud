# WhisperAloud Phases 3-7: Implementation Roadmap for Grok

**Status**: Phase 1 ✅ Complete | Phase 2 📝 Prompt Ready | Phases 3-7 📋 Planned

This document provides implementation specifications for Phases 3-7. Each phase builds incrementally on previous work.

---

## Phase 3: Clipboard Integration

**Goal**: Make transcriptions immediately usable through clipboard operations

**Estimated Time**: 3-4 hours
**Complexity**: Medium (Wayland/X11 differences)
**Files to Create**: 3
**Dependencies**: System tools (wl-clipboard, xclip, ydotool)

### Deliverables

```
src/whisper_aloud/clipboard/
├── __init__.py              # Public API
├── clipboard_manager.py     # Copy operations
└── paste_simulator.py       # Keyboard simulation (Ctrl+V)
```

### Key Requirements

1. **Session Detection**: Auto-detect Wayland vs X11
2. **Clipboard Copy**:
   - Wayland: Use `wl-copy` command
   - X11: Use `xclip` command
   - Fallback: Write to `/tmp/whisper_aloud_clipboard.txt`
3. **Paste Simulation**:
   - Wayland: Use `ydotool key ctrl+v` (requires permissions)
   - X11: Use `xdotool key ctrl+v`
   - Detect permission issues and provide setup instructions
4. **Error Handling**: Clear messages when tools missing or permissions lacking

### Technical Specifications

```python
class ClipboardManager:
    """Cross-platform clipboard operations."""

    @staticmethod
    def detect_session_type() -> str:
        """Returns 'wayland', 'x11', or 'unknown'."""

    def copy(self, text: str) -> bool:
        """
        Copy text to clipboard.
        Returns True if successful, False if failed.
        Logs helpful error messages.
        """

    def paste(self, simulate_keypress: bool = True) -> bool:
        """
        Paste from clipboard.
        If simulate_keypress=True, sends Ctrl+V.
        Returns False if simulation not available.
        """

    def check_paste_permissions(self) -> dict:
        """
        Check if paste simulation is available.
        Returns: {
            'available': bool,
            'reason': str,  # Why not available
            'fix': str      # How to enable it
        }
        """

class PasteSimulator:
    """Keyboard input simulation."""

    def simulate_paste(self) -> bool:
        """Send Ctrl+V keystroke."""
```

### Integration with Existing Code

- Add `ClipboardConfig` to `config.py`:
  ```python
  @dataclass
  class ClipboardConfig:
      auto_copy: bool = True
      auto_paste: bool = False  # Default off (requires setup)
      paste_delay_ms: int = 100
      fallback_to_file: bool = True
  ```

- Add clipboard exceptions to `exceptions.py`:
  ```python
  class ClipboardError(WhisperAloudError):
      """Clipboard operation failed."""
  ```

### Testing Strategy

```python
# tests/test_clipboard.py
def test_session_detection():
    """Test Wayland/X11 detection."""

def test_copy_with_mock():
    """Test clipboard copy (mocked subprocess)."""

@pytest.mark.skipif(not has_clipboard_tools(), reason="No clipboard tools")
def test_real_clipboard_copy():
    """Integration test with real clipboard."""

def test_paste_permission_check():
    """Test permission checking logic."""
```

### User Setup Instructions

Add to README:
```markdown
## Clipboard Setup (Wayland)

For auto-paste functionality:

```bash
# Install tools
sudo apt install wl-clipboard ydotool

# Enable ydotool service
sudo systemctl enable --now ydotool.service

# Add user to input group
sudo usermod -aG input $USER

# IMPORTANT: Logout and login for group changes to take effect
```

Test: `whisper-aloud-ctl test-clipboard`
```

### Success Criteria

- ✅ Detects session type correctly
- ✅ Copies text to clipboard (both Wayland and X11)
- ✅ Simulates paste when permissions available
- ✅ Shows clear error messages with fix instructions
- ✅ Fallback mode works when tools missing
- ✅ Tests pass on both Wayland and X11 (integration tests optional)

---

## Phase 4: GTK4 GUI (MVP)

**Goal**: Visual interface for recording and transcription

**Estimated Time**: 6-8 hours
**Complexity**: Medium-High (GTK4 + threading)
**Files to Create**: 5
**Dependencies**: GTK4, PyGObject

### Deliverables

```
src/whisper_aloud/ui/
├── __init__.py              # Public API
├── main_window.py           # Main application window
├── recording_panel.py       # Recording controls + level meter
├── transcription_view.py    # Results display
└── settings_dialog.py       # Configuration UI
```

### Key Requirements

1. **Main Window**:
   - Start/Stop recording button (large, prominent)
   - Real-time audio level indicator (animated bar)
   - Status label (Ready/Recording/Transcribing)
   - Transcription result text view
   - Copy button
   - Settings button

2. **Recording Panel**:
   - Device selector dropdown
   - Level meter (green → yellow → red)
   - Recording duration timer
   - Peak/RMS indicator

3. **Settings Dialog**:
   - Model selection (tiny/base/small/medium/large-v3)
   - Language selection
   - Audio device selection
   - Clipboard options (auto-copy, auto-paste)
   - VAD threshold slider

4. **Threading Model**:
   - Recording runs in audio thread (existing)
   - Transcription runs in worker thread
   - UI updates via `GLib.idle_add()`
   - No blocking operations in main thread

### Architecture

```python
class MainWindow(Gtk.ApplicationWindow):
    """Main application window."""

    def __init__(self, app: Gtk.Application):
        # Initialize UI components
        # Connect signals
        # Setup recorder and transcriber

    def on_record_clicked(self, button):
        """Handle record/stop button."""
        if recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        """Start recording in background."""
        self.recorder.start()
        # Update UI state

    def stop_recording(self):
        """Stop recording and transcribe."""
        audio = self.recorder.stop()
        # Start transcription in worker thread
        threading.Thread(
            target=self._transcribe_worker,
            args=(audio,),
            daemon=True
        ).start()

    def _transcribe_worker(self, audio):
        """Transcribe in background (worker thread)."""
        result = self.transcriber.transcribe_numpy(audio)
        GLib.idle_add(self._on_transcription_complete, result)

    def _on_transcription_complete(self, result):
        """Handle transcription result (main thread)."""
        # Update UI with result
        # Copy to clipboard if enabled
        # Simulate paste if enabled

class RecordingPanel(Gtk.Box):
    """Recording controls and level meter."""

    def update_level(self, level: AudioLevel):
        """Update level meter (called from audio thread)."""
        GLib.idle_add(self._update_level_ui, level)

class SettingsDialog(Gtk.Dialog):
    """Settings configuration UI."""

    def get_config(self) -> WhisperAloudConfig:
        """Get configuration from UI."""
```

### Integration Points

- Extend `WhisperAloudConfig` with UI settings:
  ```python
  @dataclass
  class UIConfig:
      window_width: int = 500
      window_height: int = 400
      theme: str = "auto"  # auto, light, dark
      show_level_meter: bool = True
  ```

### Testing Strategy

Manual testing primarily (GTK4 hard to unit test):
```python
# tests/test_ui_integration.py
@pytest.mark.manual
def test_window_creation():
    """Test window can be created (manual)."""
    # Create app
    # Create window
    # Verify no crashes
```

### Success Criteria

- ✅ Window opens and displays correctly
- ✅ Record button starts recording
- ✅ Level meter animates in real-time
- ✅ Stop button triggers transcription
- ✅ Transcription result appears in text view
- ✅ Copy button copies to clipboard
- ✅ Settings dialog opens and saves config
- ✅ No UI freezing (all blocking ops in threads)
- ✅ Application closes cleanly

---

## Phase 5: Persistence Layer (History)

**Goal**: Never lose transcriptions

**Estimated Time**: 3-4 hours
**Complexity**: Low (SQLite basics)
**Files to Create**: 3
**Dependencies**: sqlite3 (built-in)

### Deliverables

```
src/whisper_aloud/storage/
├── __init__.py              # Public API
├── database.py              # SQLite operations
└── models.py                # Transcript dataclass
```

### Key Requirements

1. **Database Schema**:
   ```sql
   CREATE TABLE transcripts (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       timestamp TEXT NOT NULL,
       text TEXT NOT NULL,
       language TEXT,
       model TEXT,
       confidence REAL,
       duration_sec REAL,
       audio_file_path TEXT  -- Optional: keep recording
   );

   CREATE INDEX idx_timestamp ON transcripts(timestamp DESC);
   CREATE INDEX idx_text_fts ON transcripts(text);  -- Full-text search
   ```

2. **Database Operations**:
   - Save transcription with metadata
   - List recent transcriptions (paginated)
   - Search by text (full-text search)
   - Search by date range
   - Delete old entries (retention policy)
   - Export to JSON/CSV

3. **History Dialog** (UI integration):
   - List view of past transcriptions
   - Click to copy
   - Search box
   - Delete button
   - Export button

### Technical Specifications

```python
@dataclass
class Transcript:
    """Transcript data model."""
    id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    text: str = ""
    language: str = ""
    model: str = ""
    confidence: float = 0.0
    duration_sec: float = 0.0
    audio_file_path: Optional[str] = None

class Database:
    """SQLite database manager."""

    def __init__(self, db_path: str):
        """Initialize database connection."""

    def save_transcript(self, transcript: Transcript) -> int:
        """Save transcript, return ID."""

    def get_recent(self, limit: int = 50) -> List[Transcript]:
        """Get recent transcriptions."""

    def search(self, query: str) -> List[Transcript]:
        """Full-text search."""

    def delete_old(self, days: int) -> int:
        """Delete entries older than N days, return count."""

    def export_json(self, file_path: str) -> None:
        """Export all to JSON."""
```

### Integration with GUI

Add history dialog to `ui/`:
```python
class HistoryDialog(Gtk.Dialog):
    """Transcription history browser."""

    def __init__(self, parent, db: Database):
        # Create list view
        # Add search box
        # Connect signals

    def on_row_activated(self, listbox, row):
        """Copy transcript on click."""
```

### Success Criteria

- ✅ Database created on first run
- ✅ Transcriptions saved automatically
- ✅ History dialog shows past transcriptions
- ✅ Search works (finds text in history)
- ✅ Old entries deleted based on retention policy
- ✅ Export to JSON works
- ✅ No database corruption (use transactions)

---

## Phase 6: D-Bus Service (Daemon)

**Goal**: Background service controllable via keyboard shortcuts

**Estimated Time**: 4-5 hours
**Complexity**: High (IPC + service management)
**Files to Create**: 4
**Dependencies**: dbus-next

### Deliverables

```
src/whisper_aloud/daemon/
├── __init__.py              # Public API
├── service.py               # D-Bus service implementation
├── dbus_interface.py        # Method definitions
└── lifecycle.py             # Single-instance enforcement

scripts/
└── whisper-aloud-ctl        # CLI controller
```

### Key Requirements

1. **D-Bus Service**:
   - Service name: `org.whisperaloud.Dictation`
   - Object path: `/org/whisperaloud/Dictation`
   - Interface: `org.whisperaloud.Dictation`

2. **Methods**:
   ```python
   @method()
   def Toggle() -> 's':
       """Start/stop recording. Returns status."""

   @method()
   def GetStatus() -> 's':
       """Get current status (JSON string)."""

   @method()
   def ShowHistory() -> 's':
       """Open history dialog."""

   @method()
   def ShowSettings() -> 's':
       """Open settings dialog."""

   @method()
   def Quit() -> 's':
       """Shutdown daemon."""
   ```

3. **Single Instance**:
   - Use D-Bus name ownership
   - If daemon already running, control existing instance
   - Graceful shutdown on SIGTERM

4. **CLI Controller**:
   ```bash
   whisper-aloud-ctl toggle     # Toggle recording
   whisper-aloud-ctl status     # Show status
   whisper-aloud-ctl history    # Open history
   whisper-aloud-ctl quit       # Shutdown
   ```

### Architecture

```python
class DictationService(ServiceInterface):
    """D-Bus service interface."""

    def __init__(self, app: MainWindow):
        super().__init__("org.whisperaloud.Dictation")
        self.app = app

    @method()
    def Toggle(self) -> 's':
        GLib.idle_add(self.app.toggle_recording)
        return "ok"

class DaemonApplication(Gtk.Application):
    """Daemon application with D-Bus."""

    def do_startup(self):
        # Initialize D-Bus service
        # Register on session bus
        # Single-instance check

    def do_activate(self):
        # Show/hide window
        # Or just run in background
```

### Integration

- Modify `__main__.py` to support daemon mode:
  ```python
  parser.add_argument('--daemon', action='store_true', help='Run as daemon')

  if args.daemon:
      from .daemon import DaemonApplication
      app = DaemonApplication()
      app.run()
  ```

### Success Criteria

- ✅ Daemon starts and registers on D-Bus
- ✅ Only one instance runs at a time
- ✅ CLI controller can toggle recording
- ✅ Keyboard shortcut works (via controller)
- ✅ Daemon survives across recordings
- ✅ Graceful shutdown on quit

---

## Phase 7: GNOME Integration (Polish)

**Goal**: Feel like a native GNOME application

**Estimated Time**: 2-3 hours
**Complexity**: Low (mostly scripting)
**Files to Create**: 5
**Dependencies**: None (shell scripts + .desktop files)

### Deliverables

```
scripts/
├── setup_gnome_shortcut.py  # Register keyboard shortcut
├── install_autostart.sh     # Enable autostart
└── uninstall.sh             # Clean removal

data/
├── whisper-aloud.desktop    # Application launcher
└── whisper-aloud-autostart.desktop  # Autostart entry

docs/
└── GNOME_SETUP.md           # Setup guide
```

### Key Requirements

1. **Keyboard Shortcut**:
   - Default: `<Super><Alt>w`
   - Command: `whisper-aloud-ctl toggle`
   - Registered via gsettings

2. **Application Launcher**:
   - Shows in GNOME applications menu
   - Icon (optional: find/create icon)
   - Categories: Audio, Utility
   - Launches daemon if not running

3. **Autostart** (optional):
   - `~/.config/autostart/whisper-aloud.desktop`
   - Starts daemon on login
   - User-configurable

4. **Setup Script**:
   ```bash
   ./scripts/setup_gnome.sh
   # - Registers keyboard shortcut
   # - Installs .desktop file
   # - Optionally enables autostart
   # - Tests configuration
   ```

### Technical Specifications

```python
# setup_gnome_shortcut.py
import subprocess

SHORTCUT_NAME = "WhisperAloud Toggle"
SHORTCUT_COMMAND = "whisper-aloud-ctl toggle"
SHORTCUT_BINDING = "<Super><Alt>w"

def register_shortcut():
    """Register custom keyboard shortcut in GNOME."""
    # Use gsettings to add custom keybinding
    # Check for conflicts
    # Test that it works

def check_gnome_version():
    """Ensure GNOME 40+ (GTK4 requirement)."""
```

### Desktop File

```ini
[Desktop Entry]
Type=Application
Name=WhisperAloud
Comment=Voice dictation with Whisper AI
Exec=whisper-aloud --daemon
Icon=audio-input-microphone
Categories=Audio;Utility;Accessibility;
Keywords=dictation;speech;transcription;whisper;
StartupNotify=false
X-GNOME-Autostart-enabled=true
```

### Success Criteria

- ✅ Keyboard shortcut registered and working
- ✅ Appears in GNOME applications menu
- ✅ Can launch from menu
- ✅ Autostart works (if enabled)
- ✅ Setup script succeeds without errors
- ✅ Uninstall script removes all traces

---

## Phase Dependencies

```
Phase 1 (Transcription) ✅
    ↓
Phase 2 (Audio Recording) ← YOU ARE HERE
    ↓
Phase 3 (Clipboard)
    ↓
Phase 4 (GUI) ← MVP MILESTONE
    ↓
Phase 5 (History)
    ↓
Phase 6 (D-Bus Daemon)
    ↓
Phase 7 (GNOME Integration) ← COMPLETE MILESTONE
```

### MVP Definition (Phase 4 Complete)

At Phase 4, users can:
- ✅ Open GUI application
- ✅ Click button to record
- ✅ See audio levels in real-time
- ✅ Get transcription displayed
- ✅ Copy to clipboard

**This is fully usable!** Phases 5-7 add polish and convenience.

### Complete Feature Set (Phase 7 Complete)

At Phase 7, users get:
- ✅ All MVP features
- ✅ Persistent transcription history
- ✅ Searchable past transcriptions
- ✅ Keyboard shortcut (Super+Alt+W)
- ✅ Background daemon (no visible window)
- ✅ Autostart on login
- ✅ Native GNOME integration

**This is production-ready for daily use!**

---

## Prompting Strategy for Grok

### For Each Phase:

1. **Copy relevant section** from this document
2. **Reference previous phases**: "Phase X is complete, integrate with..."
3. **Specify dependencies**: List new packages to add
4. **Provide success criteria**: Clear checklist
5. **Request specific files**: Be explicit about what to create

### Example Prompt for Phase 3:

```
Based on WhisperAloud Phase 1 (transcription) and Phase 2 (recording) being complete,
implement Phase 3: Clipboard Integration.

Requirements:
- Create 3 files in src/whisper_aloud/clipboard/
- Support both Wayland and X11 session types
- Handle missing tools gracefully with setup instructions
- Add ClipboardConfig to existing config.py
- Add ClipboardError to existing exceptions.py

[Copy Phase 3 specifications from this document]

Success criteria:
- Clipboard copy works on Wayland and X11
- Paste simulation works when permissions available
- Clear error messages for missing tools
- Tests pass with mocked subprocess calls

Generate production-ready code following Phase 1 quality standards.
```

---

## Testing Strategy Per Phase

### Phase 3 (Clipboard)
- Unit tests with subprocess mocks
- Manual test on Wayland
- Manual test on X11 (if available)

### Phase 4 (GUI)
- Manual testing (GTK hard to unit test)
- Screenshot comparisons
- Threading safety checks

### Phase 5 (History)
- Unit tests for database operations
- Integration test: save and retrieve
- Test retention policy

### Phase 6 (D-Bus)
- Unit tests with mocked D-Bus
- Integration test: daemon starts and responds
- Test single-instance enforcement

### Phase 7 (GNOME)
- Manual setup script testing
- Verify keyboard shortcut works
- Test autostart

---

## Timeline Estimate

| Phase | Time | Cumulative | Milestone |
|-------|------|------------|-----------|
| 1 ✅ | 6h | 6h | Core engine |
| 2 📝 | 8h | 14h | Audio capture |
| 3 | 4h | 18h | Clipboard |
| 4 | 8h | 26h | **MVP GUI** |
| 5 | 4h | 30h | History |
| 6 | 5h | 35h | Daemon |
| 7 | 3h | 38h | **Complete** |

**Total: 38 hours** (about 1 week part-time, or 5 days full-time)

---

## Quality Checklist (All Phases)

For each phase, ensure:
- [ ] Type hints on all functions
- [ ] Docstrings (Google style)
- [ ] Error handling with custom exceptions
- [ ] Logging integration
- [ ] Unit tests (where applicable)
- [ ] Integration tests (optional)
- [ ] README updates
- [ ] No breaking changes to previous phases

---

## Final Delivery

After Phase 7, the project should have:
- **~2500 lines** of production code
- **~800 lines** of tests
- **Complete documentation**
- **Installation scripts**
- **Desktop integration**
- **Zero known bugs**

**Result**: A professional, production-ready voice dictation application for Linux.

---

**Next Step**: Use `PROMPT_PHASE_2_FOR_GROK.md` to implement Phase 2 (Audio Recording).
