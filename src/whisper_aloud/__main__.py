"""CLI interface for WhisperAloud transcription and daemon control."""

import argparse
import logging
import sys
from pathlib import Path

from . import Transcriber, WhisperAloudConfig, __version__
from .exceptions import WhisperAloudError

# Import GObject libraries conditionally
try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    HAS_GIO = True
except ImportError:
    HAS_GIO = False


def check_service_running() -> bool:
    """Check if the D-Bus service is running."""
    if not HAS_GIO:
        return False

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Try to get the service name owner
        owner = connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            GLib.Variant("(s)", ("org.fede.whisperaloud",)),
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )
        return owner is not None
    except Exception:
        return False


def call_service_method(method_name: str, *args):
    """Call a method on the running D-Bus service."""
    try:
        from pydbus import SessionBus
        bus = SessionBus()
        service = bus.get("org.fede.whisperaloud")

        # Call the method
        method = getattr(service, method_name)
        result = method(*args)
        return result
    except Exception as e:
        raise WhisperAloudError(f"Failed to call service method {method_name}: {e}")


def handle_daemon_command(args) -> int:
    """Handle daemon-related commands."""
    if args.daemon:
        # Start the daemon service
        try:
            from .service import WhisperAloudService
            service = WhisperAloudService()
            service.run()
            return 0
        except Exception as e:
            print(f"Failed to start daemon: {e}", file=sys.stderr)
            return 1

    # Check if service is running
    if check_service_running():
        # Service is running, act as client
        try:
            if args.command:
                if args.command == 'start':
                    call_service_method("StartRecording")
                    print("Recording started")
                elif args.command == 'stop':
                    result = call_service_method("StopRecording")
                    print("Recording stopped, transcription in progress...")
                    # Note: actual transcription result comes via signal
                elif args.command == 'toggle':
                    result = call_service_method("ToggleRecording")
                    print(f"State: {result}")
                elif args.command == 'status':
                    result = call_service_method("GetStatus")
                    print(f"Status: {result}")
                elif args.command == 'quit':
                    call_service_method("Quit")
                    print("Service quit")
                elif args.command == 'reload':
                    result = call_service_method("ReloadConfig")
                    print(f"Config reload: {result}")
                elif args.command == 'cancel':
                    result = call_service_method("CancelRecording")
                    print("Recording cancelled" if result else "Not recording")
                else:
                    print(f"Unknown command: {args.command}", file=sys.stderr)
                    return 1
            else:
                # No command specified, show status
                result = call_service_method("GetStatus")
                print(f"Service status: {result}")
            return 0
        except WhisperAloudError as e:
            print(f"Service error: {e}", file=sys.stderr)
            return 1
    else:
        # Service not running
        if args.command:
            print("Service is not running. Start with 'whisper-aloud --daemon'", file=sys.stderr)
            return 1
        else:
            # This shouldn't happen in daemon command handler
            print("No service running and no command specified", file=sys.stderr)
            return 1


def handle_config_command(args) -> int:
    """Handle config-related commands."""
    if args.subcommand == 'validate':
        try:
            from .config import WhisperAloudConfig
            config = WhisperAloudConfig.load()
            print("✅ Configuration is valid")
            print(f"   Model: {config.model.name} on {config.model.device}")
            print(f"   Language: {config.transcription.language}")
            print(f"   Sample Rate: {config.audio.sample_rate}Hz")
            print(f"   VAD: {'enabled' if config.audio.vad_enabled else 'disabled'}")
            print(f"   Persistence: {'enabled' if config.persistence and config.persistence.save_audio else 'disabled'}")
            return 0
        except Exception as e:
            print(f"❌ Configuration has errors: {e}", file=sys.stderr)
            return 1
    else:
        print("Unknown config subcommand", file=sys.stderr)
        return 1


def handle_file_transcription(args) -> int:
    """Handle file transcription (legacy mode)."""
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
    if not hasattr(args, 'audio_file') or not args.audio_file:
        print("Error: No audio file specified", file=sys.stderr)
        return 1

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


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Transcribe audio using Whisper AI or control daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # File transcription (legacy mode)
  whisper-aloud audio.wav

  # Daemon mode
  whisper-aloud --daemon

  # Control running daemon
  whisper-aloud start
  whisper-aloud stop
  whisper-aloud status
  whisper-aloud toggle
  whisper-aloud reload
  whisper-aloud cancel
  whisper-aloud quit

  # Configuration
  whisper-aloud config validate
        """
    )

    # Global options
    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed progress"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # Daemon flag
    parser.add_argument(
        "--daemon", action="store_true", help="Start daemon service"
    )

    # Positional argument - can be either a command or an audio file
    parser.add_argument(
        "positional",
        nargs='?',
        help="Audio file path OR daemon command (start/stop/status/toggle/quit/reload/config)"
    )

    # Subcommand for config
    parser.add_argument(
        "subcommand",
        nargs='?',
        choices=['validate'],
        help="Subcommand for config operations"
    )
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

    args = parser.parse_args()

    # Handle daemon mode
    if args.daemon:
        # Set command to None for daemon mode
        args.command = None
        return handle_daemon_command(args)

    # Parse positional argument - could be command or audio file
    daemon_commands = ['start', 'stop', 'status', 'toggle', 'quit', 'reload', 'cancel', 'config']

    if args.positional:
        if args.positional in daemon_commands:
            # It's a daemon command
            args.command = args.positional
            args.audio_file = None

            if args.command == 'config':
                return handle_config_command(args)
            else:
                return handle_daemon_command(args)
        else:
            # Assume it's an audio file path
            args.command = None
            args.audio_file = Path(args.positional)
            return handle_file_transcription(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())