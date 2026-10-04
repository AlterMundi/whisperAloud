"""Pure logic for the optional GNOME Shell top-bar extension (whisperaloud@fede).

Covers whisper_aloud.shell_extension: status classification for the Settings
"Top bar indicator" section, the host install command, the Flatpak export of
the bundled zip, and the tray-suppression rule. No GTK, no D-Bus.
"""

import json
import zipfile
from pathlib import Path

import pytest

from whisper_aloud import shell_extension as se

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = REPO_ROOT / "contrib" / "gnome-shell-extension" / "whisperaloud@fede"


# ── identifiers must match the extension source ────────────────────────────


def test_identifiers_match_extension_source():
    metadata = json.loads((EXT_DIR / "metadata.json").read_text())
    js = (EXT_DIR / "extension.js").read_text()
    assert metadata["uuid"] == se.EXTENSION_UUID
    assert f"'{se.EXTENSION_BUS_NAME}'" in js
    assert f"'{se.EXTENSION_INFO_PATH}'" in js
    assert f"'{se.EXTENSION_INFO_IFACE}'" in js


def test_source_version_reads_metadata():
    metadata = json.loads((EXT_DIR / "metadata.json").read_text())
    assert se.read_extension_dir_version(EXT_DIR) == metadata["version"]


def test_metadata_url_points_at_real_repo():
    metadata = json.loads((EXT_DIR / "metadata.json").read_text())
    assert metadata["url"] == "https://github.com/AlterMundi/whisperAloud"


# ── desktop detection ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("GNOME", True), ("ubuntu:GNOME", True), ("gnome-classic:GNOME", True),
     ("KDE", False), ("", False)],
)
def test_is_gnome_desktop(value, expected):
    assert se.is_gnome_desktop({"XDG_CURRENT_DESKTOP": value}) is expected


def test_is_gnome_desktop_missing_var():
    assert se.is_gnome_desktop({}) is False


# ── org.gnome.Shell.Extensions GetExtensionInfo parsing ──────────────────────


def test_parse_shell_info_empty_means_not_installed():
    info = se.parse_shell_info({})
    assert info.installed is False
    assert info.enabled is False


def test_parse_shell_info_enabled_active():
    info = se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": 1.0, "enabled": True, "version": 2.0}
    )
    assert info.installed and info.enabled
    assert info.state == se.STATE_ACTIVE
    assert info.version == 2


def test_parse_shell_info_without_enabled_key_uses_state():
    # Older shells may omit "enabled"; ACTIVE implies enabled.
    info = se.parse_shell_info({"uuid": se.EXTENSION_UUID, "state": 1.0})
    assert info.enabled is True
    info = se.parse_shell_info({"uuid": se.EXTENSION_UUID, "state": 2.0})
    assert info.enabled is False


def test_parse_shell_info_uninstalled_state():
    info = se.parse_shell_info({"uuid": se.EXTENSION_UUID, "state": 99.0})
    assert info.installed is False


def test_parse_shell_info_tolerates_garbage():
    info = se.parse_shell_info({"uuid": se.EXTENSION_UUID, "state": "x", "version": "y"})
    assert info.installed is True
    assert info.version is None


# ── classification ───────────────────────────────────────────────────────────


def _classify(**kw):
    defaults = dict(
        is_gnome=True, sandboxed=False, shell_info=None, running_info=None,
        bundled_version=2,
    )
    defaults.update(kw)
    return se.classify(**defaults)


def test_not_gnome():
    r = _classify(is_gnome=False)
    assert r.status == se.Status.NOT_GNOME
    assert not r.show_install_command
    assert r.toggle_action is None


def test_running_extension_is_active_even_without_shell_info():
    # Flatpak: no org.gnome.Shell.Extensions access, but our own extension's
    # bus name answers.
    r = _classify(sandboxed=True, running_info={"version": 2})
    assert r.status == se.Status.ACTIVE
    assert not r.show_install_command
    assert r.installed_version == 2


def test_running_older_extension_is_outdated():
    r = _classify(sandboxed=True, running_info={"version": 1})
    assert r.status == se.Status.OUTDATED
    assert r.show_install_command


def test_legacy_extension_without_info_interface_is_outdated():
    # Version 1 of the extension owned the bus name but had no GetInfo.
    r = _classify(sandboxed=True, running_info={})
    assert r.status == se.Status.OUTDATED
    assert r.show_install_command


def test_newer_installed_than_bundled_is_still_active():
    r = _classify(running_info={"version": 5}, bundled_version=2)
    assert r.status == se.Status.ACTIVE


def test_native_not_installed():
    r = _classify(shell_info=se.parse_shell_info({}))
    assert r.status == se.Status.NOT_INSTALLED
    assert r.show_install_command
    assert r.toggle_action is None


