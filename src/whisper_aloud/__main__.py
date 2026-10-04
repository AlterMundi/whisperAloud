"""CLI interface for WhisperAloud transcription and daemon control."""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from . import __version__
from .config import WhisperAloudConfig, user_config_dir
from .daemon_hints import cli_daemon_unavailable_hint
from .exceptions import ConfigurationError, WhisperAloudError
from .sandbox import is_flatpak_sandbox

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


def _activate_daemon_connection(max_wait_seconds: float = 20.0) -> bool:
    """Attempt to connect to the daemon, triggering D-Bus activation.

    Field bug 5: `check_service_running()` only calls org.freedesktop.DBus's
    GetNameOwner, a plain query that never triggers D-Bus activation -- so a
    CLI command run while the daemon isn't already up (e.g. right after
    `flatpak run --command=whisper-aloud org.fede.whisperaloud status`, with
    nothing else having started it yet) always reported "not running"
    instead of auto-starting it the way the exported .service activation
    file is meant to allow.

    WhisperAloudClient()'s first connection attempt (pydbus's
    SessionBus().get(name)) does trigger activation, and
    connect_with_activation_wait() keeps retrying while that attempt looks
    like the daemon is mid-startup (field bug 1's is_starting
    classification) instead of giving up immediately. Bounded here (unlike
    the GUI's indefinite-while-starting retry) because a CLI command must
    eventually return control to the shell either way -- `max_wait_seconds`
    is a generous allowance for first-run model/audio/tray/hotkey init, not
    a "give up and declare it broken" deadline.
    """
    from .service.client import WhisperAloudClient

    client = WhisperAloudClient()
    try:
        return client.connect_with_activation_wait(max_wait_seconds=max_wait_seconds)
    finally:
        client.disconnect()


def _require_daemon(hint: str = "", *, allow_activation: bool = True) -> bool:
    """Check that the daemon is running. Prints error if not. Returns True if running.

    `allow_activation=False` (used by `quit`) skips the activation-triggering
    probe below -- starting the daemon just to immediately quit it again
    would be pointless, and the plain GetNameOwner check is all that command
    needs.
    """
    if check_service_running():
        return True
    if allow_activation and _activate_daemon_connection():
        return True
    if not hint:
        hint = cli_daemon_unavailable_hint(is_flatpak=is_flatpak_sandbox())
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
        hotkey_trigger = _val(status.get("hotkey_trigger", ""))
        uptime = _val(status.get("uptime", 0))
        # API 1.6.0 model readiness; absent from older daemons.
        model_state = _val(status.get("model_state", ""))
        model_error = _val(status.get("model_error", ""))

        hotkey_display = f"{hotkey} ({hotkey_trigger})" if hotkey_trigger else hotkey
        # API 1.7.0 (WHISP-87) readiness keys; absent from older daemons.
        hotkey_bound = status.get("hotkey_bound")
        if hotkey_bound is not None:
            hotkey_display += " [bound]" if _val(hotkey_bound) else " [not bound]"
        autostart = status.get("autostart")
        model_state_display = f" [{model_state}]" if model_state else ""

        print(f"WhisperAloud v{version}")
        print(f"  State:    {state}")
        print(f"  Model:    {model} ({device}){model_state_display}")
        if model_error:
            print(f"  Model error: {model_error}")
        print(f"  Hotkey:   {hotkey_display}")
        if autostart is not None:
            login = "starts in background" if _val(autostart) else "not started at login"
            print(f"  Login:    {login}")
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
    # allow_activation=False: starting the daemon (D-Bus activation, plus its
    # audio/model/tray/hotkey init) just to immediately quit it again would
    # be pure waste -- if it's not already running, there's nothing to quit.
    if not _require_daemon(allow_activation=False):
        return 1
    try:
        call_service_method("Quit")
        print("Daemon stopped.")
        return 0
    except WhisperAloudError as e:
        return _error(str(e))


