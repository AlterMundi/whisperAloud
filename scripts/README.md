# WhisperAloud Scripts

Utility scripts for testing, validation, and diagnostics.

## System Validation

### validate_system.py

Comprehensive system validation tool that checks:

```bash
python scripts/validate_system.py
```

**Checks performed:**
- Python version and environment
- System dependencies (PortAudio, GTK4)
- Audio device availability
- Clipboard tool installation (wl-clipboard, xclip, ydotool, xdotool)
- Permissions for audio and input groups
- WhisperAloud package installation
- Model loading and basic transcription

**Output:** Detailed report with ✓/✗ for each check, plus actionable fixes

**Use cases:**
- Post-installation verification
- Troubleshooting setup issues
- CI/CD environment validation
- Bug report diagnostics

---

## Transcription Testing

### test_transcription_simple.py

Minimal transcription test for diagnosing model loading issues:

```bash
python scripts/test_transcription_simple.py
```

**Features:**
- Step-by-step progress indicators
- Progress dots during model download
- Performance timing
- Detailed error messages
- Tests with 1 second of silence

**Output:** Model loading time, transcription time, success/failure

**Use cases:**
- First-time model download verification
- Debugging "hanging" transcription
- Performance baseline testing
- Model cache verification

**Common scenarios:**

```bash
# First run (downloads model)
python scripts/test_transcription_simple.py
# Expected: 1-2 minutes, downloads ~150MB

# Subsequent runs (cached model)
python scripts/test_transcription_simple.py
# Expected: 5-15 seconds
```

---

## Usage Notes

### Installer Matrix Validation

Validate installer behavior for supported modes:

```bash
./scripts/validate_install_matrix.sh
```

This script exercises `install.sh --dry-run` with combinations such as:
- default install
- `--skip-system`
- `--skip-user-service`
- combined skip modes

It fails fast if reported behavior diverges from the documented matrix in `INSTALL.md`.

### CI Test Profile

Use the deterministic CI test profile:

```bash
./scripts/test_ci.sh
```

This excludes tests marked:
- `integration`
- `requires_audio_hw`
- `requires_display`
- `requires_dbus`

Pass extra pytest args after the script command:

```bash
./scripts/test_ci.sh -q -x
```

---

### Environment Setup

All scripts require the virtual environment:

```bash
source ~/.venvs/whisper_aloud/bin/activate
python scripts/<script_name>.py
```

### Permissions

Some validation checks require sudo for group membership checks:

```bash
# Check audio group
groups | grep audio

# Add user to audio group (requires logout)
sudo usermod -aG audio $USER
```

### CI/CD Integration

Use `validate_system.py` in CI pipelines:

```bash
#!/bin/bash
set -e

# Activate environment
source ~/.venvs/whisper_aloud/bin/activate

# Run validation
python scripts/validate_system.py

# Check exit code
if [ $? -eq 0 ]; then
    echo "System validation passed"
else
    echo "System validation failed"
    exit 1
fi
```

---

## See Also

- **Examples:** [../examples/](../examples/) - Feature demonstrations
- **Tests:** [../tests/](../tests/) - Unit and integration tests
- **Installation:** [../INSTALL.md](../INSTALL.md) - Setup guide
- **Troubleshooting:** [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md) - Common issues
