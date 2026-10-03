"""Pure text helpers for "daemon isn't running" messaging (field bugs 3 & 5).

Both the GUI (ui/main_window.py) and the CLI (__main__.py) need to tell the
user how to start the daemon when a connection attempt ultimately fails. The
right instructions depend on whether this process is running inside a
Flatpak sandbox (see sandbox.is_flatpak_sandbox()): `systemctl --user ...`
and a bare `whisper-aloud-daemon`/`whisper-aloud --daemon` invocation do not
work there -- only `flatpak run --command=... org.fede.whisperaloud --daemon`
does, and even that should rarely be necessary since the daemon is normally
started automatically via D-Bus activation.

Kept here, free of GTK/argparse/D-Bus, so both callers (and their tests) can
use the exact same wording without importing each other.
"""

FLATPAK_RUN_DAEMON_CMD = "flatpak run --command=whisper-aloud org.fede.whisperaloud --daemon"


def gui_daemon_unavailable_hint(*, is_flatpak: bool, has_user_service: bool) -> str:
    """Multi-line hint text for the GUI's "daemon not running" dialog.

    `has_user_service` (whether ~/.config/systemd/user/whisper-aloud.service
    exists) is only consulted outside a Flatpak sandbox -- systemd user units
    are irrelevant/inapplicable inside one.
    """
    if is_flatpak:
        return (
            "WhisperAloud normally starts the daemon automatically via D-Bus\n"
            "activation. If it doesn't, start it manually with:\n\n"
            f"  {FLATPAK_RUN_DAEMON_CMD}"
        )

    if has_user_service:
        return (
            "Start the daemon with:\n\n"
            "  systemctl --user start whisper-aloud\n\n"
            "Or run directly:\n\n"
            "  whisper-aloud-daemon\n"
            "  whisper-aloud --daemon"
        )

    return (
        "Start the daemon with:\n\n"
        "  whisper-aloud-daemon\n"
        "  whisper-aloud --daemon\n\n"
        "Tip: run ./install.sh to enable systemd user service integration."
    )


def cli_daemon_unavailable_hint(*, is_flatpak: bool) -> str:
    """Hint text for the CLI's `_error("daemon is not running", hint)` line."""
    if is_flatpak:
        return (
            "WhisperAloud normally starts automatically via D-Bus activation.\n"
            f"  If it doesn't, start it with: {FLATPAK_RUN_DAEMON_CMD}"
        )

    return (
        "Start it with: whisper-aloud --daemon\n"
        "  Or via systemd: systemctl --user start whisper-aloud"
    )