def _resolve_transcribe_model(args, catalog):
    """Resolve (engine, model_name) for `transcribe` from --engine/--model/--language.

    Mirrors `cmd_download_model`'s needs_review gate: an entry pending license
    review is only reachable with an explicit, matching --model/--engine plus
    --accept-license (WHISP-76 item 3). The no-args default and an --engine-only
    selection both go through `catalog.recommended()`, which never returns a
    needs_review entry on its own.
    """
    accept_license = getattr(args, "accept_license", False)
    language = getattr(args, "language", "auto")

    def _check_review(entry):
        if entry.needs_review and not accept_license:
            raise ValueError(
                f"model '{entry.name}' is pending license review (license: {entry.license}); "
                "re-run with --accept-license to proceed"
            )

    if args.engine and args.model:
        entry = catalog.get(args.engine, args.model)
        if entry is None:
            raise ValueError(f"no model '{args.model}' for engine '{args.engine}'")
        _check_review(entry)
        return entry.engine, entry.name

    if args.engine:
        try:
            entry = catalog.recommended(engine=args.engine, language=language)
        except LookupError as e:
            raise ValueError(str(e))
        return entry.engine, entry.name

    if args.model:
        matches = [
            e for e in catalog.entries() if e.name == args.model or args.model in e.aliases
        ]
        if not matches:
            raise ValueError(f"no model named '{args.model}' in the catalog")
        entry = matches[0]
        _check_review(entry)
        return entry.engine, entry.name

    entry = catalog.recommended(language=language)
    return entry.engine, entry.name


def _segments_from_result(result):
    """Segment list from a native (faster-whisper) TranscriptionResult.

    Defensive about `result.segments` not being a real list (e.g. an
    unconfigured MagicMock in a test double) -- treated as "no segments".
    """
    from .stt.formatters import Segment

    raw = getattr(result, "segments", None)
    if not isinstance(raw, list):
        return []
    return [
        Segment(start=float(s.get("start", 0.0)), end=float(s.get("end", 0.0)), text=str(s.get("text", "")))
        for s in raw
    ]


def _transcribe_chunked(engine, samples, sample_rate, language, *, verbose=False):
    """Chunked transcription for engines without native long-form support.

    Splits `samples` at silence-aware cut points (`stt.chunking`), transcribes
    each chunk, and assembles absolute-timestamp segments: chunk offset plus
    intra-chunk timestamps when the engine provides them, otherwise the chunk's
    own start/end (WHISP-76 item 4). Returns (whole_text, segments).
    """
    from .stt.chunking import iter_chunks
    from .stt.formatters import Segment

    chunks = iter_chunks(samples, sample_rate)
    total = len(chunks)
    texts: list[str] = []
    segments: list[Segment] = []
    run_start = time.time()

    for i, (a, b) in enumerate(chunks, start=1):
        chunk_offset = a / sample_rate
        chunk_duration = (b - a) / sample_rate
        chunk_t0 = time.time()
        result = engine.transcribe_numpy(samples[a:b], sample_rate, language=language)
        chunk_elapsed = time.time() - chunk_t0

        text = (result.text or "").strip()
        if text:
            texts.append(text)

        inner = getattr(result, "segments", None)
        if isinstance(inner, list) and inner:
            for s in inner:
                segments.append(Segment(
                    start=chunk_offset + float(s.get("start", 0.0)),
                    end=chunk_offset + float(s.get("end", 0.0)),
                    text=str(s.get("text", "")),
                ))
        elif text:
            segments.append(Segment(start=chunk_offset, end=chunk_offset + chunk_duration, text=text))

        if verbose:
            rtf = chunk_elapsed / chunk_duration if chunk_duration > 0 else 0.0
            print(
                f"chunk {i}/{total}  elapsed={time.time() - run_start:.1f}s  rtf={rtf:.3f}",
                file=sys.stderr,
            )

    return " ".join(texts).strip(), segments


