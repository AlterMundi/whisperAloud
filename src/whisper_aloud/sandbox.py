"""Flatpak sandbox detection (WHISP-59).

Pure logic, deliberately free of any GTK/D-Bus/subprocess dependency so it
is trivially unit-testable and safe to call early (e.g. before the daemon
decides which text-insertion/hotkey backend to use).

``/.flatpak-info`` -- bind-mounted read-only into every sandboxed app by
the Flatpak runtime itself -- is the *only* authoritative signal. It is
not something an app or its subprocesses can spoof: it comes from the
bwrap container setup, not from anything the app's own environment
carries around.

``FLATPAK_ID``, by contrast, is just an environment variable, and
environment variables leak across process boundaries in ways that are
*not* "inside the sandbox": `flatpak-spawn --host` (running a command on
the real host from inside a sandboxed app) still has `FLATPAK_ID` set in
its environment by default, and a developer's shell can export it by
accident (or a stray `.bashrc` line) without putting that shell inside
any sandbox at all. Treating `FLATPAK_ID` alone as sufficient would
therefore risk classifying a real host shell as "sandboxed" and routing
it to the portal-only paste/hotkey path where ydotool/xdotool/keybinder
would have worked fine -- the safer failure mode is the other way around
(audit finding, WHISP-59): `/.flatpak-info` is authoritative, and
`FLATPAK_ID` without it is logged at debug and treated as NOT sandboxed.

The tradeoff this accepts: a hypothetical environment that deliberately
sets `FLATPAK_ID` without the bind-mount (e.g. some non-standard
container tooling) will not be detected as sandboxed. That is considered
acceptable because `/.flatpak-info`'s presence is what Flatpak itself
guarantees and is the only thing this module can't be fooled about by a
leaked env var.
"""

import logging
import os
from pathlib import Path
from typing import Mapping, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_FLATPAK_INFO_PATH = Path("/.flatpak-info")


def is_flatpak_sandbox(
    flatpak_info_path: Union[str, Path] = DEFAULT_FLATPAK_INFO_PATH,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Return True if running inside a Flatpak sandbox.

    Args:
        flatpak_info_path: path to check for existence (injectable for
            tests). Defaults to the real ``/.flatpak-info``. This is the
            sole authoritative signal -- see module docstring.
        env: environment mapping, checked only to produce a debug log line
            when ``FLATPAK_ID`` is set without the sandbox actually being
            present (injectable for tests). Defaults to ``os.environ``.

    Never raises: an unreadable/invalid path is treated as "not sandboxed"
    rather than propagating an OSError.
    """
    if env is None:
        env = os.environ

    try:
        if Path(flatpak_info_path).exists():
            return True
    except OSError:
        return False

    if (env.get("FLATPAK_ID") or "").strip():
        logger.debug(
            "FLATPAK_ID is set but %s is absent; not treating this process as "
            "sandboxed (FLATPAK_ID alone can leak via `flatpak-spawn --host` "
            "or a stray shell export, so it is not authoritative on its own)",
            flatpak_info_path,
        )

    return False
