"""CLI interface for WhisperAloud transcription and daemon control."""

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import WhisperAloudConfig
from .exceptions import WhisperAloudError

# Kept for test patch compatibility; resolved lazily in handle_file_transcription.
Transcriber = None

# Import GObject libraries conditionally
try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    HAS_GIO = True
except ImportError:
    HAS_GIO = False


PROG = "whisper-aloud"


# ─── Utilities ────────────────────────────────────────────────────────────────


def _error(message: str, hint: str = "") -> int:
    """Print a consistent error message to stderr and return exit code 1."""
    print(f"{PROG}: error: {message}", file=sys.stderr)
    if hint:
        print(f"  {hint}", file=sys.stderr)
    return 1


def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {seconds % 60}s"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def _format_time_ago(seconds_ago: float) -> str:
    """Format a time delta into a human-readable 'ago' string."""
    seconds_ago = int(seconds_ago)
    if seconds_ago < 60:
        return "just now"
    minutes = seconds_ago // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


# ─── D-Bus helpers ────────────────────────────────────────────────────────────


def check_service_running() -> bool:
    """Check if the D-Bus service is running."""
    if not HAS_GIO:
        return False

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
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
        method = getattr(service, method_name)
        return method(*args)
    except Exception as e:
        raise WhisperAloudError(f"Failed to call service method {method_name}: {e}")


def _require_daemon(hint: str = "") -> bool:
    """Check that the daemon is running. Prints error if not. Returns True if running."""
    if check_service_running():
        return True
    if not hint:
        hint = ("Start it with: whisper-aloud --daemon\n"
                "  Or via systemd: systemctl --user start whisper-aloud")
    _error("daemon is not running", hint)
    return False


# ─── Command handlers ─────────────────────────────────────────────────────────


def cmd_start(args) -> int:
    """Start recording via daemon."""
    if not _require_daemon():
        return 1
    try:
        call_service_method("StartRecording")
        print("Recording started.")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_stop(args) -> int:
    """Stop recording and begin transcription."""
    if not _require_daemon():
        return 1
    try:
        call_service_method("StopRecording")
        print("Recording stopped, transcribing...")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_toggle(args) -> int:
    """Toggle recording on/off."""
    if not _require_daemon():
        return 1
    try:
        result = call_service_method("ToggleRecording")
        print(f"State: {result}")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_cancel(args) -> int:
    """Cancel active recording without transcribing."""
    if not _require_daemon():
        return 1
    try:
        result = call_service_method("CancelRecording")
        if result:
            print("Recording cancelled.")
        else:
            print("Not recording.")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_status(args) -> int:
    """Show daemon status."""
    if not _require_daemon():
        return 1
    try:
        status = call_service_method("GetStatus")
        # Unpack GLib Variants if present
        def _val(v):
            return v.unpack() if hasattr(v, 'unpack') else v

        state = _val(status.get("state", "unknown"))
        version = _val(status.get("version", "?"))
        model = _val(status.get("model", "?"))
        device = _val(status.get("device", "?"))
        hotkey = _val(status.get("hotkey_backend", "none"))
        uptime = _val(status.get("uptime", 0))

        print(f"WhisperAloud v{version}")
        print(f"  State:    {state}")
        print(f"  Model:    {model} ({device})")
        print(f"  Hotkey:   {hotkey}")
        print(f"  Uptime:   {_format_uptime(uptime)}")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_reload(args) -> int:
    """Reload daemon configuration from file."""
    if not _require_daemon():
        return 1
    try:
        result = call_service_method("ReloadConfig")
        if result:
            print("Configuration reloaded.")
        else:
            print("Configuration reload failed.")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_quit(args) -> int:
    """Stop the daemon."""
    if not _require_daemon():
        return 1
    try:
        call_service_method("Quit")
        print("Daemon stopped.")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_transcribe(args) -> int:
    """Transcribe an audio file."""
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(levelname)s: %(message)s',
            stream=sys.stderr,
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    audio_file = Path(args.file)
    if not audio_file.exists():
        return _error(f"file not found: {audio_file}")

    try:
        global Transcriber
        if Transcriber is None:
            from .transcriber import Transcriber as _Transcriber
            Transcriber = _Transcriber

        config = WhisperAloudConfig.load()
        config.model.name = args.model
        config.model.device = args.device
        config.transcription.language = args.language

        if args.verbose:
            print(f"Loading model: {args.model} on {args.device}...", file=sys.stderr)

        transcriber = Transcriber(config)
        transcriber.load_model()

        if args.verbose:
            print(f"Transcribing: {audio_file}...", file=sys.stderr)

        result = transcriber.transcribe_file(str(audio_file))
        print(result.text)

        if args.verbose:
            print(f"\n--- Metadata ---", file=sys.stderr)
            print(f"Language: {result.language}", file=sys.stderr)
            print(f"Duration: {result.duration:.2f}s", file=sys.stderr)
            print(f"Processing time: {result.processing_time:.2f}s", file=sys.stderr)
            print(f"Confidence: {result.confidence:.2%}", file=sys.stderr)

        return 0

    except WhisperAloudError as e:
        return _error(str(e))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        _error(str(e))
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def cmd_config(args) -> int:
    """Handle config subcommands."""
    if args.config_action == 'validate':
        return _config_validate(args)
    elif args.config_action == 'show':
        return _config_show(args)
    else:
        return _error("unknown config action. Use 'show' or 'validate'.")


