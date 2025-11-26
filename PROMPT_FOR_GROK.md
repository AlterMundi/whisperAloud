# Code Generation Prompt for Grok Code Fast 1

## Project Context

**Project Name**: WhisperAloud
**Goal**: Build a voice dictation application for Linux/GNOME using Whisper AI
**Target System**: Debian 12, Python 3.13, Wayland session
**Development Approach**: Incremental phases (7 total), each fully functional

---

## Phase 1 Implementation Request

### Task: Create Core Transcription Engine

Build a modular Python package that wraps `faster-whisper` for audio transcription with robust error handling, configuration management, and CLI interface.

### Project Structure to Create

```
whisper_aloud/
├── src/
│   └── whisper_aloud/
│       ├── __init__.py
│       ├── config.py           # Configuration management
│       ├── transcriber.py      # Main Whisper wrapper
│       └── exceptions.py       # Custom exceptions
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_transcriber.py
│   └── fixtures/
│       └── sample_audio.wav    # Generate 5-second test audio
├── pyproject.toml              # Modern Python packaging
├── requirements.txt            # Pinned dependencies
├── requirements-dev.txt        # Development dependencies
└── README.md                   # Usage documentation
```

---

## Technical Requirements

### Core Dependencies
```python
# requirements.txt
faster-whisper>=1.1.0
numpy>=1.24.0,<2.0.0
tomli>=2.0.1; python_version < '3.11'
```

### Development Dependencies
```python
# requirements-dev.txt
pytest>=7.4.0
pytest-cov>=4.1.0
black>=23.0.0
ruff>=0.1.0
mypy>=1.5.0
```

---

## Detailed Implementation Specifications

### 1. `src/whisper_aloud/exceptions.py`

Create custom exceptions for clear error handling:

```python
"""Custom exceptions for WhisperAloud."""

class WhisperAloudError(Exception):
    """Base exception for all WhisperAloud errors."""
    pass

class ModelLoadError(WhisperAloudError):
    """Raised when model fails to load."""
    pass

class TranscriptionError(WhisperAloudError):
    """Raised when transcription fails."""
    pass

class AudioFormatError(WhisperAloudError):
    """Raised when audio format is invalid."""
    pass

class ConfigurationError(WhisperAloudError):
    """Raised when configuration is invalid."""
    pass
```

### 2. `src/whisper_aloud/config.py`

Configuration management with validation and defaults:

**Requirements**:
- Support TOML config files (future: `~/.config/whisper_aloud/config.toml`)
- Environment variable overrides
- Sensible defaults for Debian 12 system
- Type validation for all settings
- Configuration dataclass for type safety

**Key Configuration Options**:
```python
@dataclass
class ModelConfig:
    name: str = "base"              # Model size: tiny, base, small, medium, large-v3, large-v3-turbo
    device: str = "auto"            # auto, cpu, cuda
    compute_type: str = "int8"      # int8, float16, float32
    download_root: str = None       # None = use default cache

@dataclass
class TranscriptionConfig:
    language: str = "es"            # ISO code or "auto"
    beam_size: int = 5              # 1-10, higher = better quality, slower
    vad_filter: bool = True         # Voice activity detection
    task: str = "transcribe"        # transcribe or translate

@dataclass
class WhisperAloudConfig:
    model: ModelConfig
    transcription: TranscriptionConfig

    @classmethod
    def load(cls) -> 'WhisperAloudConfig':
        """Load configuration from environment variables."""
        # Parse env vars: WHISPER_ALOUD_MODEL_NAME, etc.
        pass

    def validate(self) -> None:
        """Validate configuration values."""
        # Raise ConfigurationError if invalid
        pass
```

### 3. `src/whisper_aloud/transcriber.py`

Main transcription engine with lazy loading and error handling:

