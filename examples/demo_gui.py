#!/usr/bin/env python3
"""
Demo: GTK4 GUI for WhisperAloud

This script demonstrates the graphical user interface.
"""

import sys
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    """Run the GUI application."""
    from whisper_aloud.ui import WhisperAloudApp

    print("="*70)
    print("  WhisperAloud GTK4 GUI Demo")
    print("="*70)
    print()
    print("Starting application...")
    print()

    app = WhisperAloudApp()
    sys.exit(app.run(sys.argv))


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