def _config_validate(args) -> int:
    """Validate configuration file."""
    try:
        config = WhisperAloudConfig.load()
        print("Configuration is valid.")
        print(f"  Model:       {config.model.name} on {config.model.device}")
        print(f"  Language:    {config.transcription.language}")
        print(f"  Sample rate: {config.audio.sample_rate} Hz")
        print(f"  VAD:         {'enabled' if config.audio.vad_enabled else 'disabled'}")
        if config.persistence:
            print(f"  Persistence: {'enabled' if config.persistence.save_audio else 'disabled'}")
        return 0
    except Exception as e:
        return _error(f"configuration error: {e}")


def _config_show(args) -> int:
    """Show current configuration values."""
    try:
        config = WhisperAloudConfig.load()
        print("WhisperAloud Configuration")
        print()
        print("[Model]")
        print(f"  name:         {config.model.name}")
        print(f"  device:       {config.model.device}")
        print(f"  compute_type: {config.model.compute_type}")
        print()
        print("[Transcription]")
        print(f"  language:     {config.transcription.language}")
        print(f"  beam_size:    {config.transcription.beam_size}")
        print(f"  vad_filter:   {config.transcription.vad_filter}")
        print(f"  task:         {config.transcription.task}")
        print()
        print("[Audio]")
        print(f"  sample_rate:  {config.audio.sample_rate}")
        print(f"  channels:     {config.audio.channels}")
        print(f"  vad_enabled:  {config.audio.vad_enabled}")
        if hasattr(config.audio, 'device_name') and config.audio.device_name:
            print(f"  device_name:  {config.audio.device_name}")
        print()
        print("[Clipboard]")
        print(f"  auto_copy:    {config.clipboard.auto_copy}")
        print(f"  auto_paste:   {config.clipboard.auto_paste}")
        if config.persistence:
            print()
            print("[Persistence]")
            print(f"  save_audio:   {config.persistence.save_audio}")
        print()
        config_path = Path.home() / ".config" / "whisper_aloud" / "config.json"
        if config_path.exists():
            print(f"Config file: {config_path}")
        else:
            print(f"Config file: {config_path} (using defaults)")
        return 0
    except Exception as e:
        return _error(f"configuration error: {e}")


def cmd_history(args) -> int:
    """Browse transcription history."""
    if not _require_daemon():
        return 1

    try:
        if hasattr(args, 'query') and args.query:
            entries = call_service_method("SearchHistory", args.query, args.count)
        else:
            entries = call_service_method("GetHistory", args.count)

        if not entries:
            print("No transcriptions yet.")
            return 0

        now = time.time()
        for entry in entries:
            # Entries may be dicts with GLib Variants or plain values
            def _val(v):
                return v.unpack() if hasattr(v, 'unpack') else v

            entry_id = _val(entry.get("id", "?"))
            text = _val(entry.get("text", ""))
            timestamp = _val(entry.get("timestamp", 0))

            # Truncate long text
            display_text = text.replace('\n', ' ')
            if len(display_text) > 60:
                display_text = display_text[:57] + "..."

            time_str = _format_time_ago(now - timestamp) if timestamp else ""
            print(f"  #{entry_id:<4}  {time_str:<10}  \"{display_text}\"")

        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def cmd_history_search(args) -> int:
    """Search transcription history."""
    args.query = args.search_query
    return cmd_history(args)


def cmd_download_model(args) -> int:
    """Download a speech model bundle to the local cache (no daemon required)."""
    from .stt.model_download import ensure_model
    try:
        path = ensure_model(args.name)
    except ValueError as e:
        return _error(str(e))
    except Exception as e:  # network / extract failures
        return _error(f"download failed: {e}")
    print(f"Model ready: {path}")
    return 0


