# WhisperAloud - Voice Dictation for Linux

Offline voice transcription using OpenAI's Whisper model, optimized for Linux/GNOME.

## Phase 1: Core Transcription Engine ✅ COMPLETE

This phase implements the foundational transcription capabilities with lazy model loading, robust error handling, and CLI interface.

## Phase 2: Audio Recording Module ✅ COMPLETE

This phase adds professional audio recording capabilities with real-time level monitoring, voice activity detection, and seamless integration with Phase 1 transcription.

### Installation

**Quick Start:**
```bash
# Install system dependencies (Debian/Ubuntu)
sudo apt install -y portaudio19-dev libportaudio2 python3-venv

# Create and activate virtual environment
python3 -m venv ~/.venvs/whisper_aloud
source ~/.venvs/whisper_aloud/bin/activate

# Install WhisperAloud
pip install -e .
```

**For detailed installation instructions, troubleshooting, and other platforms, see [INSTALL.md](INSTALL.md).**

**Development installation:**
```bash
pip install -e ".[dev]"
```

### Usage

**Command Line**:
```bash
# Transcribe audio file
whisper-aloud-transcribe audio.wav

# Specify model and language
whisper-aloud-transcribe audio.wav --model medium --language en

# Verbose output
whisper-aloud-transcribe audio.wav --verbose
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
- 🔄 Phase 3: Clipboard integration (in progress)
- ⏳ Phase 4: GTK4 GUI