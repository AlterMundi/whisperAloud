# WhisperAloud Validation Guide

This guide shows you how to **actually run and test** WhisperAloud to verify it works on your system.

## Quick Start

```bash
# Activate the virtual environment
source ~/.venvs/whisper_aloud/bin/activate

# Verify you're in the right environment
which python
# Should show: /home/fede/.venvs/whisper_aloud/bin/python
```

---

## Validation Method 1: Interactive Validation Script

The comprehensive test suite that checks all components:

```bash
python validate_system.py
```

**What it tests:**
1. ✅ All Python imports work
2. ✅ Configuration loads correctly
3. ✅ Audio devices are detected
4. ✅ Audio recording works (with your permission)
5. ✅ Transcription engine works
6. ✅ End-to-end workflow (record + transcribe)

**Sample output:**
```
======================================================================
  TEST 1: Import Validation
======================================================================

✅ All imports successful

======================================================================
  TEST 2: Configuration System
======================================================================

✅ Configuration loaded successfully
   Model: base
   Device: auto
   Compute Type: int8
   Language: es
   Sample Rate: 16000Hz
```

---

## Validation Method 2: Quick Demo

Record and transcribe in one command:

```bash
# Record for 3 seconds (default)
python demo_quick.py

# Record for 5 seconds
python demo_quick.py 5

# Record for 10 seconds
python demo_quick.py 10
```

**What happens:**
1. Counts down while recording
2. Captures audio from your microphone
3. Transcribes immediately
4. Shows detailed results

**Example output:**
```
🎤 Recording for 5 seconds...
   SAY SOMETHING NOW!
   5...4...3...2...1...

✅ Recording complete (80000 samples)

\
======================================================================
  RESULT
======================================================================

📝 Text: "Hello, this is a test of the transcription system"

🌐 Language: en (99.8% confidence)
🎯 Transcription confidence: 87.3%
⏱️  Duration: 5.00 seconds
```

---

## Validation Method 3: Real-time Level Monitor

See your microphone levels in real-time:

```bash
# Monitor for 10 seconds (default)
python demo_realtime_levels.py

# Monitor for 30 seconds
python demo_realtime_levels.py 30
```

**What you'll see:**
```
🎤 Monitoring for 10 seconds...

  Peak: ████████████░░░░░░░░░░░░ 0.342  |  RMS: ██████░░░░░░░░ 0.127  |  dB:  -18.9
```

Press **Ctrl+C** to stop early.

---

## Validation Method 4: Individual Component Tests

### Test 1: Check Imports

```bash
python -c "
from whisper_aloud import Transcriber, WhisperAloudConfig
from whisper_aloud.audio import AudioRecorder, DeviceManager
print('✅ All imports successful')
"
```

### Test 2: List Audio Devices

```bash
python -c "
from whisper_aloud.audio import DeviceManager

devices = DeviceManager.list_input_devices()
default = DeviceManager.get_default_input_device()

print(f'Found {len(devices)} input devices')
print(f'\nDefault: [{default.id}] {default.name}')
print(f'  Channels: {default.channels}')
print(f'  Sample Rate: {default.sample_rate}Hz')

print('\nAll devices:')
for dev in devices:
    marker = ' [DEFAULT]' if dev.is_default else ''
    print(f'  [{dev.id:2d}] {dev.name}{marker}')
"
```

### Test 3: Test Configuration

```bash
python -c "
from whisper_aloud import WhisperAloudConfig
import json

config = WhisperAloudConfig.load()

print('Current configuration:')
print(f'  Model: {config.model.name}')
print(f'  Device: {config.model.device}')
print(f'  Compute Type: {config.model.compute_type}')
print(f'  Language: {config.transcription.language or \"auto-detect\"}')
print(f'  Sample Rate: {config.audio.sample_rate}Hz')
print(f'  VAD Enabled: {config.audio.vad_enabled}')
print(f'  VAD Threshold: {config.audio.vad_threshold}')
print(f'  Max Duration: {config.audio.max_recording_duration}s')
"
```

### Test 4: Record 2 Seconds of Audio

```bash
python -c "
from whisper_aloud import WhisperAloudConfig
from whisper_aloud.audio import AudioRecorder
import time

config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)

print('🎤 Recording 2 seconds...')
recorder.start()
time.sleep(2)
audio = recorder.stop()

duration = len(audio) / config.audio.sample_rate
print(f'✅ Recorded {duration:.2f} seconds')
print(f'   Samples: {len(audio):,}')
print(f'   Shape: {audio.shape}')
print(f'   Dtype: {audio.dtype}')
"
```

### Test 5: Transcribe Silence (Model Test)

```bash
python -c "
from whisper_aloud import Transcriber, WhisperAloudConfig
import numpy as np

config = WhisperAloudConfig.load()
transcriber = Transcriber(config)

# Create 1 second of silence
audio = np.zeros(16000, dtype=np.float32)

print('🤖 Testing transcription engine with silence...')
result = transcriber.transcribe_numpy(audio)

print(f'✅ Transcription successful')
print(f'   Text: \"{result.text}\"')
print(f'   Language: {result.language}')
print(f'   Confidence: {result.confidence:.2%}')
"
```

**Note:** First run will download the model (~145MB for base model).

### Test 6: Record and Transcribe (Full Workflow)

