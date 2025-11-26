# WhisperAloud Quick Start

Get up and running with WhisperAloud in 5 minutes.

## Prerequisites

- Debian 12+ or Ubuntu 22.04+
- Python 3.10+
- Microphone
- GTK4 (for GUI)

## Installation (4 commands)

```bash
# 1. Install system dependencies
sudo apt install -y portaudio19-dev libportaudio2 python3-venv python3-gi gir1.2-gtk-4.0

# 2. Create virtual environment with system packages (for GTK4)
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages

# 3. Activate and install WhisperAloud
source ~/.venvs/whisper_aloud/bin/activate
pip install -e .

# 4. Verify installation
whisper-aloud-gui --version
```

## First Use

### Option 1: GUI (Recommended)

Launch the graphical interface:

```bash
source ~/.venvs/whisper_aloud/bin/activate
whisper-aloud-gui
```

**Features:**
- Click "Start Recording" or press Space
- Speak into your microphone
- Click "Stop Recording" (or Space again)
- Transcription appears automatically
- Click "Copy to Clipboard" or press Ctrl+C

**Note:** First launch downloads the model (~150MB), takes 1-2 minutes.

### Option 2: Command Line

Quick recording and transcription:

```bash
source ~/.venvs/whisper_aloud/bin/activate
python examples/demo_quick.py 5
```

Output:
```
🎤 Recording for 5 seconds...
   SAY SOMETHING NOW!
   5...4...3...2...1...

✅ Recording complete (80000 samples)

🤖 Transcribing...
   ✅ Transcription completed in 2.3 seconds

================================================================================
  RESULT
================================================================================

📝 Text: "Hello, this is a test of WhisperAloud"

🌐 Language: en
🎯 Transcription confidence: 94.2%
⏱️  Duration: 5.00 seconds
📊 Segments: 1
```

### Option 3: Python API

```python
from whisper_aloud import WhisperAloudConfig, Transcriber
from whisper_aloud.audio import AudioRecorder
import time

# Setup
config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)

# Record
print("🎤 Speak now...")
recorder.start()
time.sleep(5)
audio = recorder.stop()

# Transcribe
result = transcriber.transcribe_numpy(audio, sample_rate=16000)
print(f"📝 {result.text}")
print(f"🎯 Confidence: {result.confidence:.1%}")
```

## Configuration

WhisperAloud uses JSON configuration at `~/.config/whisper_aloud/config.json`.

### Via GUI Settings Dialog

1. Click the settings icon (⚙️) in the GUI
2. Adjust settings:
   - **Model:** tiny/base/small/medium/large
   - **Language:** Leave blank for auto-detect or use code (en, es, etc.)
   - **Device:** CPU or CUDA (GPU)
   - **Audio:** Select microphone, sample rate, VAD settings
   - **Clipboard:** Auto-copy, auto-paste options
3. Click "Save"

### Via Environment Variables

```bash
# Model settings
export WHISPER_ALOUD_MODEL_NAME=base
export WHISPER_ALOUD_MODEL_DEVICE=cpu
export WHISPER_ALOUD_LANGUAGE=en

# Audio settings
export WHISPER_ALOUD_SAMPLE_RATE=16000
export WHISPER_ALOUD_VAD_ENABLED=true
```

### Via JSON File

Create `~/.config/whisper_aloud/config.json`:

```json
{
  "model": {
    "name": "base",
    "device": "cpu"
  },
  "transcription": {
    "language": "en"
  },
  "audio": {
    "sample_rate": 16000,
    "vad_enabled": true,
    "normalize_audio": true
  },
  "clipboard": {
    "auto_copy": true,
    "auto_paste": false
  }
}
```

## Model Selection

| Model | Size | Speed | RAM | Use Case |
|-------|------|-------|-----|----------|
| tiny  | 75MB | ⚡⚡⚡ | 1GB | Testing, low-resource |
| **base** | 145MB | ⚡⚡ | 1GB | **Recommended** |
| small | 466MB | ⚡ | 2GB | Better accuracy |
| medium | 1.5GB | 🐌 | 5GB | High accuracy |
| large | 3GB | 🐌🐌 | 10GB | Maximum accuracy |

**Default:** base (best balance of speed/accuracy)

## Common Issues

### "PortAudio library not found"
```bash
sudo apt install -y portaudio19-dev libportaudio2
```

### "No module named 'gi'" (GUI)
```bash
# Recreate venv with system packages
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages --upgrade
source ~/.venvs/whisper_aloud/bin/activate
pip install -e .
```

### "Permission denied" for audio device
```bash
sudo usermod -aG audio $USER
# Log out and back in
```

### Model download fails
- Check internet connection
- Models cache in `~/.cache/huggingface/`
- Try smaller model: `export WHISPER_ALOUD_MODEL_NAME=tiny`

### Transcription is slow
- First run downloads model (1-2 min)
- Use GPU if available: Settings → Device → CUDA
- Use smaller model: Settings → Model → tiny/base

## Keyboard Shortcuts (GUI)

- **Space:** Start/stop recording
- **Ctrl+C:** Copy to clipboard
- **Escape:** Clear transcription
- **Ctrl+Q:** Quit application

## Examples

See [examples/](examples/) directory for more:

- `demo_gui.py` - GUI application
- `demo_quick.py` - CLI recording and transcription
- `demo_realtime_levels.py` - Real-time audio level monitoring
- `demo_clipboard.py` - Clipboard integration testing

## System Validation

Verify your setup:

```bash
python scripts/validate_system.py
```

This checks:
- ✓ Python version
- ✓ System dependencies
- ✓ Audio devices
- ✓ Clipboard tools
- ✓ Permissions
- ✓ WhisperAloud installation

## Next Steps

- **Detailed installation:** [INSTALL.md](INSTALL.md)
- **Full documentation:** [README.md](README.md)
- **Troubleshooting:** [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Examples:** [examples/](examples/)

## Getting Help

**Installation issues?** → [INSTALL.md#troubleshooting](INSTALL.md#troubleshooting)

**Audio problems?** → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

**Questions?** → GitHub Issues

---

**You're ready!** The model will download automatically on first use (~150MB for base model).
