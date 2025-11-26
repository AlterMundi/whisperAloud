#!/usr/bin/env python3
"""
Simple transcription test - just test if model loads and works.
This helps diagnose if transcription is actually hanging or just slow.
"""

import sys
import time
import numpy as np


def main():
    """Test transcription in isolation."""
    print("="*70)
    print("  Simple Transcription Test")
    print("="*70)

    print("\n[1/5] Importing modules...")
    from whisper_aloud import Transcriber, WhisperAloudConfig
    print("   ✅ Imports successful")

    print("\n[2/5] Loading configuration...")
    config = WhisperAloudConfig.load()
    print(f"   Model: {config.model.name}")
    print(f"   Device: {config.model.device}")
    print(f"   Compute type: {config.model.compute_type}")

    print("\n[3/5] Creating transcriber...")
    transcriber = Transcriber(config)
    print("   ✅ Transcriber created")

    print("\n[4/5] Creating test audio (1 second of silence)...")
    audio = np.zeros(16000, dtype=np.float32)
    print(f"   Audio shape: {audio.shape}, dtype: {audio.dtype}")

    print("\n[5/5] Loading model and transcribing...")
    print("   ⚠️  This may take 1-2 minutes on first run (downloading model)")
    print("   Model will be cached at ~/.cache/huggingface/")
    print("   Starting transcription now...")

    start_time = time.time()

    # Add dots to show progress
    import threading
    stop_dots = threading.Event()

    def show_progress():
        while not stop_dots.is_set():
            print(".", end="", flush=True)
            time.sleep(2)

    progress_thread = threading.Thread(target=show_progress, daemon=True)
    progress_thread.start()

    try:
        result = transcriber.transcribe_numpy(audio, sample_rate=16000)
        stop_dots.set()
        progress_thread.join(timeout=1)

        elapsed = time.time() - start_time

        print(f"\n\n   ✅ Transcription successful in {elapsed:.1f} seconds!")

        print("\n" + "="*70)
        print("  RESULT")
        print("="*70)
        print(f"\nText: \"{result.text}\"")
        print(f"Language: {result.language}")
        print(f"Language probability: {result.language_probability:.2%}")
        print(f"Confidence: {result.confidence:.2%}")
        print(f"Duration: {result.duration:.2f}s")
        print(f"Segments: {len(result.segments)}")

        print("\n" + "="*70)
        print("✅ Transcription engine is working!")
        print("="*70)
        print(f"\nProcessing speed: {elapsed/1.0:.1f}x real-time")
        print("(Lower is better - 1x means it takes 1 second to process 1 second of audio)")

        if elapsed < 5:
            print("\n🚀 Good performance! Ready for real-time use.")
        elif elapsed < 10:
            print("\n⚠️  Slower performance, but usable.")
        else:
            print("\n⚠️  Slow performance - consider using 'tiny' model or GPU acceleration.")

        print("\nNext: Try 'python demo_quick.py' to record and transcribe real audio!\n")

        return 0

    except Exception as e:
        stop_dots.set()
        progress_thread.join(timeout=1)
        print(f"\n\n❌ Transcription failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.\n")
        sys.exit(1)
