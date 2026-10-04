"""Optional GNOME Shell top-bar extension (whisperaloud@fede): pure logic.

The extension lives in ``contrib/gnome-shell-extension/whisperaloud@fede``.
This module holds everything about it that does not need GTK or D-Bus, so
the Settings "Top bar indicator" section and the daemon's tray suppression
stay thin and unit-testable:

- identifiers shared with ``extension.js``;
- classification of the extension's status from what the app can observe;
- the host command to install it, and the export of the bundled zip to a
  host-visible path when running inside Flatpak.

What the app can observe depends on where it runs:

- **Native**: ``org.gnome.Shell.Extensions.GetExtensionInfo`` tells
  installed / enabled / state / version, and Enable/DisableExtension work.
- **Flatpak**: the manifest deliberately does not grant
  ``--talk-name=org.gnome.Shell.Extensions`` (it would allow disabling or
  uninstalling *any* extension, see docs/flatpak.md). The only signal is our
  own extension's bus name (``EXTENSION_BUS_NAME``, which the daemon needs
  anyway for window capture): owned means enabled and running, and its
  ``GetInfo`` gives the version. "Not installed" and "installed but
  disabled" cannot be told apart, so both commands are shown.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import shlex
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

EXTENSION_UUID = "whisperaloud@fede"
# Owned by the extension while enabled (see extension.js EXT_BUS_NAME).
EXTENSION_BUS_NAME = "org.gnome.Shell.Extensions.whisperaloud"
EXTENSION_INFO_PATH = "/org/fede/whisperaloud/ShellExtension"
EXTENSION_INFO_IFACE = "org.fede.whisperaloud.ShellExtension"

# GNOME Shell's own extension-management service.
SHELL_EXTENSIONS_BUS_NAME = "org.gnome.Shell.Extensions"
SHELL_EXTENSIONS_PATH = "/org/gnome/Shell/Extensions"
SHELL_EXTENSIONS_IFACE = "org.gnome.Shell.Extensions"

ZIP_NAME = f"{EXTENSION_UUID}.shell-extension.zip"
_SHARE_SUBDIR = Path("whisper-aloud") / "gnome-shell-extension"
# Built into the Flatpak by the manifest (scripts/pack_shell_extension.py).
FLATPAK_ZIP_PATH = Path("/app/share") / _SHARE_SUBDIR / ZIP_NAME

ENABLE_COMMAND = f"gnome-extensions enable {EXTENSION_UUID}"
DISABLE_COMMAND = f"gnome-extensions disable {EXTENSION_UUID}"

# GNOME Shell ExtensionState (js/misc/extensionUtils.js, unchanged 45-48).
STATE_ACTIVE = 1
STATE_INACTIVE = 2
STATE_ERROR = 3
STATE_OUT_OF_DATE = 4
STATE_DOWNLOADING = 5
STATE_INITIALIZED = 6
STATE_DEACTIVATING = 7
STATE_ACTIVATING = 8
STATE_UNINSTALLED = 99


class Status(enum.Enum):
    NOT_GNOME = "not-gnome"
    ACTIVE = "active"
    OUTDATED = "outdated"
    DISABLED = "disabled"
    ERROR = "error"
    NOT_INSTALLED = "not-installed"
    UNKNOWN = "unknown"


STATUS_SUMMARIES = {
    Status.NOT_GNOME: "Only available on GNOME Shell.",
    Status.ACTIVE: "Installed and running.",
    Status.OUTDATED: (
        "An older version is installed. Reinstall it with the command below, "
        "then log out and back in."
    ),
    Status.DISABLED: "Installed but disabled.",
    Status.ERROR: (
        "GNOME Shell reports an error or an incompatible version for the "
        "extension. Reinstall it with the command below and log out and back in."
    ),
    Status.NOT_INSTALLED: (
        "Not installed. Install it with the command below, then log out and "
        "back in (GNOME on Wayland only loads new extensions at login) and enable it."
    ),
    Status.UNKNOWN: (
        "Not running. It is either not installed or disabled; the sandboxed app "
        "cannot tell which. Install it with the first command (then log out and "
        "back in), or enable it with the second."
    ),
}


@dataclass(frozen=True)
class ShellInfo:
    """What org.gnome.Shell.Extensions.GetExtensionInfo says about us."""

    installed: bool
    enabled: bool
    state: Optional[int]
    version: Optional[int]


@dataclass(frozen=True)
class ExtensionReport:
    status: Status
    summary: str
    installed_version: Optional[int]
    bundled_version: Optional[int]
    show_install_command: bool
    show_enable_command: bool
    # "enable" / "disable" via org.gnome.Shell.Extensions (native only).
    toggle_action: Optional[str]


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def is_gnome_desktop(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    desktops = (env.get("XDG_CURRENT_DESKTOP") or "").lower().split(":")
    return "gnome" in desktops


def parse_shell_info(info: Mapping[str, Any]) -> ShellInfo:
    """Parse an unpacked GetExtensionInfo() dict ({} for unknown uuids)."""
    if not info:
        return ShellInfo(installed=False, enabled=False, state=None, version=None)
    state = _as_int(info.get("state"))
    if state == STATE_UNINSTALLED:
        return ShellInfo(installed=False, enabled=False, state=state, version=None)
    enabled = info.get("enabled")
    if not isinstance(enabled, bool):
        enabled = state in (STATE_ACTIVE, STATE_ACTIVATING)
    return ShellInfo(installed=True, enabled=enabled, state=state,
                     version=_as_int(info.get("version")))


def _outdated(installed: Optional[int], bundled: Optional[int]) -> bool:
    if bundled is None:
        return False
    # A running extension without a version (v1, no GetInfo) is older than
    # anything we ship.
    return installed is None or installed < bundled


def classify(
    *,
    is_gnome: bool,
    sandboxed: bool,
    shell_info: Optional[ShellInfo],
    running_info: Optional[Mapping[str, Any]],
    bundled_version: Optional[int],
) -> ExtensionReport:
    """Combine everything observable into one report for the Settings UI.

    shell_info: parsed GetExtensionInfo, or None when org.gnome.Shell.Extensions
        could not be queried (always None inside Flatpak).
    running_info: our extension's GetInfo() dict when its bus name is owned
        ({} for the v1 extension, which owned the name but had no GetInfo),
        None when the name has no owner.
    """

    def report(status, installed_version=None, install=False, enable=False, toggle=None):
        return ExtensionReport(
            status=status,
            summary=STATUS_SUMMARIES[status],
            installed_version=installed_version,
            bundled_version=bundled_version,
            show_install_command=install,
            show_enable_command=enable,
            toggle_action=toggle,
        )

    if not is_gnome:
        return report(Status.NOT_GNOME)

    can_toggle = shell_info is not None and not sandboxed

    if shell_info is not None and shell_info.installed:
        version = shell_info.version
        if running_info:
            version = _as_int(running_info.get("version")) or version
        state = shell_info.state
        if state in (STATE_ERROR, STATE_OUT_OF_DATE):
            return report(Status.ERROR, version, install=True,
                          toggle="disable" if can_toggle else None)
        if shell_info.enabled and state in (STATE_ACTIVE, STATE_ACTIVATING, None):
            if _outdated(version, bundled_version):
                return report(Status.OUTDATED, version, install=True,
                              toggle="disable" if can_toggle else None)
            return report(Status.ACTIVE, version, toggle="disable" if can_toggle else None)
        return report(Status.DISABLED, version, toggle="enable" if can_toggle else None,
                      enable=not can_toggle)

    if running_info is not None:
        version = _as_int(running_info.get("version"))
        if _outdated(version, bundled_version):
            return report(Status.OUTDATED, version, install=True)
        return report(Status.ACTIVE, version)

    if shell_info is not None:
        return report(Status.NOT_INSTALLED, install=True, enable=True)
    return report(Status.UNKNOWN, install=True, enable=True)


def install_command(zip_path: Path) -> str:
    """Host shell command that installs (or upgrades) the extension."""
    return f"gnome-extensions install --force {shlex.quote(str(zip_path))}"


def host_export_path(data_home: Path) -> Path:
    """Where the Flatpak app copies the zip so the host can read it.

    Inside Flatpak, XDG_DATA_HOME is ~/.var/app/<app-id>/data -- the same
    absolute path on the host, so the command shown works verbatim there.
    """
    return Path(data_home) / _SHARE_SUBDIR / ZIP_NAME


def bundled_zip_candidates(*, sandboxed: bool, prefix: Optional[Path] = None) -> list[Path]:
    """Places the packed extension zip may be installed to, best first."""
    prefix = Path(sys.prefix) if prefix is None else Path(prefix)
    native = [prefix / "share" / _SHARE_SUBDIR / ZIP_NAME,
              Path("/usr/share") / _SHARE_SUBDIR / ZIP_NAME]
    out: list[Path] = [FLATPAK_ZIP_PATH] if sandboxed else []
    for p in native:
        if p not in out:
            out.append(p)
    return out


def find_bundled_zip(*, sandboxed: bool, prefix: Optional[Path] = None) -> Optional[Path]:
    for candidate in bundled_zip_candidates(sandboxed=sandboxed, prefix=prefix):
        if candidate.is_file():
            return candidate
    return None


def read_zip_version(zip_path: Path) -> Optional[int]:
    """metadata.json "version" of a packed extension, or None."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            metadata = json.loads(zf.read("metadata.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return _as_int(metadata.get("version"))


