# WhisperAloud - Real Implementation Plan

## Critical Analysis of Original "Whispy" Plan

### ✅ Strengths
1. **Good architecture concept**: D-Bus daemon + CLI controller is solid for GNOME integration
2. **Wayland awareness**: Acknowledges X11/Wayland differences (critical for Debian 12)
3. **faster-whisper choice**: Smart - better than openai-whisper for CPU/GPU performance
4. **SQLite history**: Simple, reliable, no external dependencies
5. **Quality focus**: Default to `large-v3` model for accuracy

### ❌ Critical Issues

#### 1. **Monolithic Design** (🔴 HIGH RISK)
- 430+ lines in single file = maintenance nightmare
- No separation of concerns (audio/UI/transcription/DB all mixed)
- Impossible to test individual components
- **Impact**: Hard to debug, modify, or extend

#### 2. **Fragile GTK4+asyncio Integration** (🔴 HIGH RISK)
```python
async def pump():
    while True:
        _glib_iteration()
        await asyncio.sleep(0.02)  # Manual event loop integration
```
- This is a hack that can cause race conditions
- GTK4 has native async support via `GLib.MainLoop`
- **Impact**: Potential freezes, crashes, or missed events

#### 3. **No Incremental Development Path** (🟡 MEDIUM RISK)
- Jumps directly to full daemon + GUI + D-Bus + keyboard shortcuts
- No way to test individual pieces (audio, transcription, clipboard)
- If one component fails, everything fails
- **Impact**: Long development cycle with late-stage integration issues

#### 4. **Missing Error Handling** (🟡 MEDIUM RISK)
- Audio device conflicts not handled (multiple apps using mic)
- Model download failures not considered
- GPU/CPU fallback not implemented
- **Impact**: Silent failures or cryptic crashes

#### 5. **System Dependencies Assumed** (🟡 MEDIUM RISK)
```bash
# Requires: wl-clipboard, ydotool, portaudio, ffmpeg
# What if missing? Script doesn't check.
```
- No runtime checks for required tools
- `ydotool` requires systemd service + special permissions
- **Impact**: Confusing errors for users

#### 6. **Wayland Paste Limitations** (🟠 KNOWN ISSUE)
```python
subprocess.run(["ydotool", "key", "ctrl+v"])  # Requires root or special group
```
- `ydotool` needs uinput permissions (`/dev/uinput`)
- User must be in `input` group OR run daemon with elevated privileges
- Security implications not mentioned
- **Impact**: Main feature (auto-paste) won't work for most users initially

#### 7. **Configuration Management** (🟢 LOW PRIORITY)
- All configuration via environment variables
- No config file support
- Hard to switch between profiles (different languages/models)

#### 8. **No Testing Strategy** (🟢 LOW PRIORITY)
- No unit tests mentioned
- No integration tests
- Manual testing only

---

## Verified System State (Debian 12 + Python 3.13)

```
✅ Python 3.13.5 (exceeds 3.10+ requirement)
✅ Wayland session (primary display server)
✅ FFmpeg 7.1.2 installed
❌ PortAudio not installed
❌ wl-clipboard not installed
❌ ydotool not installed
```

---

## Revised Implementation Strategy

### Philosophy: **Incremental + Testable + Modular**

Build in 7 phases, each fully functional and testable:

---

## Phase 1: Core Transcription Engine (Foundation)
**Goal**: Prove Whisper works on your system
**Deliverable**: CLI tool that transcribes audio files

```
whisper_aloud/
├── src/
│   └── whisper_aloud/
│       ├── __init__.py
│       ├── transcriber.py      # Whisper model wrapper
│       └── config.py            # Settings management
├── tests/
│   └── test_transcriber.py
├── requirements.txt
├── pyproject.toml
└── README.md
```

**Features**:
- Load faster-whisper model with error handling
- Transcribe audio file → text
- Support model/language/beam size configuration
- GPU detection + CPU fallback
- Graceful model download

**Test**: `python -m whisper_aloud.transcriber sample.wav`

**Dependencies**: `faster-whisper`, `numpy`

---

## Phase 2: Audio Recording (Input)
**Goal**: Capture microphone input reliably
**Deliverable**: CLI tool that records audio on demand

