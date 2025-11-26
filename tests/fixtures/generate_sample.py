#!/usr/bin/env python3
"""Generate test audio fixtures for WhisperAloud tests."""

import numpy as np
import wave
from pathlib import Path


def generate_tone(frequency: float, duration: float, sample_rate: int = 16000) -> np.ndarray:
    """Generate a pure tone."""
    t = np.linspace(0, duration, int(sample_rate * duration))
    audio = np.sin(2 * np.pi * frequency * t) * 0.3
    return audio.astype(np.float32)


def save_wav(audio: np.ndarray, filename: str, sample_rate: int = 16000) -> None:
    """Save audio as WAV file."""
    # Convert float32 [-1, 1] to int16
    audio_int16 = (audio * 32767).astype(np.int16)

    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())


def main():
    """Generate all test fixtures."""
    fixtures_dir = Path(__file__).parent
    fixtures_dir.mkdir(exist_ok=True)

    # Generate 5-second 440Hz tone (A note)
    audio = generate_tone(frequency=440, duration=5.0)
    save_wav(audio, str(fixtures_dir / "sample_audio.wav"))
    print(f"Generated: {fixtures_dir / 'sample_audio.wav'}")


if __name__ == "__main__":
    main()