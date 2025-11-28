# WhisperAloud - Voice Dictation for Linux

Offline voice transcription using OpenAI's Whisper model, optimized for Linux/GNOME.

## Phase 1: Core Transcription Engine ✅ COMPLETE

This phase implements the foundational transcription capabilities with lazy model loading, robust error handling, and CLI interface.

## Phase 2: Audio Recording Module ✅ COMPLETE

This phase adds professional audio recording capabilities with real-time level monitoring, voice activity detection, and seamless integration with Phase 1 transcription.

## Phase 3: Clipboard Integration ✅ COMPLETE

This phase adds clipboard integration to make transcriptions immediately usable. Features include automatic clipboard copy, paste simulation (Wayland/X11), and reliable file fallback.

## Phase 4: GTK4 GUI ✅ COMPLETE

A graphical user interface built with GTK4 for easy voice recording and transcription.

**Features:**
- ✅ Application window with state management
- ✅ Audio recording with timer and visual feedback
- ✅ Real-time audio level meter
- ✅ Automatic transcription on recording stop
- ✅ Copy to clipboard with auto-copy option
- ✅ Settings dialog for configuration
- ✅ Keyboard shortcuts (Space, Ctrl+C, Escape)
- ✅ Comprehensive error handling with recovery options

## Phase 5: Persistence Layer ✅ COMPLETE

A complete persistence layer for storing transcription history with full-text search and optional audio archiving.

**Features:**
- ✅ SQLite database with FTS5 full-text search
- ✅ Sidebar history panel with search and filtering
- ✅ Optional audio archiving (FLAC format) with deduplication
- ✅ Export to JSON, Markdown, CSV, and Text
- ✅ Auto-cleanup of old entries and orphaned audio files
- ✅ Favorites and tagging support
- ✅ Non-blocking background operations

## Phase 6: D-Bus Service (Daemon Mode) ✅ COMPLETE

A D-Bus service that allows WhisperAloud to run as a background daemon, enabling control from CLI, GUI, or system components like global shortcuts.

**Features:**
- ✅ D-Bus interface for remote control (`org.fede.whisperAloud`)
- ✅ Background daemon mode with `whisper-aloud --daemon`
- ✅ CLI client commands: `start`, `stop`, `status`, `toggle`, `quit`
- ✅ Non-blocking transcription with signal-based completion
- ✅ Thread-safe state management
- ✅ Integration with existing AudioRecorder and Transcriber components

### Installation

**Automated Install (Recommended):**
```bash
# Clone repository
git clone https://github.com/your-org/whisperAloud.git
cd whisperAloud

# Run installer (handles all dependencies)
./install.sh

# Or with development tools
./install.sh --dev
```

**Manual Install:**
```bash
# 1. Install ALL system dependencies (Debian/Ubuntu)
sudo apt install -y \
    portaudio19-dev libportaudio2 \
    python3-venv python3-dev \
    python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 \
    gir1.2-gsound-1.0 \
    wl-clipboard xclip

# 2. Create virtual environment WITH system package access
#    (Required for GTK4 bindings)
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages
source ~/.venvs/whisper_aloud/bin/activate

# 3. Install WhisperAloud
pip install -e .

# 4. Verify installation
python scripts/check_dependencies.py
```

**System Dependencies Reference:**

| Package | Purpose | Required |
|---------|---------|----------|
| `portaudio19-dev` | Audio recording | Yes |
| `python3-gi` | GObject bindings | Yes |
| `gir1.2-gtk-4.0` | GTK4 UI | Yes |
| `gir1.2-adw-1` | Adwaita theming | Yes |
| `gir1.2-gsound-1.0` | Sound feedback | No |
| `wl-clipboard` | Wayland clipboard | No* |
| `xclip` | X11 clipboard | No* |
| `ydotool` | Paste simulation | No |

*One clipboard tool required for copy functionality.

**For detailed installation instructions, see [INSTALL.md](INSTALL.md).**
**For complete dependency reference, see [DEPENDENCIES.md](DEPENDENCIES.md).**

**Development installation:**
```bash
pip install -e ".[dev]"
```

### Usage

**Graphical Interface (Recommended)**:
```bash
# Launch GUI application
whisper-aloud-gui

# Or using demo script
python demo_gui.py
```