**Add**:
```
src/whisper_aloud/
├── audio/
│   ├── __init__.py
│   ├── recorder.py              # Microphone capture
│   ├── device_manager.py        # List/select audio devices
│   └── audio_utils.py           # Format conversions, VAD
```

**Features**:
- List available audio input devices
- Record with start/stop
- Real-time RMS level monitoring (for UI feedback)
- WAV/numpy format export
- Handle device errors gracefully

**Test**: `python -m whisper_aloud record --duration 5 output.wav`

**Dependencies**: `sounddevice` (uses PortAudio), `scipy`

---

## Phase 3: Clipboard Integration (Output)
**Goal**: Make transcriptions usable immediately
**Deliverable**: Reliable clipboard operations

**Add**:
```
src/whisper_aloud/
├── clipboard/
│   ├── __init__.py
│   ├── clipboard_manager.py    # Wayland/X11 abstraction
│   └── paste_simulator.py      # ydotool/xdotool wrapper
```

**Features**:
- Detect session type (Wayland/X11)
- Copy text to clipboard (wl-clipboard/xclip)
- Simulate Ctrl+V paste (with permission checks)
- Fallback modes if paste unavailable
- Clear error messages for missing dependencies

**Test**: `python -m whisper_aloud.clipboard copy "Test text"`

**Dependencies**: System tools (wl-clipboard, ydotool)

**Wayland Setup**:
```bash
# Install tools
sudo apt install wl-clipboard ydotool

# Enable ydotool service
sudo systemctl enable --now ydotool.service

# Add user to input group (for /dev/uinput access)
sudo usermod -aG input $USER
# Logout/login required
```

---

## Phase 4: Simple GTK4 UI (User Experience)
**Goal**: Visual feedback during recording/transcription
**Deliverable**: Standalone GUI app

**Add**:
```
src/whisper_aloud/
├── ui/
│   ├── __init__.py
│   ├── main_window.py           # Recording UI + status
│   ├── history_dialog.py        # Transcription history
│   └── settings_dialog.py       # Config UI
```

**Features**:
- Start/Stop recording button
- Real-time audio level indicator
- Transcription progress (loading model, processing)
- Display result text
- Copy button
- Status messages

**Test**: `python -m whisper_aloud.ui`

**Dependencies**: `PyGObject` (GTK4 bindings)

**Architecture**: Use `GLib.MainLoop` (not asyncio hacks)

---

## Phase 5: Persistence Layer (History)
**Goal**: Never lose transcriptions
**Deliverable**: SQLite database + search

**Add**:
```
src/whisper_aloud/
├── storage/
│   ├── __init__.py
│   ├── database.py              # SQLite operations
│   └── models.py                # Transcript dataclass
```

**Features**:
- Save all transcriptions with metadata
- Search history by text/date
- Export to JSON/CSV
- Prune old entries (configurable retention)

**Location**: `~/.local/share/whisper_aloud/transcripts.db`

**Schema**:
```sql
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    text TEXT NOT NULL,
    language TEXT,
    model TEXT,
    confidence REAL,
    duration_sec REAL,
    audio_file TEXT  -- Optional: keep recording
);
```

---

## Phase 6: D-Bus Service (System Integration)
**Goal**: Control from keyboard shortcuts/other apps
**Deliverable**: Background daemon + CLI controller

**Add**:
```
src/whisper_aloud/
├── daemon/
│   ├── __init__.py
│   ├── service.py               # D-Bus service
│   ├── dbus_interface.py        # Method definitions
│   └── lifecycle.py             # Start/stop/restart
```

**Features**:
- Single-instance daemon (prevent multiple)
- D-Bus methods: `Toggle()`, `Start()`, `Stop()`, `GetStatus()`
- Auto-restart on crash (systemd integration)
- Graceful shutdown (save state)

**Service**: `org.whisperaloud.Dictation` @ `/org/whisperaloud/Dictation`

**CLI Controller**:
```bash
whisper-aloud-ctl toggle          # Start/stop recording
whisper-aloud-ctl status           # Is recording?
whisper-aloud-ctl history          # Open history UI
whisper-aloud-ctl settings         # Open settings
```

**Dependencies**: `dbus-next` (pure Python D-Bus)

---

