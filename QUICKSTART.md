# WhisperAloud Quick Start

Get up and running with WhisperAloud in 5 minutes.

## Prerequisites

- Debian 12+ or Ubuntu 22.04+
- Python 3.10+
- Microphone

## Installation (5 commands)

```bash
# 1. Install system dependencies
sudo apt install -y portaudio19-dev libportaudio2 python3-venv

# 2. Create virtual environment
python3 -m venv ~/.venvs/whisper_aloud && source ~/.venvs/whisper_aloud/bin/activate

# 3. Install WhisperAloud
cd /path/to/whisperAloud && pip install -e .

# 4. Verify installation
whisper-aloud-transcribe --version
```

## First Use

### 1. Generate Test Audio

```bash
python tests/fixtures/generate_sample.py
```

### 2. Transcribe Test File

```bash
whisper-aloud-transcribe tests/fixtures/sample.wav --verbose
```

Expected output:
```
Loading model: base
Transcribing: tests/fixtures/sample.wav
Transcription: [Test audio content]
Language: en (confidence: 0.98)
Processing time: 2.3s
```

### 3. Record and Transcribe (Live)

```python
python << 'EOF'
from whisper_aloud import WhisperAloudConfig, Transcriber
from whisper_aloud.audio import AudioRecorder
import time

# Setup
config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)

# Record 5 seconds
print("🎤 Recording 5 seconds... Speak now!")
recorder.start()
time.sleep(5)
audio = recorder.stop()

# Transcribe
print("🤖 Transcribing...")
result = transcriber.transcribe_numpy(audio)
print(f"📝 Transcription: {result.text}")
print(f"🎯 Confidence: {result.confidence:.1%}")
EOF
```

## Common Issues

### "PortAudio library not found"
```bash
sudo apt install -y portaudio19-dev libportaudio2
```

### "No module named 'sounddevice'"
```bash
source ~/.venvs/whisper_aloud/bin/activate
pip install sounddevice scipy
```

### "Permission denied" for audio device
```bash
sudo usermod -aG audio $USER
# Log out and back in
```

## Configuration

Create `~/.config/whisper_aloud/config.toml`:

```toml
[model]
name = "base"          # tiny, base, small, medium, large-v3
device = "auto"        # auto, cpu, cuda

[transcription]
language = "es"        # Language code or "auto"
beam_size = 5

[audio]
sample_rate = 16000
channels = 1
```

Or use environment variables:

```bash
export WHISPER_MODEL=base
export WHISPER_LANGUAGE=es
export WHISPER_DEVICE=auto
```

## Model Selection

| Model | Size | Speed | Use Case |
|-------|------|-------|----------|
| tiny  | 75MB | ⚡⚡⚡ | Testing, low-resource |
| **base** | 145MB | ⚡⚡ | **Recommended** |
| small | 466MB | ⚡ | Better accuracy |
| medium | 1.5GB | 🐌 | High accuracy |
| large-v3 | 3GB | 🐌🐌 | Maximum accuracy |

## Next Steps

- **Detailed install**: See [INSTALL.md](INSTALL.md)
- **Full documentation**: See [README.md](README.md)
- **Development workflow**: See [HOW_TO_USE_PROMPTS.md](HOW_TO_USE_PROMPTS.md)
- **Architecture**: See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

## Getting Help

**Problem installing?** → [INSTALL.md#troubleshooting](INSTALL.md#troubleshooting)
**Want GPU acceleration?** → [INSTALL.md#gpu-acceleration](INSTALL.md#performance-optimization)
**Questions?** → GitHub Issues

---

**You're ready!** The base model will download automatically on first use (~150MB).
