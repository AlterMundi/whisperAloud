#!/usr/bin/env python3
"""
WhisperAloud System Validation Script

Run this to verify all components are working correctly.
Usage: python validate_system.py
"""

import sys
import time
from pathlib import Path


def print_header(text):
    """Print a formatted header."""
    print(f"\n{'='*70}")
    print(f"  {text}")
    print(f"{'='*70}\n")


def test_imports():
    """Test 1: Verify all imports work."""
    print_header("TEST 1: Import Validation")

    try:
        from whisper_aloud import Transcriber, WhisperAloudConfig
        from whisper_aloud.audio import AudioRecorder, DeviceManager
        from whisper_aloud.exceptions import (
            WhisperAloudError,
            ModelLoadError,
            AudioRecordingError,
        )
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False


def test_configuration():
    """Test 2: Verify configuration loading."""
    print_header("TEST 2: Configuration System")

    try:
        from whisper_aloud import WhisperAloudConfig

        config = WhisperAloudConfig.load()
        print(f"✅ Configuration loaded successfully")
        print(f"   Model: {config.model.name}")
        print(f"   Device: {config.model.device}")
        print(f"   Compute Type: {config.model.compute_type}")
        print(f"   Language: {config.transcription.language or 'auto-detect'}")
        print(f"   Sample Rate: {config.audio.sample_rate}Hz")
        print(f"   VAD Enabled: {config.audio.vad_enabled}")
        return True
    except Exception as e:
        print(f"❌ Configuration failed: {e}")
        return False


def test_audio_devices():
    """Test 3: Check audio device detection."""
    print_header("TEST 3: Audio Device Detection")

    try:
        from whisper_aloud.audio import DeviceManager

        devices = DeviceManager.list_input_devices()
        default = DeviceManager.get_default_input_device()

        print(f"✅ Found {len(devices)} input device(s)")
        print(f"\nDefault device:")
        print(f"   [{default.id}] {default.name}")
        print(f"   Channels: {default.channels}")
        print(f"   Sample Rate: {default.sample_rate}Hz")

        if len(devices) > 1:
            print(f"\nOther available devices:")
            for dev in devices[:5]:  # Show first 5
                if dev.id != default.id:
                    print(f"   [{dev.id}] {dev.name}")

        return True
    except Exception as e:
        print(f"❌ Device detection failed: {e}")
        return False


def test_audio_recording():
    """Test 4: Record a short audio sample."""
    print_header("TEST 4: Audio Recording (3 seconds)")

    try:
        from whisper_aloud import WhisperAloudConfig
        from whisper_aloud.audio import AudioRecorder

        config = WhisperAloudConfig.load()

        # Track level updates
        level_count = [0]

        def level_callback(level):
            level_count[0] += 1
            if level_count[0] % 10 == 0:  # Print every 10th update
                print(f"   📊 Level: {level.db:.1f} dB (peak: {level.peak:.3f})")

        recorder = AudioRecorder(config.audio, level_callback=level_callback)

        print("🎤 Recording for 3 seconds... Speak or make noise!")
        recorder.start()
        time.sleep(3)
        audio = recorder.stop()

        duration = len(audio) / config.audio.sample_rate
        print(f"\n✅ Recording successful")
        print(f"   Duration: {duration:.2f} seconds")
        print(f"   Samples: {len(audio)}")
        print(f"   Level updates: {level_count[0]}")
        print(f"   Sample rate: {config.audio.sample_rate}Hz")

        return True, audio
    except Exception as e:
        print(f"❌ Recording failed: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_transcription(audio=None):
    """Test 5: Transcribe audio."""
    print_header("TEST 5: Transcription Engine")

    try:
        from whisper_aloud import Transcriber, WhisperAloudConfig
        import numpy as np

        config = WhisperAloudConfig.load()
        transcriber = Transcriber(config)

        # Use provided audio or create test signal
        if audio is None:
            print("ℹ️  No audio provided, creating test silence...")
            audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence

        print(f"🤖 Loading model '{config.model.name}'... (first time may take a moment)")
        print(f"   This will download the model if not already cached.")

        result = transcriber.transcribe_numpy(audio, sample_rate=16000)

        print(f"\n✅ Transcription successful")
        print(f"   Text: '{result.text}'")
        print(f"   Language: {result.language}")
        print(f"   Confidence: {result.confidence:.2%}")
        print(f"   Duration: {result.duration:.2f}s")
        print(f"   Segments: {len(result.segments)}")

        if result.text.strip():
            print(f"\n   🎯 Detected speech: \"{result.text}\"")
        else:
            print(f"\n   ℹ️  No speech detected (silence or noise)")

        return True
    except Exception as e:
        print(f"❌ Transcription failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_end_to_end():
    """Test 6: Complete workflow."""
    print_header("TEST 6: End-to-End Workflow (Record + Transcribe)")

    try:
        from whisper_aloud import Transcriber, WhisperAloudConfig
        from whisper_aloud.audio import AudioRecorder

        config = WhisperAloudConfig.load()
        recorder = AudioRecorder(config.audio)
        transcriber = Transcriber(config)

        print("🎤 Recording 5 seconds... SAY SOMETHING!")
        print("   Try: 'Hello, this is a test'")
        recorder.start()
        time.sleep(5)
        audio = recorder.stop()

        print("\n🤖 Transcribing...")
        result = transcriber.transcribe_numpy(audio, sample_rate=16000)

        print(f"\n✅ Complete workflow successful!")
        print(f"\n📝 TRANSCRIPTION RESULT:")
        print(f"   Text: \"{result.text}\"")
        print(f"   Language: {result.language}")
        print(f"   Overall confidence: {result.confidence:.1%}")

        return True
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
        return False
    except Exception as e:
        print(f"❌ End-to-end test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all validation tests."""
    print("\n" + "="*70)
    print("  WhisperAloud System Validation")
    print("="*70)
    print("\nThis will test all components of WhisperAloud.")
    print("Make sure you have a working microphone!\n")

    results = []

    # Test 1: Imports
    results.append(("Imports", test_imports()))

    # Test 2: Configuration
    results.append(("Configuration", test_configuration()))

    # Test 3: Audio Devices
    results.append(("Audio Devices", test_audio_devices()))

    # Test 4: Audio Recording
    print("\n" + "-"*70)
    response = input("Run audio recording test? (y/n): ").strip().lower()
    if response == 'y':
        success, audio = test_audio_recording()
        results.append(("Audio Recording", success))

        # Test 5: Transcription
        if success and audio is not None:
            print("\n" + "-"*70)
            response = input("Transcribe the recorded audio? (y/n): ").strip().lower()
            if response == 'y':
                results.append(("Transcription", test_transcription(audio)))
            else:
                print("ℹ️  Skipping transcription test")
        else:
            # Test with silence
            results.append(("Transcription", test_transcription(None)))
    else:
        print("ℹ️  Skipping audio recording test")
        results.append(("Transcription", test_transcription(None)))

    # Test 6: End-to-end (optional)
    print("\n" + "-"*70)
    response = input("\nRun full end-to-end test (record + transcribe)? (y/n): ").strip().lower()
    if response == 'y':
        results.append(("End-to-End", test_end_to_end()))
    else:
        print("ℹ️  Skipping end-to-end test")

    # Summary
    print_header("VALIDATION SUMMARY")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\n{'='*70}")
    print(f"  Results: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
    print(f"{'='*70}\n")

    if passed == total:
        print("🎉 All tests passed! WhisperAloud is fully functional.\n")
        return 0
    else:
        print("⚠️  Some tests failed. Check the output above for details.\n")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Validation interrupted by user.\n")
        sys.exit(1)