def handle_daemon_command(args) -> int:
    """Start the daemon service."""
    try:
        from .service import WhisperAloudService
        service = WhisperAloudService()
        service.run()
        return 0
    except Exception as e:
        return _error(f"failed to start daemon: {e}")


# ─── Argument parser ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Voice dictation and transcription powered by Whisper AI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s status                   Show daemon status
  %(prog)s start                    Start recording
  %(prog)s stop                     Stop and transcribe
  %(prog)s toggle                   Toggle recording on/off
  %(prog)s transcribe audio.wav     Transcribe a file
  %(prog)s config show              Show configuration
  %(prog)s history                  Recent transcriptions
  %(prog)s download-model parakeet-tdt-0.6b-v3
  %(prog)s --daemon                 Start the daemon
""",
    )

    parser.add_argument(
        "--version", action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="show detailed output",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="start the background daemon service",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── Daemon control commands ───────────────────────────────────────────
    sub.add_parser("start", help="start recording")
    sub.add_parser("stop", help="stop recording and transcribe")
    sub.add_parser("toggle", help="toggle recording on/off")
    sub.add_parser("cancel", help="cancel active recording")
    sub.add_parser("status", help="show daemon status")
    sub.add_parser("reload", help="reload configuration")
    sub.add_parser("quit", help="stop the daemon")

    # ── Model download ────────────────────────────────────────────────────
    p_download = sub.add_parser(
        "download-model",
        help="download a speech model to the cache",
        description="Download a named speech model to the local cache. No daemon required.",
    )
    p_download.add_argument("name", help="model name, e.g. parakeet-tdt-0.6b-v3")

    # ── Transcribe ────────────────────────────────────────────────────────
    p_transcribe = sub.add_parser(
        "transcribe", help="transcribe an audio file",
        description="Transcribe an audio file using the Whisper model.",
    )
    p_transcribe.add_argument("file", help="path to audio file")
    p_transcribe.add_argument(
        "--model", "-m", default="base",
        choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"],
        help="model size (default: base)",
    )
    p_transcribe.add_argument(
        "--language", "-l", default="auto",
        help="language code or 'auto' (default: auto)",
    )
    p_transcribe.add_argument(
        "--device", "-d", default="auto",
        choices=["auto", "cpu", "cuda"],
        help="compute device (default: auto)",
    )
    p_transcribe.add_argument(
        "--verbose", "-v", action="store_true",
        help="show detailed output",
    )

    # ── Config ────────────────────────────────────────────────────────────
    p_config = sub.add_parser(
        "config", help="view or validate configuration",
        description="View or validate the WhisperAloud configuration.",
    )
    p_config_sub = p_config.add_subparsers(dest="config_action", metavar="<action>")
    p_config_sub.add_parser("show", help="show current configuration")
    p_config_sub.add_parser("validate", help="validate configuration file")

    # ── History ───────────────────────────────────────────────────────────
    p_history = sub.add_parser(
        "history", help="browse transcription history",
        description="Browse recent transcription history.",
    )
    p_history.add_argument(
        "-n", "--count", type=int, default=10,
        help="number of entries to show (default: 10)",
    )

    p_history_sub = p_history.add_subparsers(dest="history_action", metavar="")
    p_search = p_history_sub.add_parser("search", help="search history by text")
    p_search.add_argument("search_query", help="search text")

    return parser


COMMAND_MAP = {
    "start": cmd_start,
    "stop": cmd_stop,
    "toggle": cmd_toggle,
    "cancel": cmd_cancel,
    "status": cmd_status,
    "reload": cmd_reload,
    "quit": cmd_quit,
    "transcribe": cmd_transcribe,
    "config": cmd_config,
    "history": cmd_history,
    "download-model": cmd_download_model,
}


def main(argv=None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle --daemon flag (backward compat, systemd ExecStart)
    if args.daemon:
        return handle_daemon_command(args)

    # No command given → show help
    if not args.command:
        parser.print_help()
        return 0

    # History search sub-action
    if args.command == "history" and getattr(args, 'history_action', None) == "search":
        args.query = args.search_query
        return cmd_history(args)

    # Config with no sub-action defaults to 'show'
    if args.command == "config" and not getattr(args, 'config_action', None):
        args.config_action = 'show'

    # Dispatch to command handler
    handler = COMMAND_MAP.get(args.command)
    if handler:
        return handler(args)
    else:
        return _error(f"unknown command: {args.command}")


def daemon_main() -> int:
    """Entry point for dedicated daemon command."""
    class _Args:
        daemon = True
    return handle_daemon_command(_Args())


def transcribe_main() -> int:
    """Entry point for legacy transcribe-only command."""
    return main(["transcribe", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
