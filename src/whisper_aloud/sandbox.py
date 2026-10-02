"""Flatpak sandbox detection (WHISP-59).

Pure logic, deliberately free of any GTK/D-Bus/subprocess dependency so it
is trivially unit-testable and safe to call early (e.g. before the daemon
decides which text-insertion/hotkey backend to use).

Detection mirrors what Flatpak itself guarantees inside a sandboxed app:
- ``FLATPAK_ID`` is exported into the sandbox environment, and
- ``/.flatpak-info`` is bind-mounted read-only into every sandboxed app.

Either signal is sufficient; both are checked because a caller might clear
one but not the other in unusual test/container setups.
"""

import os
from pathlib import Path
from typing import Mapping, Optional, Union

DEFAULT_FLATPAK_INFO_PATH = Path("/.flatpak-info")


def is_flatpak_sandbox(
    flatpak_info_path: Union[str, Path] = DEFAULT_FLATPAK_INFO_PATH,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Return True if running inside a Flatpak sandbox.

    Args:
        flatpak_info_path: path to check for existence (injectable for
            tests). Defaults to the real ``/.flatpak-info``.
        env: environment mapping to check for ``FLATPAK_ID`` (injectable
            for tests). Defaults to ``os.environ``.

    Never raises: an unreadable/invalid path is treated as "not sandboxed"
    rather than propagating an OSError.
    """
    if env is None:
        env = os.environ

    if (env.get("FLATPAK_ID") or "").strip():
        return True

    try:
        return Path(flatpak_info_path).exists()
    except OSError:
        return False