def cmd_transcribe(args) -> int:
    """Transcribe an audio file.

    Chooses an engine/model from the catalog (--engine/--model, or the
    catalog's recommendation for --language), chunks long audio for engines
    without native long-form support, and writes plain or timestamped
    (srt/vtt/json) output to stdout or --output (WHISP-76).
    """
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

    from .stt import catalog as stt_catalog
    from .stt.formatters import format_json, format_srt, format_txt, format_vtt

    try:
        engine_name, model_name = _resolve_transcribe_model(args, stt_catalog)
    except ValueError as e:
        return _error(str(e))

    # WHISP-86: a one-line, non-blocking license notice for a non-standard-license
    # model (e.g. Nemotron's OpenMDW-1.1) -- same wording/doc pointer as
    # cmd_download_model, since `transcribe` can trigger its download too. Printed
    # to stderr (not stdout) so it never corrupts a machine-readable --format
    # (srt/vtt/json) piped from stdout.
    resolved_entry = stt_catalog.get(engine_name, model_name)
    if resolved_entry is not None:
        notice = _license_notice_for(resolved_entry)
        if notice:
            print(notice, file=sys.stderr)

    try:
        config = WhisperAloudConfig.load()
        config.model.name = model_name
        config.model.engine = engine_name
        config.model.device = args.device
        config.transcription.language = args.language
        if args.no_vad:
            config.transcription.vad_filter = False

        if engine_name == "faster-whisper":
            # Resolved lazily via the module-level `Transcriber` global so
            # existing tests can patch `whisper_aloud.__main__.Transcriber`.
            global Transcriber
            if Transcriber is None:
                from .transcriber import Transcriber as _Transcriber
                Transcriber = _Transcriber
            engine = Transcriber(config)
        else:
            from .stt.engine import create_engine
            engine = create_engine(config)

        if args.verbose:
            print(f"Loading model: {model_name} ({engine_name}) on {args.device}...", file=sys.stderr)

        engine.load_model()

        if args.verbose:
            print(f"Transcribing: {audio_file}...", file=sys.stderr)

        t0 = time.time()
        result = None

        if engine_name == "faster-whisper":
            # faster-whisper handles long audio natively -- no chunking needed.
            vad_kwargs = {"vad_filter": False} if args.no_vad else {}
            result = engine.transcribe_file(str(audio_file), **vad_kwargs)
            whole_text = result.text
            segments = _segments_from_result(result)
            duration = result.duration
        else:
            from .stt.audio_io import load_audio_16k_mono
            from .stt.normalize import normalize_loudness

            samples = load_audio_16k_mono(str(audio_file))
            if args.normalize:
                samples = normalize_loudness(samples, 16000)
            duration = len(samples) / 16000
            whole_text, segments = _transcribe_chunked(
                engine, samples, 16000, args.language, verbose=args.verbose,
            )

        elapsed = time.time() - t0

        if args.format == "txt" and not args.timestamps:
            # Backward compatible with the pre-WHISP-76 output exactly.
            output_text = (whole_text or "") + "\n"
        elif args.format == "txt":
            output_text = format_txt(segments, timestamps=True)
        elif args.format == "srt":
            output_text = format_srt(segments)
        elif args.format == "vtt":
            output_text = format_vtt(segments)
        else:
            language = result.language if engine_name == "faster-whisper" else args.language
            output_text = format_json(segments, meta={"language": language, "duration": duration})

        if args.output:
            Path(args.output).write_text(output_text)
        else:
            sys.stdout.write(output_text)

        if args.verbose:
            rtf = elapsed / duration if duration else 0.0
            print(f"\n--- Metadata ---", file=sys.stderr)
            print(f"Engine: {engine_name} / {model_name}", file=sys.stderr)
            print(f"Duration: {duration:.2f}s", file=sys.stderr)
            print(f"Processing time: {elapsed:.2f}s (rtf={rtf:.3f})", file=sys.stderr)
            if engine_name == "faster-whisper":
                print(f"Language: {result.language}", file=sys.stderr)
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
    """Validate configuration file.

    Uses load(strict=True) (WHISP-75) so a config file that exists but is
    malformed (invalid JSON, or unreadable) is reported as an error here,
    instead of being silently swallowed and reported as "valid" while
    actually falling back to in-memory defaults. Never writes the file.
    """
    config_path = user_config_dir() / "config.json"
    try:
        config = WhisperAloudConfig.load(strict=True)
    except ConfigurationError as e:
        return _error(f"{config_path} is invalid: {e}")
    except Exception as e:
        return _error(f"configuration error: {e}")

    print("Configuration is valid.")
    print(f"  Model:       {config.model.name} on {config.model.device}")
    print(f"  Language:    {config.transcription.language}")
    print(f"  Sample rate: {config.audio.sample_rate} Hz")
    print(f"  VAD:         {'enabled' if config.audio.vad_enabled else 'disabled'}")
    if config.persistence:
        print(f"  Persistence: {'enabled' if config.persistence.save_audio else 'disabled'}")
    return 0