def read_extension_dir_version(ext_dir: Path) -> Optional[int]:
    try:
        metadata = json.loads((Path(ext_dir) / "metadata.json").read_text())
    except (OSError, ValueError):
        return None
    return _as_int(metadata.get("version"))


def export_zip(src: Path, dest: Path) -> bool:
    """Copy src to dest unless an identical copy is already there.

    Returns True when dest holds src's content afterwards.
    """
    try:
        data = Path(src).read_bytes()
    except OSError:
        return False
    try:
        if Path(dest).read_bytes() == data:
            return True
    except OSError:
        pass
    try:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(dest).with_name(Path(dest).name + ".tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except OSError as e:
        logger.warning("Could not export the GNOME Shell extension zip to %s: %s", dest, e)
        return False
    return True


def resolve_install_zip(*, sandboxed: bool, data_home: Path, bundled: Optional[Path]) -> Optional[Path]:
    """Host-visible path of the zip to install, exporting it if sandboxed."""
    if bundled is None or not Path(bundled).is_file():
        return None
    if not sandboxed:
        return Path(bundled)
    dest = host_export_path(data_home)
    return dest if export_zip(bundled, dest) else None


_NO_OBJECT_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownObject",
        "org.freedesktop.DBus.Error.UnknownInterface",
    }
)


