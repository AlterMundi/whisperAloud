"""Native (non-Flatpak) XDG autostart entry writer (WHISP-87).

Every test works in a tmp_path autostart directory passed explicitly; nothing
here ever resolves the user's real ~/.config/autostart.
"""

import re
import subprocess
from pathlib import Path

import pytest

from whisper_aloud import autostart


@pytest.fixture
def adir(tmp_path) -> Path:
    return tmp_path / "config" / "autostart"


def _exec_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("Exec="))


class TestAutostartDir:
    def test_uses_xdg_config_home(self, tmp_path):
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")}
        assert autostart.autostart_dir(env) == tmp_path / "xdg" / "autostart"

    def test_falls_back_to_home_dot_config(self, tmp_path):
        env = {"HOME": str(tmp_path / "home")}
        assert autostart.autostart_dir(env) == tmp_path / "home" / ".config" / "autostart"

    def test_relative_xdg_config_home_is_ignored(self, tmp_path):
        # The XDG spec says relative paths are invalid and must be ignored.
        env = {"XDG_CONFIG_HOME": "relative/dir", "HOME": str(tmp_path / "home")}
        assert autostart.autostart_dir(env) == tmp_path / "home" / ".config" / "autostart"


class TestDesktopEntry:
    def test_reuses_the_existing_native_file_name(self):
        # Same name install.sh and contrib/ use, so enabling never creates a
        # second entry next to an installer-made one.
        assert autostart.MANAGED_FILENAME == "whisper-aloud-autostart.desktop"

    def test_runs_the_daemon_only_never_the_gui(self):
        text = autostart.build_desktop_entry("/usr/bin/whisper-aloud")
        exec_line = _exec_line(text)
        assert exec_line.rstrip("'").endswith("exec /usr/bin/whisper-aloud --daemon")
        assert "gui" not in exec_line
        assert "whisper_aloud.ui" not in exec_line

    def test_defers_to_the_systemd_unit_like_the_contrib_entry(self):
        # Same WHISP-63 invariants as contrib/whisper-aloud-autostart.desktop.
        exec_line = _exec_line(autostart.build_desktop_entry("/usr/bin/whisper-aloud"))
        assert "systemctl --user cat whisper-aloud.service" in exec_line
        assert "systemctl --user start whisper-aloud.service" in exec_line
        assert "is-active" not in exec_line
        assert "exec systemctl" not in exec_line
        assert exec_line.index("systemctl") < exec_line.index("--daemon")

    def test_is_a_valid_hidden_autostart_entry(self):
        text = autostart.build_desktop_entry("/usr/bin/whisper-aloud")
        assert text.startswith("[Desktop Entry]\n")
        assert "Type=Application" in text
        assert "NoDisplay=true" in text
        assert "X-GNOME-Autostart-enabled=true" in text
        assert f"{autostart.MANAGED_MARKER}=true" in text

    def test_python_module_fallback_command(self):
        exec_line = _exec_line(autostart.build_desktop_entry("/opt/venv/bin/python -m whisper_aloud"))
        assert exec_line.rstrip("'").endswith("exec /opt/venv/bin/python -m whisper_aloud --daemon")

    @pytest.mark.parametrize(
        "bad", ["/home/a b/whisper-aloud", "/x/it's", '/x/"q"', "/x/%u", "/x/$HOME", "rel/path"]
    )
    def test_refuses_commands_that_cannot_be_quoted_safely(self, bad):
        with pytest.raises(ValueError):
            autostart.build_desktop_entry(bad)

    def test_exec_script_runs_and_falls_back_to_direct_spawn(self, tmp_path):
        """Execute the generated sh script against stub binaries (never the
        real systemctl): with no unit installed it must exec the daemon."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "log"
        (bindir / "systemctl").write_text("#!/bin/sh\nexit 1\n")
        daemon = bindir / "whisper-aloud"
        daemon.write_text(f'#!/bin/sh\necho "daemon $*" >> {log}\n')
        for f in bindir.iterdir():
            f.chmod(0o755)
        exec_line = _exec_line(autostart.build_desktop_entry(str(daemon)))
        script = re.match(r"Exec=sh -lc '(.*)'$", exec_line).group(1)
        subprocess.run(
            ["/bin/sh", "-c", script],
            env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)},
            check=True,
            timeout=10,
        )
        assert log.read_text().strip() == "daemon --daemon"


class TestDaemonCommand:
    def test_prefers_the_installed_launcher(self):
        cmd = autostart.daemon_command(which=lambda name: "/usr/bin/whisper-aloud")
        assert cmd == "/usr/bin/whisper-aloud"

    def test_falls_back_to_python_module(self):
        cmd = autostart.daemon_command(which=lambda name: None, executable="/v/bin/python")
        assert cmd == "/v/bin/python -m whisper_aloud"


class TestEnableDisable:
    def test_initial_state_is_off(self, adir):
        state = autostart.read_state(adir)
        assert state.enabled is False
        assert state.others == ()

    def test_enable_writes_the_entry(self, adir):
        state = autostart.enable(adir, command="/usr/bin/whisper-aloud")
        assert state.enabled is True
        assert state.created is True
        path = adir / autostart.MANAGED_FILENAME
        assert path.exists()
        assert "--daemon" in path.read_text()
        assert autostart.read_state(adir).enabled is True

    def test_enable_is_idempotent(self, adir):
        autostart.enable(adir, command="/usr/bin/whisper-aloud")
        state = autostart.enable(adir, command="/usr/bin/whisper-aloud")
        assert state.enabled is True
        assert [p.name for p in adir.iterdir()] == [autostart.MANAGED_FILENAME]

    def test_enable_reuses_an_installer_made_entry_without_rewriting_it(self, adir):
        adir.mkdir(parents=True)
        installer = (
            "[Desktop Entry]\nType=Application\nName=WhisperAloud Daemon\n"
            "Exec=sh -lc 'exec /home/u/.venv/bin/whisper-aloud --daemon'\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        (adir / autostart.MANAGED_FILENAME).write_text(installer)
        state = autostart.enable(adir, command="/usr/bin/whisper-aloud")
        assert state.enabled is True
        assert state.created is False
        assert (adir / autostart.MANAGED_FILENAME).read_text() == installer

    @pytest.mark.parametrize(
        "line", ["Hidden=true", "X-GNOME-Autostart-enabled=false"]
    )
    def test_disabled_entry_counts_as_off_and_enable_rewrites_it(self, adir, line):
        adir.mkdir(parents=True)
        (adir / autostart.MANAGED_FILENAME).write_text(
            f"[Desktop Entry]\nType=Application\nExec=whisper-aloud --daemon\n{line}\n"
        )
        assert autostart.read_state(adir).enabled is False
        state = autostart.enable(adir, command="/usr/bin/whisper-aloud")
        assert state.enabled is True
        assert line not in (adir / autostart.MANAGED_FILENAME).read_text()

    def test_disable_removes_the_entry(self, adir):
        autostart.enable(adir, command="/usr/bin/whisper-aloud")
        state = autostart.disable(adir)
        assert state.enabled is False
        assert not (adir / autostart.MANAGED_FILENAME).exists()

    def test_disable_when_absent_is_a_no_op(self, adir):
        assert autostart.disable(adir).enabled is False
        assert not adir.exists()

    def test_write_is_atomic_no_temp_files_left(self, adir):
        autostart.enable(adir, command="/usr/bin/whisper-aloud")
        assert sorted(p.name for p in adir.iterdir()) == [autostart.MANAGED_FILENAME]


class TestOtherEntriesAreDetectedNotDuplicatedOrDeleted:
    @pytest.mark.parametrize("name", autostart.OTHER_ENTRY_FILENAMES)
    def test_other_entry_is_reported(self, adir, name):
        adir.mkdir(parents=True)
        (adir / name).write_text("[Desktop Entry]\nType=Application\nExec=x --daemon\n")
        state = autostart.read_state(adir)
        assert state.enabled is False  # ours is not there
        assert state.others == (adir / name,)

    def test_known_names_include_the_legacy_and_manual_flatpak_ones(self):
        assert "whisperaloud-daemon.desktop" in autostart.OTHER_ENTRY_FILENAMES
        assert "org.fede.whisperaloud-daemon.desktop" in autostart.OTHER_ENTRY_FILENAMES

    def test_enable_and_disable_never_touch_other_entries(self, adir):
        adir.mkdir(parents=True)
        legacy = adir / "whisperaloud-daemon.desktop"
        legacy.write_text("[Desktop Entry]\nType=Application\nExec=x\n")
        state = autostart.enable(adir, command="/usr/bin/whisper-aloud")
        assert state.others == (legacy,)
        state = autostart.disable(adir)
        assert legacy.exists()
        assert state.others == (legacy,)

    def test_disabled_other_entry_is_not_reported(self, adir):
        adir.mkdir(parents=True)
        (adir / "whisperaloud-daemon.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=x\nHidden=true\n"
        )
        assert autostart.read_state(adir).others == ()

    def test_unreadable_directory_degrades_to_off(self, tmp_path):
        not_a_dir = tmp_path / "file"
        not_a_dir.write_text("x")
        state = autostart.read_state(not_a_dir)
        assert state.enabled is False
        assert state.others == ()