def _config_show(args) -> int:
    """Show current configuration values.

    If the config file exists but is malformed, this falls back to
    defaults (like load()'s lenient default) but, unlike before WHISP-75,
    warns on stderr that it is doing so instead of silently presenting
    defaults as if they were read from the file. Never writes the file.
    """
    config_path = user_config_dir() / "config.json"
    try:
        config = WhisperAloudConfig.load(strict=True)
    except ConfigurationError as e:
        print(
            f"{PROG}: warning: {config_path} is malformed ({e}); showing defaults instead.",
            file=sys.stderr,
        )
        try:
            config = WhisperAloudConfig.load()
        except Exception as e2:
            return _error(f"configuration error: {e2}")
    except Exception as e:
        return _error(f"configuration error: {e}")

    try:
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
        elif getattr(args, 'favorites', False):
            entries = call_service_method("GetFavoriteHistory", args.count)
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


def cmd_history_export(args) -> int:
    """Export transcription history to a file or stdout (WHISP-79).

    Goes through the daemon's ExportHistory D-Bus method, like every other
    history command -- the daemon renders the content server-side and
    returns it as a string; this command is the one that writes it to disk
    (or stdout), since ExportHistory deliberately never writes to a path
    itself (see docs/dbus-api.md's ExportHistory section).
    """
    if not _require_daemon():
        return 1

    # Local import: avoids a hard gi.repository/GLib dependency for every
    # other CLI command that doesn't need a{sv} variant wrapping.
    from .service.client import _wrap_config_changes

    filters = {}
    if getattr(args, "since", None):
        filters["since"] = args.since
    if getattr(args, "until", None):
        filters["until"] = args.until
    if getattr(args, "favorites", False):
        filters["favorites_only"] = True
    if getattr(args, "query", None):
        filters["query"] = args.query
    if getattr(args, "limit", None):
        filters["limit"] = args.limit

    try:
        content = call_service_method(
            "ExportHistory", args.format, _wrap_config_changes(filters)
        )
    except WhisperAloudError as e:
        return _error(str(e))

    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"Exported to {args.output}")
    else:
        print(content)

    return 0


# Doc a tester should read before accepting a needs_review model's license.
_NEEDS_REVIEW_DOC_PATH = "docs/superpowers/specs/2026-10-01-whisp55-nemotron-sherpa-findings.md"

# Reference doc for any model whose license is outside the standard allowlist
# (WHISP-86). Printed as a one-line, non-blocking notice on download -- see
# _license_notice_for() below.
_THIRD_PARTY_MODELS_DOC_PATH = "docs/THIRD_PARTY_MODELS.md"


def _license_notice_for(entry) -> str | None:
    """A one-line, non-blocking license notice for `entry`, or None when its
    license is in the standard allowlist (nothing to call out).

    WHISP-86: a non-standard license (e.g. Nemotron's OpenMDW-1.1, or
    Parakeet's CC-BY-4.0) no longer blocks the CLI behind `--accept-license`
    once its catalog entry is reviewed (`needs_review=False`) -- the user
    still needs to *see* the license and any attribution requirement, just
    not acknowledge it with a flag before every download.
    """
    from .ui.model_picker_logic import is_nonstandard_license

    if not is_nonstandard_license(entry.license):
        return None
    return (
        f"License notice: {entry.label} is distributed under {entry.license}. "
        f"See {_THIRD_PARTY_MODELS_DOC_PATH} for attribution requirements."
    )