def test_native_installed_disabled_offers_enable():
    r = _classify(shell_info=se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": 2.0, "enabled": False, "version": 2.0}))
    assert r.status == se.Status.DISABLED
    assert r.toggle_action == "enable"
    assert not r.show_install_command


def test_native_active_offers_disable():
    r = _classify(
        shell_info=se.parse_shell_info(
            {"uuid": se.EXTENSION_UUID, "state": 1.0, "enabled": True, "version": 2.0}),
        running_info={"version": 2},
    )
    assert r.status == se.Status.ACTIVE
    assert r.toggle_action == "disable"


def test_native_active_outdated_per_shell_info():
    r = _classify(shell_info=se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": 1.0, "enabled": True, "version": 1.0}))
    assert r.status == se.Status.OUTDATED
    assert r.show_install_command
    assert r.toggle_action == "disable"


@pytest.mark.parametrize("state", [3.0, 4.0])
def test_native_error_or_out_of_date(state):
    r = _classify(shell_info=se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": state, "enabled": True, "version": 2.0}))
    assert r.status == se.Status.ERROR
    assert r.toggle_action == "disable"


@pytest.mark.parametrize("state", [6.0, 7.0])
def test_native_initialized_or_deactivating_counts_as_disabled(state):
    # INITIALIZED: loaded by the shell but never enabled.
    r = _classify(shell_info=se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": state, "enabled": False, "version": 2.0}))
    assert r.status == se.Status.DISABLED
    assert r.toggle_action == "enable"


def test_not_installed_summary_mentions_relogin():
    # A freshly `gnome-extensions install`ed extension is invisible to a
    # Wayland session until the next login.
    assert "log out" in se.STATUS_SUMMARIES[se.Status.NOT_INSTALLED].lower()


def test_sandboxed_without_running_extension_is_unknown():
    # Inside Flatpak we cannot tell "not installed" from "installed but
    # disabled": show both the install and the enable command.
    r = _classify(sandboxed=True)
    assert r.status == se.Status.UNKNOWN
    assert r.show_install_command
    assert r.show_enable_command
    assert r.toggle_action is None


def test_native_shell_unreachable_is_unknown():
    r = _classify(shell_info=None)
    assert r.status == se.Status.UNKNOWN


def test_every_status_has_a_summary():
    for status in se.Status:
        assert se.STATUS_SUMMARIES[status]


# ── commands ─────────────────────────────────────────────────────────────────


def test_install_command_quotes_path():
    cmd = se.install_command(Path("/home/a b/x.zip"))
    assert cmd == "gnome-extensions install --force '/home/a b/x.zip'"


def test_enable_disable_commands():
    assert se.ENABLE_COMMAND == "gnome-extensions enable whisperaloud@fede"
    assert se.DISABLE_COMMAND == "gnome-extensions disable whisperaloud@fede"


# ── bundled zip location and host export ─────────────────────────────────────


def test_host_export_path(tmp_path):
    p = se.host_export_path(tmp_path)
    assert p == tmp_path / "whisper-aloud" / "gnome-shell-extension" / se.ZIP_NAME


def test_flatpak_zip_path_constant():
    assert str(se.FLATPAK_ZIP_PATH) == (
        "/app/share/whisper-aloud/gnome-shell-extension/whisperaloud@fede.shell-extension.zip"
    )


def _make_zip(path: Path, version: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"uuid": se.EXTENSION_UUID, "version": version}))
        zf.writestr("extension.js", "// js\n")
    return path


def test_read_zip_version(tmp_path):
    z = _make_zip(tmp_path / "e.zip", 7)
    assert se.read_zip_version(z) == 7


def test_read_zip_version_missing_or_bad(tmp_path):
    assert se.read_zip_version(tmp_path / "nope.zip") is None
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    assert se.read_zip_version(bad) is None


def test_export_zip_copies_and_is_idempotent(tmp_path):
    src = _make_zip(tmp_path / "app" / se.ZIP_NAME, 2)
    dest = se.host_export_path(tmp_path / "data")
    assert se.export_zip(src, dest) is True
    assert dest.read_bytes() == src.read_bytes()
    mtime = dest.stat().st_mtime_ns
    assert se.export_zip(src, dest) is True
    assert dest.stat().st_mtime_ns == mtime  # unchanged content: not rewritten


def test_export_zip_replaces_stale_copy(tmp_path):
    src = _make_zip(tmp_path / "app" / se.ZIP_NAME, 3)
    dest = _make_zip(se.host_export_path(tmp_path / "data"), 2)
    assert se.export_zip(src, dest) is True
    assert se.read_zip_version(dest) == 3


def test_export_zip_missing_source(tmp_path):
    assert se.export_zip(tmp_path / "missing.zip", tmp_path / "out.zip") is False


def test_resolve_install_zip_sandboxed_exports(tmp_path):
    src = _make_zip(tmp_path / "app" / se.ZIP_NAME, 2)
    data_home = tmp_path / "data"
    path = se.resolve_install_zip(sandboxed=True, data_home=data_home, bundled=src)
    assert path == se.host_export_path(data_home)
    assert path.exists()


def test_resolve_install_zip_native_uses_bundled_in_place(tmp_path):
    src = _make_zip(tmp_path / "share" / se.ZIP_NAME, 2)
    path = se.resolve_install_zip(sandboxed=False, data_home=tmp_path / "d", bundled=src)
    assert path == src


def test_resolve_install_zip_none_when_missing(tmp_path):
    assert se.resolve_install_zip(
        sandboxed=True, data_home=tmp_path, bundled=tmp_path / "missing.zip") is None


def test_bundled_zip_candidates_order(tmp_path):
    cands = se.bundled_zip_candidates(sandboxed=True, prefix=tmp_path)
    assert cands[0] == se.FLATPAK_ZIP_PATH
    native = se.bundled_zip_candidates(sandboxed=False, prefix=tmp_path)
    assert native[0] == tmp_path / "share" / "whisper-aloud" / "gnome-shell-extension" / se.ZIP_NAME
    assert se.FLATPAK_ZIP_PATH not in native


# ── tray suppression ─────────────────────────────────────────────────────────


def test_tray_hidden_while_extension_present():
    assert se.tray_should_show(extension_present=False) is True
    assert se.tray_should_show(extension_present=True) is False


# ── D-Bus error mapping for our extension's GetInfo ──────────────────────────


@pytest.mark.parametrize(
    "error,expected",
    [
        ("org.freedesktop.DBus.Error.ServiceUnknown", None),
        ("org.freedesktop.DBus.Error.NameHasNoOwner", None),
        ("org.freedesktop.DBus.Error.AccessDenied", None),
        # v1 extension: owns the name, but has no ShellExtension object.
        ("org.freedesktop.DBus.Error.UnknownMethod", {}),
        ("org.freedesktop.DBus.Error.UnknownObject", {}),
        ("org.freedesktop.DBus.Error.UnknownInterface", {}),
        ("", None),
    ],
)
def test_running_info_from_dbus_error(error, expected):
    assert se.running_info_from_dbus_error(error) == expected


# ── Settings section view model ──────────────────────────────────────────────


def _report_for(status):
    return se.ExtensionReport(
        status=status, summary=se.STATUS_SUMMARIES[status], installed_version=2,
        bundled_version=2, show_install_command=False, show_enable_command=False,
        toggle_action=None,
    )


def test_every_status_has_a_title():
    for status in se.Status:
        assert se.status_title(_report_for(status))


def test_status_title_includes_version_when_active():
    assert se.status_title(_report_for(se.Status.ACTIVE)) == "Running (version 2)"


def test_view_not_installed_with_zip(tmp_path):
    zip_path = tmp_path / "x y.zip"
    view = se.section_view(_classify(shell_info=se.parse_shell_info({})), zip_path)
    assert view.install_command == se.install_command(zip_path)
    assert view.enable_command == se.ENABLE_COMMAND
    assert view.toggle_label is None
    assert view.package_missing is False


def test_view_install_needed_but_no_zip():
    view = se.section_view(_classify(shell_info=se.parse_shell_info({})), None)
    assert view.install_command is None
    assert view.package_missing is True


def test_view_active_native_offers_disable_button(tmp_path):
    report = _classify(
        shell_info=se.parse_shell_info(
            {"uuid": se.EXTENSION_UUID, "state": 1.0, "enabled": True, "version": 2.0}),
    )
    view = se.section_view(report, tmp_path / "z.zip")
    assert view.toggle_label == "Disable"
    assert view.install_command is None
    assert view.enable_command is None
    assert view.disable_command is None


def test_view_disabled_native_offers_enable_button():
    report = _classify(shell_info=se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": 2.0, "enabled": False, "version": 2.0}))
    view = se.section_view(report, None)
    assert view.toggle_label == "Enable"
    assert view.package_missing is False


def test_view_disabled_sandboxed_like_shows_enable_command():
    report = _classify(shell_info=se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": 2.0, "enabled": False, "version": 2.0}),
        sandboxed=True)
    view = se.section_view(report, None)
    assert view.toggle_label is None
    assert view.enable_command == se.ENABLE_COMMAND


def test_view_active_sandboxed_shows_disable_command():
    # No org.gnome.Shell.Extensions access: tell the user how to turn it off.
    view = se.section_view(_classify(sandboxed=True, running_info={"version": 2}), None)
    assert view.toggle_label is None
    assert view.disable_command == se.DISABLE_COMMAND


def test_view_not_gnome_shows_nothing_actionable():
    view = se.section_view(_classify(is_gnome=False), None)
    assert view.install_command is None
    assert view.enable_command is None
    assert view.disable_command is None
    assert view.toggle_label is None
    assert view.package_missing is False