The GUI provides:
- One-click recording with visual timer
- Automatic transcription when you stop recording
- Copy to clipboard (manual or automatic)
- Clean, simple interface

**Command Line**:

**File Transcription (Legacy)**:
```bash
# Transcribe audio file
whisper-aloud audio.wav

# Specify model and language
whisper-aloud audio.wav --model medium --language en

# Verbose output
whisper-aloud audio.wav --verbose
```

**Daemon Mode**:
```bash
# Start background daemon
whisper-aloud --daemon

# Control running daemon (in another terminal)
whisper-aloud start    # Start recording
whisper-aloud stop     # Stop recording and transcribe
whisper-aloud status   # Show current status
whisper-aloud toggle   # Toggle recording state
whisper-aloud quit     # Stop the daemon
```

**Testing the Daemon**:
```bash
# Run automated daemon test
python scripts/test_daemon.py
```

**Python API**:
```python
from whisper_aloud import Transcriber, WhisperAloudConfig

# Create configuration
config = WhisperAloudConfig.load()
config.model.name = "base"
config.transcription.language = "es"

# Initialize transcriber
transcriber = Transcriber(config)

# Transcribe file
result = transcriber.transcribe_file("audio.wav")
print(result.text)
print(f"Confidence: {result.confidence:.2%}")
```

## Audio Recording

WhisperAloud includes a professional audio recording subsystem for capturing microphone input.

### Device Management

```python
from whisper_aloud.audio import DeviceManager

# List all available input devices
devices = DeviceManager.list_input_devices()
for device in devices:
    print(f"ID {device.id}: {device.name} ({device.channels}ch, {device.sample_rate}Hz)")
    if device.is_default:
        print("  [DEFAULT]")

# Get default device
default_device = DeviceManager.get_default_input_device()
```

### Recording Audio

```python
from whisper_aloud.audio import AudioRecorder
from whisper_aloud import WhisperAloudConfig

# Load configuration
config = WhisperAloudConfig.load()

# Create recorder with real-time level monitoring
def level_callback(level):
    print(f"RMS: {level.rms:.2f}, Peak: {level.peak:.2f}, dB: {level.db:.1f}")

recorder = AudioRecorder(config.audio, level_callback=level_callback)

# Start recording
print("Recording... Press Ctrl+C to stop")
recorder.start()

# Wait for user input or implement your own stop condition
input("Press Enter to stop recording")

# Stop and get processed audio
audio = recorder.stop()
print(f"Recorded {len(audio) / 16000:.2f} seconds of audio")

# Audio is now ready for transcription with Phase 1
result = transcriber.transcribe_numpy(audio, sample_rate=16000)
print(f"Transcription: {result.text}")
```

### Complete Voice Dictation Workflow

```python
from whisper_aloud import WhisperAloudConfig, Transcriber
from whisper_aloud.audio import AudioRecorder

# Setup
config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)

# Record
print("🎤 Speak now...")
recorder.start()
time.sleep(5)  # Record for 5 seconds
audio = recorder.stop()

# Transcribe
result = transcriber.transcribe_numpy(audio, sample_rate=16000)
print(f"📝 {result.text}")
print(f"🎯 Confidence: {result.confidence:.1%}")
```

## Clipboard Integration

WhisperAloud includes clipboard integration for immediate text usage with automatic fallback.

### Basic Usage

```python
from whisper_aloud import WhisperAloudConfig, ClipboardManager

# Load configuration
config = WhisperAloudConfig.load()
clipboard = ClipboardManager(config.clipboard)

# Copy text to clipboard
clipboard.copy("Transcribed text here")
```

### Complete Workflow with Clipboard

```python
from whisper_aloud import WhisperAloudConfig, Transcriber, ClipboardManager
from whisper_aloud.audio import AudioRecorder
import time

# Setup
config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)
clipboard = ClipboardManager(config.clipboard)

# Record
print("🎤 Speak now...")
recorder.start()
time.sleep(5)
audio = recorder.stop()

# Transcribe
result = transcriber.transcribe_numpy(audio, sample_rate=16000)
print(f"📝 {result.text}")

# Copy to clipboard
if clipboard.copy(result.text):
    print("📋 Copied to clipboard!")
```

### Session Detection

WhisperAloud automatically detects your display server:

