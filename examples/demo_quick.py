#!/usr/bin/env python3
"""
Quick WhisperAloud Demo - Record and transcribe in one go

Usage: python demo_quick.py [duration_seconds]
Example: python demo_quick.py 5
"""

import sys
import time


def main():
    """Quick demo: record and transcribe."""
    from whisper_aloud import Transcriber, WhisperAloudConfig
    from whisper_aloud.audio import AudioRecorder

    # Get duration from command line or use default
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    print("="*70)
    print("  WhisperAloud Quick Demo")
    print("="*70)

    # Setup
    config = WhisperAloudConfig.load()
    recorder = AudioRecorder(config.audio)
    transcriber = Transcriber(config)

    print(f"\nConfiguration:")
    print(f"  Model: {config.model.name}")
    print(f"  Language: {config.transcription.language or 'auto-detect'}")
    print(f"  Duration: {duration} seconds")

    # Record
    print(f"\n🎤 Recording for {duration} seconds...")
    print("   SAY SOMETHING NOW!")

    recorder.start()

    # Countdown
    for i in range(duration, 0, -1):
        print(f"   {i}...", end='', flush=True)
        time.sleep(1)
    print()

    audio = recorder.stop()

    print(f"\n✅ Recording complete ({len(audio)} samples)")

    # Transcribe
    print("\n🤖 Transcribing...")
    print("   Note: First run may take 1-2 minutes to download/load model...")
    print("   Please be patient...")

    start_time = time.time()
    result = transcriber.transcribe_numpy(audio, sample_rate=config.audio.sample_rate)
    elapsed = time.time() - start_time

    print(f"   ✅ Transcription completed in {elapsed:.1f} seconds")

    # Display results
    print("\n" + "="*70)
    print("  RESULT")
    print("="*70)
    print(f"\n📝 Text: \"{result.text}\"")
    print(f"\n🌐 Language: {result.language}")
    print(f"🎯 Transcription confidence: {result.confidence:.1%}")
    print(f"⏱️  Duration: {result.duration:.2f} seconds")
    print(f"📊 Segments: {len(result.segments)}")

    if result.segments:
        print(f"\nSegment details:")
        for i, seg in enumerate(result.segments, 1):
            print(f"  [{i}] {seg['start']:.2f}s - {seg['end']:.2f}s: \"{seg['text']}\"")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