## Phase 7: GNOME Integration (Polish)
**Goal**: Feel like a native GNOME app
**Deliverable**: Keyboard shortcut + autostart

**Add**:
```
scripts/
├── setup_gnome_shortcut.py      # Programmatic gsettings
├── install_autostart.sh         # Create .desktop file
└── uninstall.sh                 # Clean removal
```

**Features**:
- Register custom keyboard shortcut (default: `<Super><Alt>w`)
- Show in GNOME applications menu
- Autostart daemon on login (optional)
- System tray indicator (if using appindicator)

**Autostart**: `~/.config/autostart/whisper-aloud.desktop`

---

## Development Roadmap

| Phase | Estimated Time | Complexity | Testable? |
|-------|---------------|------------|-----------|
| 1 - Transcription | 4-6 hours | Medium | ✅ Yes |
| 2 - Recording | 3-4 hours | Low | ✅ Yes |
| 3 - Clipboard | 2-3 hours | Medium | ✅ Yes |
| 4 - GUI | 6-8 hours | Medium | ✅ Yes |
| 5 - Database | 2-3 hours | Low | ✅ Yes |
| 6 - D-Bus | 4-5 hours | High | ⚠️ Manual |
| 7 - GNOME | 2-3 hours | Low | ⚠️ Manual |
| **Total** | **23-32 hours** | | |

---

## Dependency Management

### Core Python Packages (`requirements.txt`)
```txt
# Transcription
faster-whisper>=1.1.0
numpy>=1.24.0

# Audio
sounddevice>=0.4.6
scipy>=1.10.0

# UI
PyGObject>=3.42.0

# D-Bus
dbus-next>=0.2.3

# Utilities
tomli>=2.0.1; python_version < '3.11'  # Config files
```

### System Packages (Debian 12)
```bash
# Audio
sudo apt install python3-dev portaudio19-dev

# GTK4
sudo apt install python3-gi gir1.2-gtk-4.0 libgirepository1.0-dev

# Clipboard (Wayland)
sudo apt install wl-clipboard

# Paste automation (Wayland)
sudo apt install ydotool
sudo systemctl enable --now ydotool.service
sudo usermod -aG input $USER  # Requires logout

# Build tools
sudo apt install build-essential pkg-config
```

---

## Configuration Design

### File: `~/.config/whisper_aloud/config.toml`

```toml
[model]
name = "large-v3"           # or large-v3-turbo, medium, small
device = "auto"             # auto, cpu, cuda
compute_type = "int8"       # int8, float16, float32

[audio]
sample_rate = 16000
channels = 1
vad_enabled = true          # Voice Activity Detection

[transcription]
language = "es"             # or "auto" for detection
beam_size = 5
task = "transcribe"         # or "translate"

[clipboard]
auto_paste = true           # Simulate Ctrl+V after copy
paste_delay_ms = 100        # Wait before pasting

[storage]
keep_audio = false          # Save recordings
retention_days = 90         # Delete older than X days
max_entries = 1000

[ui]
window_width = 420
window_height = 220
show_level_meter = true
theme = "auto"              # auto, light, dark

[shortcuts]
toggle = "<Super><Alt>w"
```

---

## Testing Strategy

### Unit Tests
```python
# tests/test_transcriber.py
def test_model_loading():
    t = Transcriber(model="base")  # Fast for testing
    assert t.model is not None

def test_transcribe_file():
    t = Transcriber()
    result = t.transcribe("tests/fixtures/sample.wav")
    assert len(result.text) > 0
```

### Integration Tests
```python
# tests/integration/test_recording_pipeline.py
def test_record_and_transcribe():
    recorder = Recorder()
    audio = recorder.record(duration=3)

    transcriber = Transcriber()
    result = transcriber.transcribe_audio(audio)

    clipboard = ClipboardManager()
    clipboard.copy(result.text)
    assert clipboard.get() == result.text
```

### Manual Testing Checklist
- [ ] Record 10s audio → transcribes correctly
- [ ] Switch languages mid-session
- [ ] Test on GPU (if available)
- [ ] Test on CPU (fallback)
- [ ] Clipboard copy works
- [ ] Paste simulation works (Wayland)
- [ ] History saves and loads
- [ ] Keyboard shortcut triggers recording
- [ ] Multiple rapid toggles (stress test)
- [ ] Model download on first run

