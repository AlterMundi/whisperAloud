# WhisperAloud Examples

This directory contains example scripts demonstrating different features of WhisperAloud.

## Quick Start Examples

### 1. GUI Application Demo

**File:** `demo_gui.py`

Launch the GTK4 graphical interface:

```bash
python examples/demo_gui.py
```

Features demonstrated:
- Visual recording interface with timer
- Real-time audio level meter
- Automatic transcription on stop
- Copy to clipboard
- Settings dialog

**Best for:** Desktop users, visual workflow

---

### 2. Quick CLI Demo

**File:** `demo_quick.py`

Record and transcribe from command line:

```bash
# Record for 3 seconds (default)
python examples/demo_quick.py

# Record for 5 seconds
python examples/demo_quick.py 5
```

Features demonstrated:
- Command-line recording
- Countdown timer
- Transcription with detailed results
- Segment-by-segment breakdown

**Best for:** CLI users, scripting, automation

---

### 3. Real-time Audio Levels

**File:** `demo_realtime_levels.py`

Monitor microphone levels in real-time:

```bash
# Monitor for 10 seconds (default)
python examples/demo_realtime_levels.py

# Monitor for 30 seconds
python examples/demo_realtime_levels.py 30
```

Features demonstrated:
- Real-time RMS/peak level monitoring
- Visual level bars in terminal
- dB measurements
- Level statistics

**Best for:** Testing microphone, checking audio quality, debugging

---

### 4. Clipboard Integration

**File:** `demo_clipboard.py`

Test clipboard features:

```bash
python examples/demo_clipboard.py
```

Features demonstrated:
- Session detection (Wayland/X11)
- Clipboard copy
- Paste simulation
- Fallback file mechanism
- Permission checking

**Best for:** Testing clipboard setup, troubleshooting paste issues

---

## System Requirements

All examples require:
- WhisperAloud installed: `pip install -e .`
- Virtual environment activated: `source ~/.venvs/whisper_aloud/bin/activate`

GUI demo additionally requires:
- GTK4: `sudo apt install python3-gi gir1.2-gtk-4.0`

Clipboard demo may require:
- Wayland: `wl-clipboard`, `ydotool`
- X11: `xclip`, `xdotool`

## Running Examples

### From Repository Root

```bash
# Activate environment
source ~/.venvs/whisper_aloud/bin/activate

# Run any example
python examples/demo_gui.py
python examples/demo_quick.py 5
python examples/demo_realtime_levels.py
python examples/demo_clipboard.py
```

### Common Issues

**"No module named 'whisper_aloud'"**
```bash
# Install in development mode
pip install -e .
```

**"PortAudio library not found"**
```bash
sudo apt install portaudio19-dev libportaudio2
pip install sounddevice scipy
```

**GUI: "ModuleNotFoundError: No module named 'gi'"**
```bash
# Recreate venv with system packages
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages --upgrade
source ~/.venvs/whisper_aloud/bin/activate
pip install -e .
```

## See Also

- **Main README:** [../README.md](../README.md)
- **Installation Guide:** [../INSTALL.md](../INSTALL.md)
- **Troubleshooting:** [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md)
- **System Validation:** [../scripts/validate_system.py](../scripts/validate_system.py)