def cmd_download_model(args) -> int:
    """Download a speech model bundle to the local cache (no daemon required)."""
    from .stt import catalog
    from .stt.model_download import ensure_model

    entry = next(
        (e for e in catalog.entries() if e.name == args.name or args.name in e.aliases),
        None,
    )
    if entry is not None and entry.needs_review and not getattr(args, "accept_license", False):
        return _error(
            f"model '{args.name}' is pending license review (license: {entry.license}).",
            hint=(
                f"See {_NEEDS_REVIEW_DOC_PATH} for details, then re-run with "
                "--accept-license to proceed."
            ),
        )

    if entry is not None:
        notice = _license_notice_for(entry)
        if notice:
            print(notice)

    try:
        path = ensure_model(args.name)
    except ValueError as e:
        return _error(str(e))
    except Exception as e:  # network / extract failures
        return _error(f"download failed: {e}")
    print(f"Model ready: {path}")
    return 0


def cmd_models(args) -> int:
    """List the STT model catalog as a table (engine, name, size, languages, license).

    Entries pending license review (`needs_review`) are hidden unless `--all` is
    passed, and are marked "(pending license review)" when shown (WHISP-56 audit
    item 4).
    """
    from .stt import catalog

    show_all = getattr(args, "all", False)
    rows = []
    for entry in catalog.entries():
        if entry.needs_review and not show_all:
            continue
        size = f"{entry.size_mb} MB" if entry.size_mb is not None else "—"
        langs = "multilingual" if entry.languages is None else f"{len(entry.languages)} langs"
        rec = "yes" if entry.recommended else ""
        name = entry.name + (" (pending license review)" if entry.needs_review else "")
        rows.append((entry.engine, name, size, langs, entry.license, rec))

    headers = ("engine", "name", "size", "languages", "license", "recommended")
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def _fmt(row) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print(_fmt(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(_fmt(row))

    return 0


def _configure_daemon_logging(verbose: bool = False) -> None:
    """Configure logging for the daemon process (field bug 6).

    Unlike cmd_transcribe (which calls logging.basicConfig for its own
    one-shot run), nothing on the `--daemon` path ever configured logging:
    in production only WARNING+ reached the journal via Python's lastResort
    handler, so INFO lines (e.g. hotkey_portal.py's CreateSession/
    ListShortcuts/BindShortcuts/Activated outcomes) and all DEBUG were
    silently lost, and `--daemon --verbose` changed nothing.

    INFO by default; DEBUG when `verbose` is True or
    WHISPER_ALOUD_LOG_LEVEL=DEBUG/INFO/WARNING/ERROR is set (matching
    WhisperAloudConfig's WHISPER_ALOUD_-prefixed env override convention;
    the env var wins over `verbose` when both are set, since it's the more
    explicit ask). Format is deliberately bare -- journald/systemd already
    timestamps each line, so repeating that here would be redundant noise.
    """
    env_level_name = os.environ.get("WHISPER_ALOUD_LOG_LEVEL", "").strip().upper()
    level = getattr(logging, env_level_name, None)
    if not isinstance(level, int):
        level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def handle_daemon_command(args) -> int:
    """Start the daemon service.

    Acquires the D-Bus name *before* constructing WhisperAloudService: three
    things can try to start this daemon (the systemd unit, the XDG autostart
    fallback, and a manual `whisper-aloud --daemon`), and only one can ever
    own the name. Checking here, first, means a losing attempt fails fast
    with a clean message instead of doing a full audio/tray/model init only
    to crash inside bus.publish() (WHISP-63).
    """
    _configure_daemon_logging(verbose=getattr(args, "verbose", False))

    from pydbus import SessionBus

    from .service.daemon import EXIT_ALREADY_RUNNING, AlreadyRunningError, acquire_bus_name

    bus = SessionBus()
    try:
        name_owner = acquire_bus_name(bus)
    except AlreadyRunningError as e:
        logging.getLogger(__name__).error(str(e))
        print(f"{PROG}: error: {e}", file=sys.stderr)
        return EXIT_ALREADY_RUNNING

    try:
        from .service import WhisperAloudService
        service = WhisperAloudService(bus=bus, name_owner=name_owner)
        service.run()
        return 0
    except AlreadyRunningError as e:
        # Defensive: run() re-checks when it wasn't handed a name_owner.
        # We always pass one above, so this should not normally trigger.
        logging.getLogger(__name__).error(str(e))
        print(f"{PROG}: error: {e}", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
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
  %(prog)s models                   List available STT models
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
    p_download.add_argument(
        "--accept-license", action="store_true",
        help="confirm acceptance of a pending-review model's license (required for some models)",
    )

    # ── Model catalog ─────────────────────────────────────────────────────
    p_models = sub.add_parser(
        "models",
        help="list the STT model catalog",
        description="List all engines and models in the STT catalog with their metadata.",
    )
    p_models.add_argument(
        "--all", action="store_true",
        help="include models pending license review (hidden by default)",
    )

    # ── Transcribe ────────────────────────────────────────────────────────
    from .stt import catalog as _catalog

    engine_choices = _catalog.engines()
    model_choices = sorted({
        alias for entry in _catalog.entries() for alias in (entry.name, *entry.aliases)
    })

    p_transcribe = sub.add_parser(
        "transcribe", help="transcribe an audio file",
        description="Transcribe an audio file, choosing an engine/model from the catalog.",
    )
    p_transcribe.add_argument("file", help="path to audio file")
    p_transcribe.add_argument(
        "--engine", default=None, choices=engine_choices,
        help="STT engine (default: the catalog's recommendation for --language)",
    )
    p_transcribe.add_argument(
        "--model", "-m", default=None, choices=model_choices,
        help="model name (default: the catalog's recommendation for --engine/--language)",
    )
    p_transcribe.add_argument(
        "--accept-license", action="store_true",
        help="confirm acceptance of a pending-review model's license (required for some models)",
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
        "--format", choices=["txt", "srt", "vtt", "json"], default="txt",
        help="output format (default: txt)",
    )
    p_transcribe.add_argument(
        "--timestamps", action="store_true",
        help="include timestamps in txt output (implied by srt/vtt/json)",
    )
    p_transcribe.add_argument(
        "--output", "-o", default=None,
        help="write output to FILE instead of stdout",
    )
    p_transcribe.add_argument(
        "--no-vad", action="store_true",
        help="disable voice-activity-detection filtering for this run",
    )
    p_transcribe.add_argument(
        "--normalize", action="store_true",
        help="apply dynamic loudness normalization to the audio before transcribing",
    )
    p_transcribe.add_argument(
        "--verbose", "-v", action="store_true", default=argparse.SUPPRESS,
        help="show detailed output, including per-chunk progress",
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

    p_list = p_history_sub.add_parser("list", help="list recent history (default)")
    p_list.add_argument(
        "--limit", type=int, default=None,
        help="number of entries to show (default: 10)",
    )
    p_list.add_argument(
        "--favorites", action="store_true",
        help="only show favorited entries",
    )

    p_search = p_history_sub.add_parser("search", help="search history by text")
    p_search.add_argument("search_query", help="search text")

    p_export = p_history_sub.add_parser(
        "export", help="export history to a file or stdout",
        description="Export transcription history, optionally filtered, via the daemon.",
    )
    p_export.add_argument(
        "--format", choices=["md", "json", "csv", "txt"], required=True,
        help="export format",
    )
    p_export.add_argument(
        "--output", "-o", default=None,
        help="write to FILE instead of stdout",
    )
    p_export.add_argument(
        "--since", default=None,
        help="only entries on/after this ISO date or datetime (e.g. 2026-01-01)",
    )
    p_export.add_argument(
        "--until", default=None,
        help="only entries on/before this ISO date or datetime",
    )
    p_export.add_argument(
        "--favorites", action="store_true",
        help="only export favorited entries",
    )
    p_export.add_argument(
        "--query", default=None,
        help="only export entries matching this full-text search",
    )
    p_export.add_argument(
        "--limit", type=int, default=None,
        help="maximum entries to export (daemon default 500, max 5000)",
    )

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
    "models": cmd_models,
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

    # History sub-actions
    if args.command == "history" and getattr(args, 'history_action', None) == "search":
        args.query = args.search_query
        return cmd_history(args)
    if args.command == "history" and getattr(args, 'history_action', None) == "list":
        if args.limit is not None:
            args.count = args.limit
        return cmd_history(args)
    if args.command == "history" and getattr(args, 'history_action', None) == "export":
        return cmd_history_export(args)

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
