"""The root conftest must keep the unit suite away from the user's desktop."""

import os
import subprocess

import pytest


@pytest.mark.parametrize("tool", ["wl-copy", "/usr/bin/xclip", "xdotool", "wpctl"])
def test_desktop_tools_cannot_be_launched(tool):
    with pytest.raises(RuntimeError, match="desktop tool"):
        subprocess.run([tool, "--version"], capture_output=True)


def test_shell_string_with_desktop_tool_is_blocked():
    with pytest.raises(RuntimeError, match="desktop tool"):
        subprocess.Popen("wl-copy hello", shell=True)


def test_ordinary_commands_still_run():
    assert subprocess.run(["true"]).returncode == 0


def test_runtime_dir_is_private():
    runtime = os.environ["XDG_RUNTIME_DIR"]
    assert not runtime.startswith("/run/user/")
    assert not any(n.startswith(("wayland-", "pipewire-")) for n in os.listdir(runtime))


def test_home_is_isolated_and_config_save_stays_inside_it():
    from pathlib import Path

    from whisper_aloud.config import WhisperAloudConfig

    real_home = os.environ["WHISPER_ALOUD_TEST_REAL_HOME"]
    assert str(Path.home()) != real_home
    WhisperAloudConfig().save()
    saved = Path.home() / ".config" / "whisper_aloud" / "config.json"
    assert saved.exists()
    assert not str(saved).startswith(real_home + os.sep)