---

## Risk Mitigation

### 1. **Whisper Model Download** (First Run)
**Risk**: 3GB+ model downloads can timeout/fail
**Solution**:
- Show progress bar during download
- Resume partial downloads
- Offer smaller models as fallback
- Pre-download script for offline setup

### 2. **ydotool Permissions** (Wayland Auto-Paste)
**Risk**: Most users won't have correct permissions
**Solution**:
- Detect permission issues at startup
- Show clear setup instructions in UI
- Offer "manual paste" mode as default
- Auto-paste is opt-in feature

### 3. **Audio Device Conflicts**
**Risk**: Mic in use by another app (Zoom, Discord, etc.)
**Solution**:
- List available devices at startup
- Allow device selection in UI
- Retry logic with exponential backoff
- Clear error messages

### 4. **GPU Detection False Positives**
**Risk**: Detects GPU but CTranslate2 not compiled with CUDA
**Solution**:
- Test GPU with dummy inference at startup
- Fallback to CPU if GPU test fails
- Allow manual device override in config

---

## Key Differences from Original Plan

| Aspect | Original (Whispy) | This Plan (WhisperAloud) |
|--------|-------------------|--------------------------|
| **Architecture** | Monolithic 430-line file | Modular package (7+ modules) |
| **GTK+asyncio** | Manual event pump (fragile) | Native GLib.MainLoop |
| **Development** | Big bang (all at once) | 7 incremental phases |
| **Testing** | Not mentioned | Unit + integration tests |
| **Dependencies** | Assumes installed | Runtime checks + setup docs |
| **Configuration** | Env vars only | TOML config file + env vars |
| **Error Handling** | Minimal | Comprehensive with user feedback |
| **Wayland Paste** | Assumes works | Detect + fallback + setup guide |
| **Distribution** | Copy files manually | Proper Python package (pip) |

---

## Next Steps

### Immediate Actions (Phase 1)
1. **Create project structure**:
   ```bash
   mkdir -p src/whisper_aloud/{audio,clipboard,ui,storage,daemon}
   touch src/whisper_aloud/{__init__.py,config.py,transcriber.py}
   ```

2. **Setup development environment**:
   ```bash
   python3 -m venv ~/.venvs/whisper_aloud
   source ~/.venvs/whisper_aloud/bin/activate
   pip install --upgrade pip wheel setuptools
   ```

3. **Create `pyproject.toml`** (modern Python packaging)

4. **Install system dependencies**:
   ```bash
   sudo apt update
   sudo apt install -y python3-dev portaudio19-dev build-essential
   ```

5. **Test basic Whisper transcription**:
   - Install `faster-whisper`
   - Transcribe a test audio file
   - Verify model downloads correctly

### Phase Transitions
- Complete each phase fully before moving to next
- Write tests for each component
- Update documentation continuously
- Tag git releases after each phase

---

## Success Criteria

### Minimum Viable Product (MVP) = Phase 4
- Record audio via GUI button
- Transcribe with Whisper
- Copy result to clipboard
- Show result in window
- **User can dictate text without command line**

### Full Feature Parity = Phase 7
- Keyboard shortcut toggles recording
- Auto-paste to active window
- Persistent history with search
- Runs as background daemon
- Autostart on login

### Quality Metrics
- [ ] Transcription accuracy >95% (Spanish)
- [ ] Record → Transcribe → Paste <5 seconds (10s audio, large-v3-turbo)
- [ ] No crashes during 100 consecutive recordings
- [ ] Works on both Wayland and X11
- [ ] Clear error messages for all failures

---

## Conclusion

The original "Whispy" plan has the **right vision** but **wrong execution strategy**. By:
1. Breaking into incremental phases
2. Separating concerns into modules
3. Testing each component independently
4. Handling errors explicitly
5. Documenting system requirements clearly

We transform a **risky monolithic approach** into a **systematic development process** that:
- ✅ Can be tested at every step
- ✅ Fails fast with clear errors
- ✅ Is maintainable long-term
- ✅ Adapts to your Debian 12 + Wayland system
- ✅ Provides user value early (MVP at Phase 4)

**Recommendation**: Start with Phase 1 (transcription engine) and validate Whisper works on your hardware before investing in GUI/D-Bus complexity.
