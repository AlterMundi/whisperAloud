#!/usr/bin/env python3
"""
Demo: Clipboard Integration for WhisperAloud

This script demonstrates the clipboard functionality including:
- Session type detection (Wayland/X11)
- Text copying to clipboard
- Fallback file creation
- Paste simulation availability checking
"""

import sys
import argparse

def main():
    """Test clipboard integration."""
    from whisper_aloud import WhisperAloudConfig, ClipboardManager

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='WhisperAloud clipboard demo')
    parser.add_argument('--test-paste', action='store_true',
                        help='Test paste simulation (requires text editor to be focused)')
    args = parser.parse_args()

    print("="*70)
    print("  WhisperAloud Clipboard Integration Demo")
    print("="*70)

    # Load configuration
    config = WhisperAloudConfig.load()
    clipboard = ClipboardManager(config.clipboard)

    print(f"\n📋 Clipboard Configuration:")
    print(f"   Session type: {clipboard._session_type}")
    print(f"   Auto-copy: {config.clipboard.auto_copy}")
    print(f"   Auto-paste: {config.clipboard.auto_paste}")
    print(f"   Fallback path: {config.clipboard.fallback_path}")
    print(f"   Timeout: {config.clipboard.timeout_seconds}s")

    # Test copy functionality
    print(f"\n{'='*70}")
    print("  Test 1: Copy Text to Clipboard")
    print(f"{'='*70}\n")

    test_text = "¡Hola! This is a test from WhisperAloud clipboard integration.\n\nSupports:\n- Wayland (wl-copy)\n- X11 (xclip)\n- Fallback to file"

    print(f"Copying text ({len(test_text)} characters)...")
    result = clipboard.copy(test_text)

    if result:
        print(f"✅ Copy successful!")
        print(f"\n💡 Text is available:")
        if clipboard._session_type in ('wayland', 'x11'):
            print(f"   - In system clipboard (use Ctrl+V)")
        print(f"   - In fallback file: {config.clipboard.fallback_path}")
        print(f"\n   To view fallback file:")
        print(f"   cat {config.clipboard.fallback_path}")
    else:
        print(f"❌ Copy failed (check logs above)")

    # Check paste simulation availability
    print(f"\n{'='*70}")
    print("  Test 2: Paste Simulation Availability")
    print(f"{'='*70}\n")

    status = clipboard.check_paste_permissions()

    print(f"Paste simulation status:")
    print(f"   Available: {'✅ Yes' if status['available'] else '❌ No'}")

    if not status['available']:
        print(f"   Reason: {status['reason']}")
        print(f"\n💡 To enable paste simulation:")
        print(f"   {status['fix']}")

        if clipboard._session_type == 'wayland':
            print(f"\n📝 Complete Wayland setup:")
            print(f"   1. sudo apt install wl-clipboard ydotool")
            print(f"   2. sudo systemctl enable --now ydotool.service")
            print(f"   3. sudo usermod -aG input $USER")
            print(f"   4. Logout and login again")
    else:
        print(f"   ✅ Paste simulation is ready to use!")
        print(f"\n💡 To test paste:")
        print(f"   1. Open a text editor")
        print(f"   2. Run this script with --test-paste")

        # Actually test paste if requested
        if args.test_paste:
            print(f"\n{'='*70}")
            print("  Test 3: Paste Simulation")
            print(f"{'='*70}\n")

            from whisper_aloud.clipboard import PasteSimulator
            import time

            print("⏳ Preparing to paste in 3 seconds...")
            print("   Please focus the text editor now!")
            time.sleep(3)

            simulator = PasteSimulator(clipboard._session_type, config.clipboard)
            success = simulator.simulate_paste()

            if success:
                print("✅ Paste simulation executed!")
                print("   Check your text editor for the pasted content.")
            else:
                print("❌ Paste simulation failed!")
                print("   Check the logs above for error details.")

    # Demo with transcription
    print(f"\n{'='*70}")
    print("  Integration Example")
    print(f"{'='*70}\n")

    print("After a transcription, you could do:")
    print()
    print("  from whisper_aloud import ClipboardManager, WhisperAloudConfig")
    print("  ")
    print("  config = WhisperAloudConfig.load()")
    print("  clipboard = ClipboardManager(config.clipboard)")
    print("  ")
    print("  # After transcription")
    print("  result = transcriber.transcribe_numpy(audio)")
    print("  clipboard.copy(result.text)")
    print("  ")
    print("  # Check and use paste if available")
    print("  if config.clipboard.auto_paste:")
    print("      status = clipboard.check_paste_permissions()")
    print("      if status['available']:")
    print("          from whisper_aloud.clipboard import PasteSimulator")
    print("          simulator = PasteSimulator(clipboard._session_type, config.clipboard)")
    print("          simulator.simulate_paste()")

    print(f"\n{'='*70}\n")


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