**Requirements**:
- Lazy model loading (don't load until first transcription)
- Automatic GPU detection with CPU fallback
- Progress callbacks for long transcriptions
- Memory-efficient processing
- Detailed error messages with troubleshooting hints
- Support both file paths and numpy arrays as input
- Return structured results with metadata

**Key Features**:
```python
@dataclass
class TranscriptionResult:
    """Result of a transcription operation."""
    text: str                       # Final transcribed text
    language: str                   # Detected/specified language
    segments: List[dict]            # Individual segments with timestamps
    confidence: float               # Average confidence score
    duration: float                 # Audio duration in seconds
    processing_time: float          # Time taken to transcribe

class Transcriber:
    """High-level interface to faster-whisper with error handling."""

    def __init__(self, config: WhisperAloudConfig):
        """Initialize transcriber with configuration (lazy loading)."""
        pass

    def load_model(self) -> None:
        """
        Explicitly load the Whisper model.

        Raises:
            ModelLoadError: If model fails to load with details
        """
        # Test GPU availability if device='auto'
        # Fallback to CPU if GPU fails
        # Show download progress if model not cached
        # Validate model loads successfully with dummy inference
        pass

    def transcribe_file(self, audio_path: str, **kwargs) -> TranscriptionResult:
        """
        Transcribe audio file.

        Args:
            audio_path: Path to audio file (WAV, MP3, etc.)
            **kwargs: Override transcription config

        Returns:
            TranscriptionResult with text and metadata

        Raises:
            AudioFormatError: If file format invalid/unsupported
            TranscriptionError: If transcription fails
        """
        # Validate file exists and is readable
        # Load model if not already loaded
        # Run transcription with progress tracking
        # Handle all faster-whisper exceptions gracefully
        pass

    def transcribe_numpy(self, audio: np.ndarray, sample_rate: int = 16000, **kwargs) -> TranscriptionResult:
        """
        Transcribe numpy audio array.

        Args:
            audio: Float32 array, mono, [-1.0, 1.0] range
            sample_rate: Sample rate in Hz
            **kwargs: Override transcription config

        Returns:
            TranscriptionResult with text and metadata

        Raises:
            AudioFormatError: If array format invalid
            TranscriptionError: If transcription fails
        """
        # Validate audio shape and dtype
        # Resample if needed (should be 16kHz for Whisper)
        # Run transcription
        pass

    @property
    def is_loaded(self) -> bool:
        """Check if model is currently loaded."""
        pass

    def unload_model(self) -> None:
        """Unload model to free memory."""
        pass
```

### 4. `src/whisper_aloud/__init__.py`

Public API exports:

```python
"""WhisperAloud - Voice dictation with Whisper AI for Linux."""

__version__ = "0.1.0"

from .transcriber import Transcriber, TranscriptionResult
from .config import WhisperAloudConfig, ModelConfig, TranscriptionConfig
from .exceptions import (
    WhisperAloudError,
    ModelLoadError,
    TranscriptionError,
    AudioFormatError,
    ConfigurationError,
)

__all__ = [
    "Transcriber",
    "TranscriptionResult",
    "WhisperAloudConfig",
    "ModelConfig",
    "TranscriptionConfig",
    "WhisperAloudError",
    "ModelLoadError",
    "TranscriptionError",
    "AudioFormatError",
    "ConfigurationError",
]
```

### 5. `pyproject.toml`

Modern Python packaging configuration:

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "whisper-aloud"
version = "0.1.0"
description = "Voice dictation with Whisper AI for Linux/GNOME"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "your.email@example.com"}
]
keywords = ["whisper", "transcription", "dictation", "speech-to-text", "gnome"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: End Users/Desktop",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]

dependencies = [
    "faster-whisper>=1.1.0",
    "numpy>=1.24.0,<2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "mypy>=1.5.0",
]

[project.scripts]
whisper-aloud-transcribe = "whisper_aloud.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.black]
line-length = 100
target-version = ['py310']

[tool.ruff]
line-length = 100
select = ["E", "F", "I", "N", "W"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --cov=whisper_aloud --cov-report=term-missing"
```

### 6. CLI Interface (`src/whisper_aloud/__main__.py`)

Command-line interface for testing:

```python
"""CLI interface for WhisperAloud transcription."""

import argparse
import sys
from pathlib import Path

from . import Transcriber, WhisperAloudConfig, __version__
from .exceptions import WhisperAloudError


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Transcribe audio files using Whisper AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("audio_file", type=Path, help="Path to audio file")
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"],
        help="Model size (default: base)",
    )
    parser.add_argument(
        "--language", default="es", help="Language code or 'auto' (default: es)"
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Device to use (default: auto)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed progress"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    # Validate input file
    if not args.audio_file.exists():
        print(f"Error: File not found: {args.audio_file}", file=sys.stderr)
        return 1

    try:
        # Create configuration
        config = WhisperAloudConfig.load()
        config.model.name = args.model
        config.model.device = args.device
        config.transcription.language = args.language

        # Initialize transcriber
        if args.verbose:
            print(f"Loading model: {args.model} on {args.device}...")

        transcriber = Transcriber(config)
        transcriber.load_model()

        if args.verbose:
            print(f"Transcribing: {args.audio_file}...")

        # Transcribe
        result = transcriber.transcribe_file(str(args.audio_file))

        # Output results
        print(result.text)

        if args.verbose:
            print(f"\n--- Metadata ---", file=sys.stderr)
            print(f"Language: {result.language}", file=sys.stderr)
            print(f"Duration: {result.duration:.2f}s", file=sys.stderr)
            print(f"Processing time: {result.processing_time:.2f}s", file=sys.stderr)
            print(f"Confidence: {result.confidence:.2%}", file=sys.stderr)

        return 0

    except WhisperAloudError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

---

## Testing Requirements

### Unit Tests

Create comprehensive tests for each module:

```python
# tests/test_config.py
def test_default_config():
    """Test default configuration values."""
    config = WhisperAloudConfig.load()
    assert config.model.name == "base"
    assert config.model.device == "auto"
    assert config.transcription.language == "es"

def test_env_override():
    """Test environment variable overrides."""
    # Set WHISPER_ALOUD_MODEL_NAME=medium
    # Load config
    # Assert model.name == "medium"
    pass

def test_invalid_config():
    """Test validation catches invalid settings."""
    # Create config with invalid model name
    # Assert raises ConfigurationError
    pass

# tests/test_transcriber.py
def test_transcriber_lazy_loading():
    """Test model is not loaded until first use."""
    transcriber = Transcriber(WhisperAloudConfig.load())
    assert not transcriber.is_loaded
    # After first transcription, should be loaded
    pass

def test_transcribe_silence():
    """Test transcribing 1 second of silence."""
    audio = np.zeros(16000, dtype=np.float32)
    transcriber = Transcriber(WhisperAloudConfig.load())
    result = transcriber.transcribe_numpy(audio)
    assert result.text == "" or len(result.text) < 10  # Should be empty or noise

def test_gpu_fallback():
    """Test graceful fallback from GPU to CPU."""
    config = WhisperAloudConfig.load()
    config.model.device = "cuda"  # Force CUDA
    transcriber = Transcriber(config)
    # Should fallback to CPU if CUDA unavailable
    transcriber.load_model()
    # Should not raise error
    pass

def test_invalid_audio_format():
    """Test proper error for invalid audio."""
    transcriber = Transcriber(WhisperAloudConfig.load())
    with pytest.raises(AudioFormatError):
        transcriber.transcribe_numpy(np.array([1, 2, 3], dtype=np.int32))
```

### Generate Test Audio Fixture

Create a simple test audio file:

```python
# tests/fixtures/generate_sample.py
import numpy as np
import scipy.io.wavfile as wav

# Generate 5 seconds of 440Hz sine wave (A note)
sample_rate = 16000
duration = 5
t = np.linspace(0, duration, sample_rate * duration)
audio = np.sin(2 * np.pi * 440 * t) * 0.3  # 440Hz, 30% volume
audio = audio.astype(np.float32)

wav.write("sample_audio.wav", sample_rate, audio)
```

---

## Code Quality Requirements

### Style Guide
- **Line length**: 100 characters max
- **Formatting**: Black (enforced)
- **Linting**: Ruff (E, F, I, N, W rules)
- **Type hints**: All public functions must have type hints
- **Docstrings**: Google style for all classes and public methods

### Error Messages
All error messages must:
1. State what failed clearly
2. Provide context (model name, file path, etc.)
3. Suggest a fix if possible

Example:
```python
raise ModelLoadError(
    f"Failed to load model '{model_name}' on device '{device}'. "
    f"If using GPU, ensure CUDA/ROCm drivers are installed. "
    f"Try device='cpu' to use CPU instead."
)
```

### Logging
- Use Python's `logging` module
- Log levels:
  - DEBUG: Model loading details, segment-level info
  - INFO: Transcription start/complete, config loaded
  - WARNING: GPU fallback, long processing time
  - ERROR: Transcription failures, model errors

---

## README.md Content

Create user-friendly documentation:

```markdown
# WhisperAloud - Voice Dictation for Linux

Offline voice transcription using OpenAI's Whisper model, optimized for Linux/GNOME.

## Phase 1: Core Transcription Engine

This phase implements the foundational transcription capabilities.

### Installation

\`\`\`bash
# Create virtual environment
python3 -m venv ~/.venvs/whisper_aloud
source ~/.venvs/whisper_aloud/bin/activate

# Install package
pip install -e .

# Development installation
pip install -e ".[dev]"
\`\`\`

### Usage

**Command Line**:
\`\`\`bash
# Transcribe audio file
whisper-aloud-transcribe audio.wav

# Specify model and language
whisper-aloud-transcribe audio.wav --model medium --language en

# Verbose output
whisper-aloud-transcribe audio.wav --verbose
\`\`\`

**Python API**:
\`\`\`python
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
\`\`\`

### Configuration

Via environment variables:
\`\`\`bash
export WHISPER_ALOUD_MODEL_NAME=medium
export WHISPER_ALOUD_MODEL_DEVICE=cuda
export WHISPER_ALOUD_LANGUAGE=en
\`\`\`

### Testing

\`\`\`bash
# Run all tests
pytest

# Run with coverage
pytest --cov=whisper_aloud --cov-report=html
\`\`\`

### Troubleshooting

**Model download fails**:
- Check internet connection
- Models are cached in `~/.cache/huggingface/`
- Try smaller model first: `--model base`

**GPU not detected**:
- Ensure NVIDIA drivers installed: `nvidia-smi`
- Install CUDA-enabled faster-whisper build
- Fallback to CPU with `--device cpu`

### Next Steps

- Phase 2: Audio recording from microphone
- Phase 3: Clipboard integration
- Phase 4: GTK4 GUI
\`\`\`

---

## Success Criteria

Phase 1 is complete when:

- [ ] All files created with correct structure
- [ ] `pip install -e .` succeeds without errors
- [ ] Command line tool works: `whisper-aloud-transcribe test.wav`
- [ ] Python API works: Can import and use `Transcriber` class
- [ ] All tests pass: `pytest` returns 0
- [ ] Type checking passes: `mypy src/` returns 0
- [ ] Formatting is correct: `black --check src/` returns 0
- [ ] Linting passes: `ruff check src/` returns 0
- [ ] Can transcribe 5-second audio file in <5 seconds (base model, CPU)
- [ ] README.md provides clear usage instructions
- [ ] Errors produce helpful messages (not stack traces)

---

## Important Notes for Code Generation

1. **Type Safety**: Use type hints everywhere, including internal functions
2. **Error Handling**: Every external API call (file I/O, model loading) must be try/except
3. **Validation**: Validate all inputs (file paths exist, numpy arrays correct shape, config values in range)
4. **Performance**: Lazy load model (don't load in `__init__`)
5. **Memory**: Clean up resources (model can be unloaded to free RAM)
6. **Testability**: All core logic must be testable without actual model (use mocks)
7. **Documentation**: Every public function needs a docstring with Args/Returns/Raises
8. **Progress**: Long operations should support progress callbacks (model loading, transcription)
9. **Compatibility**: Python 3.10+ (use match/case sparingly, support 3.10)
10. **Dependencies**: Pin major versions, allow minor updates (e.g., `>=1.1.0,<2.0.0`)

---

## Code Generation Checklist

- [ ] Create all directory structure files
- [ ] Implement custom exceptions with clear messages
- [ ] Implement configuration management with validation
- [ ] Implement transcriber with lazy loading
- [ ] Implement CLI with argparse
- [ ] Create pyproject.toml with all metadata
- [ ] Create requirements.txt and requirements-dev.txt
- [ ] Write comprehensive unit tests
- [ ] Generate test audio fixture
- [ ] Write README.md with examples
- [ ] Add type hints to all functions
- [ ] Add docstrings to all public APIs
- [ ] Add inline comments for complex logic
- [ ] Ensure all imports are organized
- [ ] Ensure code passes black/ruff/mypy

---

## Target Output

After generation, the repository should have:

1. **Runnable CLI**: `whisper-aloud-transcribe audio.wav` works immediately
2. **Importable package**: `from whisper_aloud import Transcriber` works
3. **Passing tests**: `pytest` shows all tests passing
4. **Type-safe code**: `mypy src/` shows no errors
5. **Formatted code**: Consistent style throughout
6. **Clear documentation**: README explains everything needed

---

## Estimated Generation Time

- **Code files**: 15-20 minutes
- **Tests**: 10-15 minutes
- **Documentation**: 5-10 minutes
- **Total**: ~30-45 minutes

Generate production-ready, maintainable code that follows Python best practices and is ready for immediate use.