```bash
python -c "
from whisper_aloud import Transcriber, WhisperAloudConfig
from whisper_aloud.audio import AudioRecorder
import time

config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)
transcriber = Transcriber(config)

print('🎤 Recording 5 seconds... SAY SOMETHING!')
recorder.start()
time.sleep(5)
audio = recorder.stop()

print('🤖 Transcribing...')
result = transcriber.transcribe_numpy(audio)

print('\n' + '='*60)
print('RESULT:')
print(f'Text: \"{result.text}\"')
print(f'Language: {result.language} ({result.language_probability:.1%})')
print(f'Confidence: {result.confidence:.1%}')
print('='*60)
"
```

---

## Validation Method 5: CLI Tool Test

### Check CLI is installed

```bash
which whisper-aloud-transcribe
# Should show: /home/fede/.venvs/whisper_aloud/bin/whisper-aloud-transcribe

whisper-aloud-transcribe --version
whisper-aloud-transcribe --help
```

### Create test audio file

```bash
# Generate a test audio file (440Hz tone for 3 seconds)
python -c "
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

sample_rate = 16000
duration = 3
frequency = 440

t = np.linspace(0, duration, int(sample_rate * duration))
audio = np.sin(2 * np.pi * frequency * t) * 0.3
audio = (audio * 32767).astype(np.int16)

wavfile.write('test_audio.wav', sample_rate, audio)
print('✅ Created test_audio.wav')
"
```

### Transcribe the test file

```bash
whisper-aloud-transcribe test_audio.wav --verbose
```

### Record and save, then transcribe

```bash
# Record 5 seconds
python -c "
from whisper_aloud import WhisperAloudConfig
from whisper_aloud.audio import AudioRecorder
from scipy.io import wavfile
import time

config = WhisperAloudConfig.load()
recorder = AudioRecorder(config.audio)

print('🎤 Recording 5 seconds... speak now!')
recorder.start()
time.sleep(5)
audio = recorder.stop()

# Save as WAV
wavfile.write('my_recording.wav', config.audio.sample_rate, audio)
print('✅ Saved to my_recording.wav')
"

# Now transcribe it
whisper-aloud-transcribe my_recording.wav --verbose
```

---

## Expected Results

### ✅ Success Indicators

1. **Imports work** - No ImportError or ModuleNotFoundError
2. **Audio devices found** - At least 1 device listed
3. **Recording works** - No PortAudio errors, samples captured
4. **Transcription works** - Model loads, returns result object
5. **Text output** - Even if empty/silence, should return clean result

### ❌ Common Issues

#### "PortAudio library not found"
```bash
sudo apt install -y portaudio19-dev libportaudio2
# Then reinstall
pip install --force-reinstall sounddevice
```

#### "No module named 'whisper_aloud'"
```bash
# Make sure venv is activated
source ~/.venvs/whisper_aloud/bin/activate

# Reinstall
pip install -e .
```

#### ALSA warnings (can be ignored)
```
ALSA lib ... Unknown PCM cards.pcm.rear
```
These are harmless. To suppress: see INSTALL.md:308-320

#### "Model download fails"
```bash
# Check internet connection
ping -c 3 huggingface.co

# Clear cache and retry
rm -rf ~/.cache/huggingface/hub/models--Systran--faster-whisper-base
```

---

## Performance Benchmarks

After validation, check performance:

```bash
python -c "
from whisper_aloud import Transcriber, WhisperAloudConfig
import numpy as np
import time

config = WhisperAloudConfig.load()
transcriber = Transcriber(config)

# Test with 10 seconds of audio
audio = np.random.randn(160000).astype(np.float32) * 0.01

print(f'Testing model: {config.model.name}')
print(f'Device: {config.model.device}')

start = time.time()
result = transcriber.transcribe_numpy(audio)
elapsed = time.time() - start

print(f'\nPerformance:')
print(f'  Audio duration: 10.0 seconds')
print(f'  Processing time: {elapsed:.2f} seconds')
print(f'  Real-time factor: {elapsed/10:.2f}x')

if elapsed < 10:
    print('  ✅ Faster than real-time!')
else:
    print('  ⚠️  Slower than real-time')
"
```

**Good performance:**
- Base model on CPU: ~2-5x real-time (2-5 seconds per 1 second of audio)
- Base model on GPU: ~10-20x real-time

---

## Next Steps After Validation

Once everything works:

1. **Try different models:**
   ```bash
   export WHISPER_ALOUD_MODEL_NAME=tiny  # Faster, less accurate
   export WHISPER_ALOUD_MODEL_NAME=small  # Slower, more accurate
   ```

2. **Change language:**
   ```bash
   export WHISPER_ALOUD_LANGUAGE=en  # English
   export WHISPER_ALOUD_LANGUAGE=auto  # Auto-detect
   ```

3. **Run the full test suite:**
   ```bash
   pytest -v
   pytest --cov=whisper_aloud --cov-report=html
   ```

4. **Explore Phase 3** (clipboard integration) - see PHASES_3_TO_7_ROADMAP.md

---

## Troubleshooting

### Get detailed error info:

```bash
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)

from whisper_aloud import Transcriber, WhisperAloudConfig
# ... your test code ...
"
```

### Check model cache:

```bash
ls -lh ~/.cache/huggingface/hub/models--Systran--faster-whisper-*
```

### Verify audio system:

```bash
# Test system audio
arecord -l  # List recording devices
arecord -d 3 test.wav  # Record 3 seconds
aplay test.wav  # Play it back
```

---

## Summary

**Simplest validation:**
```bash
source ~/.venvs/whisper_aloud/bin/activate
python demo_quick.py 5
```

**Most comprehensive:**
```bash
source ~/.venvs/whisper_aloud/bin/activate
python validate_system.py
```

Both should work flawlessly on your Debian 12 system with Python 3.13.
