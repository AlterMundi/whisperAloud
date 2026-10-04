"""Native (non-Flatpak) "start in background at login" (WHISP-87).

Writes or removes one XDG autostart entry that starts the *daemon only*
(``whisper-aloud --daemon``), never the window. Inside Flatpak this module
is not used: the app cannot write the host's autostart directory, so the GUI
asks the Background portal instead (see ``background_portal.py``).

Pure file logic, no GTK/D-Bus, every function takes the autostart directory
explicitly so tests never resolve the user's real one.

Naming: the entry reuses ``whisper-aloud-autostart.desktop``, the name
``install.sh`` and ``contrib/`` already use, so turning the setting on after
an installer run reuses that entry instead of adding a second one. Other
names that also start WhisperAloud at login (the pre-WHISP-63 legacy entry,
the manual Flatpak workaround from docs/flatpak.md, and the entry the
Background portal writes for the Flatpak) are detected and reported, never
modified or deleted: they were not created by this setting.

The Exec line follows the same WHISP-63 rules as the contrib entry: when the
systemd user unit is installed it asks systemd to start it (idempotent, no
race for the bus name); only otherwise does it spawn the daemon directly.
"""

import logging
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

MANAGED_FILENAME = "whisper-aloud-autostart.desktop"
MANAGED_MARKER = "X-WhisperAloud-Managed"

# Entries this setting did not create but that also start WhisperAloud at
# login: legacy pre-WHISP-63 entry, the manual Flatpak workaround from
# docs/flatpak.md, and the entry the Background portal writes for the
# Flatpak app (named after its app id).
OTHER_ENTRY_FILENAMES: Tuple[str, ...] = (
    "whisperaloud-daemon.desktop",
    "org.fede.whisperaloud-daemon.desktop",
    "org.fede.whisperaloud.desktop",
)

# Characters that survive the Exec= line unquoted: no whitespace, quotes,
# backslashes, `$` or `%` (desktop-entry field codes).
_SAFE_COMMAND = re.compile(r"^/[A-Za-z0-9/._+-]+( -m [A-Za-z0-9_.]+)?$")


@dataclass(frozen=True)
class NativeAutostartState:
    """What the autostart directory says, after a read/enable/disable."""

    path: Path
    enabled: bool
    # True only when enable() wrote the file during this call.
    created: bool = False
    # Other enabled entries that also start WhisperAloud (see module docstring).
    others: Tuple[Path, ...] = ()


def autostart_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """``$XDG_CONFIG_HOME/autostart``, or ``~/.config/autostart``.

    A relative XDG_CONFIG_HOME is invalid per the XDG spec and ignored.
    """
    if env is None:
        env = os.environ
    base = env.get("XDG_CONFIG_HOME", "")
    if not base or not os.path.isabs(base):
        home = env.get("HOME") or str(Path.home())
        base = os.path.join(home, ".config")
    return Path(base) / "autostart"


def daemon_command(
    which: Callable[[str], Optional[str]] = shutil.which,
    executable: str = sys.executable,
) -> str:
    """The command that starts this installation's daemon (without --daemon)."""
    launcher = which("whisper-aloud")
    if launcher:
        return launcher
    return f"{executable} -m whisper_aloud"


def build_desktop_entry(command: str) -> str:
    """Autostart entry text for ``<command> --daemon``.

    Raises ValueError for a command that cannot be put in Exec= safely
    (relative path, whitespace, quotes, ``$`` or ``%``).
    """
    if not _SAFE_COMMAND.match(command):
        raise ValueError(f"cannot write an autostart entry for command {command!r}")
    script = (
        "if command -v systemctl >/dev/null 2>&1 && "
        "systemctl --user cat whisper-aloud.service >/dev/null 2>&1; "
        "then systemctl --user start whisper-aloud.service && exit 0; fi; "
        f"exec {command} --daemon"
    )
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=WhisperAloud Daemon\n"
        "GenericName=Voice Transcription Daemon\n"
        "Comment=Start WhisperAloud in the background at login (no window)\n"
        f"Exec=sh -lc '{script}'\n"
        "Icon=audio-input-microphone-symbolic\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=3\n"
        "NoDisplay=true\n"
        "StartupNotify=false\n"
        f"{MANAGED_MARKER}=true\n"
    )


def entry_is_enabled(text: str) -> bool:
    """False when the entry opts out with Hidden=true or
    X-GNOME-Autostart-enabled=false, True otherwise."""
    for raw in text.splitlines():
        key, sep, value = raw.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip().lower()
        if key == "Hidden" and value == "true":
            return False
        if key == "X-GNOME-Autostart-enabled" and value == "false":
            return False
    return True


def _enabled_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        return entry_is_enabled(path.read_text(errors="replace"))
    except OSError as e:
        logger.debug("Cannot read autostart entry %s: %s", path, e)
        return False


def read_state(directory: Path) -> NativeAutostartState:
    """Inspect ``directory`` (an autostart dir). Never raises."""
    directory = Path(directory)
    path = directory / MANAGED_FILENAME
    others = tuple(
        directory / name for name in OTHER_ENTRY_FILENAMES if _enabled_file(directory / name)
    )
    return NativeAutostartState(path=path, enabled=_enabled_file(path), others=others)


def enable(directory: Path, command: Optional[str] = None) -> NativeAutostartState:
    """Make sure the daemon starts at login.

    An already-enabled entry with the managed name is reused as is (it may
    come from install.sh with a venv path this process cannot know). A
    missing or opted-out one is (re)written atomically.

    Raises OSError if the directory or file cannot be written, ValueError if
    the command cannot be quoted safely.
    """
    directory = Path(directory)
    current = read_state(directory)
    if current.enabled:
        return current
    text = build_desktop_entry(command or daemon_command())
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wa-autostart-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, current.path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.info("Wrote autostart entry %s", current.path)
    state = read_state(directory)
    return NativeAutostartState(
        path=state.path, enabled=state.enabled, created=True, others=state.others
    )


def disable(directory: Path) -> NativeAutostartState:
    """Remove the managed entry, if present. Other entries are left alone
    and reported in ``others``. Raises OSError if removal fails."""
    directory = Path(directory)
    path = directory / MANAGED_FILENAME
    try:
        path.unlink()
        logger.info("Removed autostart entry %s", path)
    except FileNotFoundError:
        pass
    return read_state(directory)
