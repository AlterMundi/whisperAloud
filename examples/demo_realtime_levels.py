#!/usr/bin/env python3
"""
Real-time Audio Level Monitoring Demo

Shows how the level meter works with visual feedback.
Press Ctrl+C to stop.

Usage: python demo_realtime_levels.py [duration_seconds]
"""

import sys
import time


def create_level_bar(value, max_value=1.0, width=50):
    """Create a visual level bar."""
    filled = int((value / max_value) * width)
    bar = "█" * filled + "░" * (width - filled)
    return bar


def main():
    """Demo real-time level monitoring."""
    from whisper_aloud import WhisperAloudConfig
    from whisper_aloud.audio import AudioRecorder

    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("="*70)
    print("  Real-time Audio Level Monitor")
    print("="*70)
    print("\nThis demonstrates the built-in level meter.")
    print("Make sounds into your microphone to see the levels change.\n")

    config = WhisperAloudConfig.load()

    # Track statistics
    stats = {
        'max_peak': 0.0,
        'max_rms': 0.0,
        'max_db': -100.0,
        'updates': 0
    }

    def level_callback(level):
        """Display real-time levels."""
        stats['updates'] += 1
        stats['max_peak'] = max(stats['max_peak'], level.peak)
        stats['max_rms'] = max(stats['max_rms'], level.rms)
        stats['max_db'] = max(stats['max_db'], level.db)

        # Create visual bars
        peak_bar = create_level_bar(level.peak, max_value=1.0, width=40)
        rms_bar = create_level_bar(level.rms, max_value=0.5, width=40)

        # Clear line and print levels
        print(f"\r  Peak: {peak_bar} {level.peak:.3f}  |  "
              f"RMS: {rms_bar} {level.rms:.3f}  |  "
              f"dB: {level.db:6.1f}", end='', flush=True)

    recorder = AudioRecorder(config.audio, level_callback=level_callback)

    print(f"🎤 Monitoring for {duration} seconds...\n")
    print("  Legend: Peak (instantaneous) | RMS (average) | dB (loudness)\n")

    recorder.start()

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        print("\n\n⚠️  Stopped by user")

    audio = recorder.stop()

    # Show statistics
    print("\n\n" + "="*70)
    print("  Statistics")
    print("="*70)
    print(f"\nRecording:")
    print(f"  Duration: {len(audio) / config.audio.sample_rate:.2f} seconds")
    print(f"  Samples: {len(audio):,}")
    print(f"  Level updates: {stats['updates']}")

    print(f"\nPeak levels:")
    print(f"  Max peak: {stats['max_peak']:.3f}")
    print(f"  Max RMS: {stats['max_rms']:.3f}")
    print(f"  Max dB: {stats['max_db']:.1f} dB")

    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
