"""CLI interface for WhisperAloud transcription."""

import argparse
import logging
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

    # Configure logging
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(levelname)s: %(message)s',
            stream=sys.stderr,
        )
    else:
        logging.basicConfig(level=logging.WARNING)

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