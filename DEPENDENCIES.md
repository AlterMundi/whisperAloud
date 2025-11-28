# WhisperAloud Dependencies

Complete reference of all system and Python dependencies required by WhisperAloud.

## Table of Contents

- [Overview](#overview)
- [System Dependencies](#system-dependencies)
  - [Audio Libraries](#audio-libraries)
  - [GTK4 and GObject](#gtk4-and-gobject)
  - [Clipboard Tools](#clipboard-tools)
  - [Build Tools](#build-tools)
- [Python Dependencies](#python-dependencies)
  - [Core Dependencies](#core-dependencies)
  - [Development Dependencies](#development-dependencies)
- [Platform-Specific Packages](#platform-specific-packages)
- [Optional Dependencies](#optional-dependencies)
- [Dependency Verification](#dependency-verification)

---

## Overview

WhisperAloud has two categories of dependencies:

1. **System Dependencies**: Installed via package manager (apt, dnf, pacman)
   - Required for audio capture, GUI, and system integration
   - Cannot be installed via pip

2. **Python Dependencies**: Installed via pip
   - Speech recognition, audio processing, utilities
   - Automatically installed with `pip install -e .`

### Why System Dependencies?

WhisperAloud uses GTK4 for its graphical interface. The Python bindings for GTK4
(PyGObject) require:

- GObject Introspection libraries
- GTK4 typelib files (introspection data)
- Cairo graphics library

These cannot be reliably installed via pip because they require:
- C compilation with specific headers
- System-level library linking
- Distribution-specific configurations

**Solution**: Use system packages and create Python virtual environments with
`--system-site-packages` flag.

---

## System Dependencies

### Audio Libraries

#### PortAudio

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `portaudio19-dev` | `portaudio-devel` | `portaudio` | Audio I/O development headers |
| `libportaudio2` | *(included)* | *(included)* | PortAudio runtime library |

**What it does**:
- Cross-platform audio I/O library
- Provides real-time audio capture from microphone
- Used by `sounddevice` Python package

**Required**: Yes

**Verification**:
```bash
# Check if installed
pkg-config --exists portaudio-2.0 && echo "OK"

# Check library
find /usr/lib -name "*portaudio*" 2>/dev/null
```

---

### GTK4 and GObject

#### Python GObject Bindings

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `python3-gi` | `python3-gobject` | `python-gobject` | Python GObject bindings |
| `python3-gi-cairo` | *(included)* | *(included)* | Cairo integration for Python |

**What it does**:
- Provides Python bindings for GObject-based libraries
- Enables Python to use GTK4, GSound, and other GNOME libraries
- Pre-compiled bindings (no pip compilation needed)

**Required**: Yes

**Why not pip?**
```bash
# This often fails or requires many dev packages:
pip install PyGObject  # NOT recommended

# Use system package instead:
sudo apt install python3-gi  # Recommended
```

#### GTK4 Libraries

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `gir1.2-gtk-4.0` | `gtk4-devel` | `gtk4` | GTK4 introspection data |
| `libgtk-4-dev` | *(included)* | *(included)* | GTK4 development files |

**What it does**:
- GTK4 is the GUI toolkit used by GNOME applications
- Provides widgets: windows, buttons, text views, etc.
- `gir1.2-*` packages contain introspection data for Python bindings

**Required**: Yes

**Verification**:
```python
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
print(f"GTK4 version: {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}")
```

#### Adwaita (GNOME Theming)

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `gir1.2-adw-1` | `libadwaita-devel` | `libadwaita` | Adwaita introspection |
| `libadwaita-1-dev` | *(included)* | *(included)* | Adwaita development files |

**What it does**:
- Modern GNOME design patterns and widgets
- Adaptive layouts, preference groups, toasts
- Consistent look with GNOME desktop

**Required**: Yes (for full GUI functionality)

**Verification**:
```python
import gi
gi.require_version('Adw', '1')
from gi.repository import Adw
print("Adwaita OK")
```

#### GSound (Audio Feedback)

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `gir1.2-gsound-1.0` | `gsound-devel` | `gsound` | GSound introspection |
| `libgsound-dev` | *(included)* | *(included)* | GSound development files |

**What it does**:
- Plays system sounds (clicks, notifications)
- Uses freedesktop.org sound theme
- Provides audio feedback for recording start/stop

**Required**: No (optional, gracefully degraded)

**Behavior without GSound**:
- Application works normally
- No audio feedback sounds
- Warning logged at startup

**Verification**:
```python
import gi
gi.require_version('GSound', '1.0')
from gi.repository import GSound
ctx = GSound.Context()
ctx.init()
print("GSound OK")
```

---

### Clipboard Tools

WhisperAloud supports both Wayland and X11 display servers.

#### Wayland Clipboard

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `wl-clipboard` | `wl-clipboard` | `wl-clipboard` | Wayland clipboard utilities |

**Provides commands**:
- `wl-copy`: Copy text to clipboard
- `wl-paste`: Paste from clipboard

**Required**: No (but needed for clipboard on Wayland)

#### X11 Clipboard

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `xclip` | `xclip` | `xclip` | X11 clipboard utility |

**Required**: No (but needed for clipboard on X11)

#### Paste Simulation

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `ydotool` | `ydotool` | `ydotool` | Input simulation (Wayland) |
| `xdotool` | `xdotool` | `xdotool` | Input simulation (X11) |

**What it does**:
- Simulates keyboard input (Ctrl+V paste)
- Allows auto-paste after transcription

**Required**: No (optional feature)

**Wayland Setup for ydotool**:
```bash
# Enable service
sudo systemctl enable --now ydotool.service

# Add user to input group
sudo usermod -aG input $USER

# Logout and login for changes to take effect
```

---

### Build Tools

Required for compiling some Python packages:

| Debian/Ubuntu | Fedora | Arch | Purpose |
|---------------|--------|------|---------|
| `build-essential` | `gcc gcc-c++` | `base-devel` | C/C++ compiler |
| `python3-dev` | `python3-devel` | `python` | Python headers |
| `python3-venv` | *(included)* | *(included)* | Virtual environment support |
| `python3-pip` | `python3-pip` | `python-pip` | Package installer |

**Required**: Yes (for installation)

---

## Python Dependencies

### Core Dependencies

These are installed automatically via `pip install -e .`:

| Package | Version | Purpose | Size |
|---------|---------|---------|------|
| `faster-whisper` | >=1.1.0 | OpenAI Whisper implementation | ~50MB |
| `numpy` | >=1.24.0 | Numerical computing | ~20MB |
| `sounddevice` | >=0.4.6 | Audio recording | ~100KB |
| `soundfile` | >=0.12.0 | Audio file I/O | ~30MB |
| `scipy` | >=1.11.0 | Signal processing | ~40MB |
| `psutil` | >=5.9.0 | System monitoring | ~500KB |

#### faster-whisper

**What it does**:
- CTranslate2-based Whisper implementation
- 4x faster than original OpenAI Whisper
- Lower memory usage
- Supports CPU and GPU inference

**Models downloaded** (on first use):
| Model | Size | Quality | Speed |
|-------|------|---------|-------|
| `tiny` | 75MB | Lower | Fastest |
| `base` | 145MB | Good | Fast |
| `small` | 466MB | Better | Medium |
| `medium` | 1.5GB | High | Slow |
| `large-v3` | 3GB | Best | Slowest |

Models are cached in `~/.cache/huggingface/hub/`.

#### sounddevice

**What it does**:
- Python bindings for PortAudio
- Real-time audio capture
- Device enumeration and selection

**Depends on**: `portaudio` system library

#### scipy

**What it does**:
- Audio resampling (to 16kHz for Whisper)
- Signal processing utilities
- Audio normalization

---

### Development Dependencies

Installed with `pip install -e ".[dev]"`:

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=7.4.0 | Testing framework |
| `pytest-cov` | >=4.1.0 | Coverage reporting |
| `black` | >=23.0.0 | Code formatting |
| `ruff` | >=0.1.0 | Fast linting |
| `mypy` | >=1.5.0 | Static type checking |

---

## Platform-Specific Packages

### Complete Install Commands

#### Debian 12+ / Ubuntu 22.04+

```bash
sudo apt update
sudo apt install -y \
    build-essential \
    python3-dev \
    python3-venv \
    python3-pip \
    portaudio19-dev \
    libportaudio2 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    libgtk-4-dev \
    gir1.2-adw-1 \
    libadwaita-1-dev \
    gir1.2-gsound-1.0 \
    libgsound-dev \
    wl-clipboard \
    xclip \
    ydotool
```

#### Fedora 39+

```bash
sudo dnf install -y \
    gcc \
    gcc-c++ \
    python3-devel \
    portaudio-devel \
    python3-gobject \
    gtk4-devel \
    libadwaita-devel \
    gsound-devel \
    wl-clipboard \
    xclip \
    ydotool
```

#### Arch Linux

```bash
sudo pacman -S --needed \
    base-devel \
    python \
    python-pip \
    portaudio \
    python-gobject \
    gtk4 \
    libadwaita \
    gsound \
    wl-clipboard \
    xclip \
    ydotool
```

---

## Optional Dependencies

| Component | Packages | Purpose | Impact if Missing |
|-----------|----------|---------|-------------------|
| Sound Feedback | `gir1.2-gsound-1.0` | Audio cues | No sounds, warning logged |
| Wayland Clipboard | `wl-clipboard` | Copy on Wayland | Clipboard won't work on Wayland |
| X11 Clipboard | `xclip` | Copy on X11 | Clipboard won't work on X11 |
| Paste Simulation | `ydotool`/`xdotool` | Auto-paste | Manual paste required |
| GPU Acceleration | CUDA + cuDNN + cuBLAS | Faster inference | CPU-only (slower) |

---

## GPU Acceleration (NVIDIA CUDA)

For significantly faster transcription on NVIDIA GPUs, you need CUDA runtime libraries.

### Required CUDA Libraries

| Library | Debian/Ubuntu Package | Purpose |
|---------|----------------------|---------|
| CUDA Runtime | `cuda-runtime-12-*` | CUDA base runtime |
| cuDNN | `libcudnn9-cuda-12` | Deep Neural Network library |
| cuBLAS | `libcublas-12-*` | Linear algebra library |

### Installation (Debian/Ubuntu/Linux Mint)

**Step 1: Add NVIDIA CUDA Repository**

```bash
# Download and install the CUDA keyring
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
```

> **Note**: For older Ubuntu versions, replace `ubuntu2404` with your version:
> - Ubuntu 22.04: `ubuntu2204`
> - Ubuntu 20.04: `ubuntu2004`

**Step 2: Install CUDA Libraries**

```bash
sudo apt install -y libcudnn9-cuda-12 libcublas-12-8
```

### Common GPU Errors

#### "Unable to load libcudnn_ops.so"

```
Unable to load any of {libcudnn_ops.so.9.1.0, libcudnn_ops.so.9.1, libcudnn_ops.so.9, libcudnn_ops.so}
```

**Fix**: Install cuDNN
```bash
sudo apt install libcudnn9-cuda-12
```

#### "Cannot load symbol cublasLtGetVersion"

```
Invalid handle. Cannot load symbol cublasLtGetVersion
```

**Fix**: Install cuBLAS
```bash
sudo apt install libcublas-12-8
```

### Verify GPU Support

```bash
# Check if CUDA is available to faster-whisper
python3 -c "
import torch
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
"

# Or check via faster-whisper directly
python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('tiny', device='cuda')
print('GPU inference OK')
"
```

### Using CPU Instead

If you don't have an NVIDIA GPU or prefer not to install CUDA, set device to CPU:

```bash
# Via environment variable
WHISPER_ALOUD_MODEL_DEVICE=cpu whisper-aloud-gui

# Or in config.json
{
  "model": {
    "device": "cpu"
  }
}
```

---

## Dependency Verification

### Quick Check

```bash
# Run the dependency checker
python scripts/check_dependencies.py

# Verbose output
python scripts/check_dependencies.py --verbose

# Show fix commands
python scripts/check_dependencies.py --fix
```

### Manual Verification

```bash
# Python version
python3 --version  # Should be 3.10+

# GTK4 bindings
python3 -c "
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
print(f'GTK4 OK: {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}')
"

# Adwaita
python3 -c "
import gi
gi.require_version('Adw', '1')
from gi.repository import Adw
print('Adwaita OK')
"

# PortAudio
python3 -c "
import sounddevice as sd
print(f'PortAudio OK: {len(sd.query_devices())} devices')
"

# Whisper
python3 -c "
from faster_whisper import WhisperModel
print('faster-whisper OK')
"

# Clipboard (Wayland)
which wl-copy && echo "wl-clipboard OK"

# Clipboard (X11)
which xclip && echo "xclip OK"
```

### Virtual Environment Check

```bash
# Verify venv has system packages access
python3 -c "
import sys
has_system = any('/usr/' in p for p in sys.path if 'site-packages' in p or 'dist-packages' in p)
print('System packages:', 'OK' if has_system else 'MISSING (recreate venv with --system-site-packages)')
"
```

---

## Troubleshooting

### "No module named 'gi'"

Virtual environment doesn't have access to system packages.

```bash
# Fix: Recreate venv with system access
deactivate
rm -rf ~/.venvs/whisper_aloud
python3 -m venv ~/.venvs/whisper_aloud --system-site-packages
source ~/.venvs/whisper_aloud/bin/activate
pip install -e .
```

### "Namespace Gtk not available"

GTK4 introspection data not installed.

```bash
# Debian/Ubuntu
sudo apt install gir1.2-gtk-4.0

# Fedora
sudo dnf install gtk4-devel

# Arch
sudo pacman -S gtk4
```

### "PortAudio library not found"

```bash
# Debian/Ubuntu
sudo apt install portaudio19-dev libportaudio2

# Fedora
sudo dnf install portaudio-devel

# Verify
pkg-config --exists portaudio-2.0 && echo "OK"
```

### Model Download Fails

```bash
# Check internet
curl -I https://huggingface.co

# Clear cache and retry
rm -rf ~/.cache/huggingface/hub/models--Systran--faster-whisper-*

# Download manually
python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('base')
print('Model downloaded')
"
```

---

## Disk Space Requirements

| Component | Size |
|-----------|------|
| System packages | ~200MB |
| Python packages | ~300MB |
| Whisper model (base) | ~150MB |
| Whisper model (large-v3) | ~3GB |
| **Total (minimal)** | **~650MB** |
| **Total (with large model)** | **~3.5GB** |

---

## Version Compatibility Matrix

| Python | GTK4 | faster-whisper | Status |
|--------|------|----------------|--------|
| 3.10 | 4.6+ | 1.1.0+ | Supported |
| 3.11 | 4.6+ | 1.1.0+ | Supported |
| 3.12 | 4.10+ | 1.1.0+ | Supported |
| 3.13 | 4.14+ | 1.1.0+ | Tested (primary) |

---

*Last updated: 2025-01-28*