def running_info_from_dbus_error(error_name: str) -> Optional[dict]:
    """Interpret a failed GetInfo call on our extension's bus name.

    The name answered but has no ShellExtension object -> the v1 extension is
    running ({}). Anything else (no owner, access denied, timeout) -> treat
    it as not running (None).
    """
    return {} if error_name in _NO_OBJECT_ERRORS else None


STATUS_TITLES = {
    Status.NOT_GNOME: "Not available",
    Status.ACTIVE: "Running",
    Status.OUTDATED: "Update available",
    Status.DISABLED: "Disabled",
    Status.ERROR: "Error",
    Status.NOT_INSTALLED: "Not installed",
    Status.UNKNOWN: "Not running",
}


def status_title(report: ExtensionReport) -> str:
    title = STATUS_TITLES[report.status]
    if report.status == Status.ACTIVE and report.installed_version is not None:
        title = f"{title} (version {report.installed_version})"
    return title


@dataclass(frozen=True)
class SectionView:
    """What the Settings "Top bar indicator" section shows."""

    title: str
    summary: str
    install_command: Optional[str]
    enable_command: Optional[str]
    disable_command: Optional[str]
    # "Enable"/"Disable" button via org.gnome.Shell.Extensions (native only).
    toggle_label: Optional[str]
    # The install command is needed but this installation has no zip.
    package_missing: bool


def section_view(report: ExtensionReport, install_zip: Optional[Path]) -> SectionView:
    install_cmd = None
    package_missing = False
    if report.show_install_command:
        if install_zip is not None:
            install_cmd = install_command(install_zip)
        else:
            package_missing = True
    disable_cmd = None
    if report.toggle_action is None and report.status in (
        Status.ACTIVE, Status.OUTDATED, Status.ERROR
    ):
        disable_cmd = DISABLE_COMMAND
    toggle_label = {"enable": "Enable", "disable": "Disable"}.get(report.toggle_action or "")
    return SectionView(
        title=status_title(report),
        summary=report.summary,
        install_command=install_cmd,
        enable_command=ENABLE_COMMAND if report.show_enable_command else None,
        disable_command=disable_cmd,
        toggle_label=toggle_label,
        package_missing=package_missing,
    )


def tray_should_show(*, extension_present: bool) -> bool:
    """The daemon's AppIndicator tray icon duplicates the extension's
    top-bar indicator; hide it while the extension is running."""
    return not extension_present
