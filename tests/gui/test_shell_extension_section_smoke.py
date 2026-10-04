"""GTK smoke tests for the Settings "Top bar indicator (GNOME Shell extension)"
section. Opt-in via scripts/test_gui.sh only (same display guard as
test_settings_dialog_smoke.py, whose module-level setup restores the Xvfb
DISPLAY). The D-Bus probe is faked: no bus traffic at all.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from tests.gui.test_settings_dialog_smoke import _make_dialog, pytestmark  # noqa: F401
from whisper_aloud import shell_extension as se

GNOME_ENV = {"XDG_CURRENT_DESKTOP": "GNOME"}


class FakeProbe:
    def __init__(self, shell_info=None, running_info=None):
        self.shell_info = shell_info
        self.running_info = running_info
        self.enabled_calls = []
        self.watch_cb = None
        self.closed = False

    def query(self, on_done):
        on_done(self.shell_info, self.running_info)

    def set_enabled(self, enabled, on_done):
        self.enabled_calls.append(enabled)
        on_done(True)

    def watch(self, on_change):
        self.watch_cb = on_change
        return lambda: None

    def close(self):
        self.closed = True


def _gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    if not Gtk.init_check():
        pytest.skip("GTK4 failed to initialize against the Xvfb display")
    return Gtk


def _zip(tmp_path, version=2):
    path = tmp_path / se.ZIP_NAME
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"uuid": se.EXTENSION_UUID, "version": version}))
    return path


def _section(tmp_path, probe, *, sandboxed=False, env=GNOME_ENV, bundled=None):
    _gtk()
    from whisper_aloud.ui.shell_extension_section import ShellExtensionSection

    copied = []
    section = ShellExtensionSection(
        probe=probe, sandboxed=sandboxed, env=env, data_home=tmp_path / "data",
        bundled_zip=bundled if bundled is not None else _zip(tmp_path), copy=copied.append,
    )
    return section, copied


def test_not_installed_native_shows_install_command_and_copies(tmp_path):
    probe = FakeProbe(shell_info=se.parse_shell_info({}))
    section, copied = _section(tmp_path, probe)
    assert section.status_label.get_text() == "Not installed"
    assert section.install_box.get_visible()
    cmd = section.install_label.get_text()
    assert cmd == se.install_command(tmp_path / se.ZIP_NAME)
    assert not section.toggle_button.get_visible()
    # Copy button of the install box.
    row = section.install_box.get_last_child()
    row.get_last_child().emit("clicked")
    assert copied == [cmd]


def test_flatpak_unknown_exports_zip_and_shows_both_commands(tmp_path):
    section, _ = _section(tmp_path, FakeProbe(), sandboxed=True)
    exported = se.host_export_path(tmp_path / "data")
    assert exported.exists()
    assert section.install_label.get_text() == se.install_command(exported)
    assert section.enable_label.get_text() == se.ENABLE_COMMAND
    assert section.status_label.get_text() == "Not running"


def test_native_active_disable_button_calls_shell(tmp_path):
    probe = FakeProbe(
        shell_info=se.parse_shell_info(
            {"uuid": se.EXTENSION_UUID, "state": 1.0, "enabled": True, "version": 2.0}),
        running_info={"version": 2},
    )
    section, _ = _section(tmp_path, probe)
    assert section.status_label.get_text() == "Running (version 2)"
    assert section.toggle_button.get_visible()
    assert section.toggle_button.get_label() == "Disable"
    assert not section.install_box.get_visible()
    section.toggle_button.emit("clicked")
    assert probe.enabled_calls == [False]


def test_watch_triggers_refresh(tmp_path):
    probe = FakeProbe(shell_info=se.parse_shell_info({}))
    section, _ = _section(tmp_path, probe)
    probe.running_info = {"version": 2}
    probe.shell_info = se.parse_shell_info(
        {"uuid": se.EXTENSION_UUID, "state": 1.0, "enabled": True, "version": 2.0})
    probe.watch_cb()
    assert section.status_label.get_text().startswith("Running")


def test_not_gnome_never_queries(tmp_path):
    class NoQuery(FakeProbe):
        def query(self, on_done):  # pragma: no cover - must not be called
            raise AssertionError("queried D-Bus outside GNOME")

    section, _ = _section(tmp_path, NoQuery(), env={"XDG_CURRENT_DESKTOP": "KDE"})
    assert section.status_label.get_text() == "Not available"
    assert not section.install_box.get_visible()


def test_settings_dialog_includes_section(monkeypatch):
    from whisper_aloud.config import WhisperAloudConfig

    dialog = _make_dialog(WhisperAloudConfig(), monkeypatch)
    section = dialog._shell_extension_section
    assert section is not None
    assert section.widget.get_parent() is not None