- **Wayland**: Uses `wl-copy` for clipboard, `ydotool` for paste simulation
- **X11**: Uses `xclip` for clipboard, `xdotool` for paste simulation
- **Fallback**: Always writes to `/tmp/whisper_aloud_clipboard.txt`

### Paste Simulation

Check if paste simulation is available:

```python
status = clipboard.check_paste_permissions()

if status['available']:
    print("✅ Paste simulation ready!")
    # Simulate Ctrl+V paste
    from whisper_aloud.clipboard import PasteSimulator
    simulator = PasteSimulator(clipboard._session_type, config.clipboard)
    simulator.simulate_paste()
else:
    print(f"❌ Not available: {status['reason']}")
    print(f"💡 Fix: {status['fix']}")
```

### Setup for Auto-Paste (Wayland)

For paste simulation on Wayland, install required tools and configure permissions:

```bash
# Install clipboard tools
sudo apt install wl-clipboard ydotool

# Enable ydotool service
sudo systemctl enable --now ydotool.service

# Add user to input group (required for ydotool)
sudo usermod -aG input $USER

# IMPORTANT: Logout and login for group changes to take effect
```

### Setup for Auto-Paste (X11)

For X11 systems:

```bash
# Install clipboard tools
sudo apt install xclip xdotool
```

### Clipboard Configuration

Configure via environment variables:

```bash
# Clipboard settings
export WHISPER_ALOUD_CLIPBOARD_AUTO_COPY=true    # Auto-copy transcriptions
export WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE=true   # Auto-paste if available
export WHISPER_ALOUD_CLIPBOARD_PASTE_DELAY_MS=100  # Delay before paste
export WHISPER_ALOUD_CLIPBOARD_TIMEOUT_SECONDS=5.0  # Command timeout
export WHISPER_ALOUD_CLIPBOARD_FALLBACK_PATH=/tmp/whisper_aloud_clipboard.txt
```

### Fallback Mechanism

If clipboard tools are not installed, WhisperAloud **always** saves text to a fallback file:

```bash
# View fallback file
cat /tmp/whisper_aloud_clipboard.txt

# Monitor for new transcriptions
tail -f /tmp/whisper_aloud_clipboard.txt
```

This ensures transcriptions are never lost, even without clipboard tools installed.

### Demo

Test clipboard functionality:

```bash
python demo_clipboard.py
```

This will:
- Detect your session type (Wayland/X11)
- Test clipboard copy
- Check paste simulation availability
- Show setup instructions if needed

### Configuration

Via environment variables:
```bash
# Model settings
export WHISPER_ALOUD_MODEL_NAME=medium
export WHISPER_ALOUD_MODEL_DEVICE=cuda
export WHISPER_ALOUD_LANGUAGE=en

# Audio recording settings
export WHISPER_ALOUD_SAMPLE_RATE=16000
export WHISPER_ALOUD_DEVICE_ID=1              # Use specific device
export WHISPER_ALOUD_VAD_ENABLED=true         # Voice activity detection
export WHISPER_ALOUD_VAD_THRESHOLD=0.02       # VAD sensitivity
export WHISPER_ALOUD_NORMALIZE_AUDIO=true     # Audio normalization
export WHISPER_ALOUD_MAX_RECORDING_DURATION=300  # 5 minutes max
```

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=whisper_aloud --cov-report=html
```

### Troubleshooting

**PortAudio library not found**:
```bash
sudo apt install -y portaudio19-dev libportaudio2
```

**Model download fails**:
- Check internet connection
- Models are cached in `~/.cache/huggingface/`
- Try smaller model first: `--model base`

**GPU not detected**:
- Ensure NVIDIA drivers installed: `nvidia-smi`
- Install CUDA-enabled faster-whisper build
- Fallback to CPU with `--device cpu`

**For more detailed troubleshooting, see [INSTALL.md](INSTALL.md#troubleshooting).**

### Next Steps

- ✅ Phase 1: Core transcription engine
- ✅ Phase 2: Audio recording module
- ✅ Phase 3: Clipboard integration
- ✅ Phase 4: GTK4 GUI (Complete with settings, level meter, error handling)
- ✅ Phase 5: Persistence layer (history, search, audio archiving)
- ✅ Phase 6: D-Bus service (daemon mode)
- ✅ Phase 7: GNOME integration
