"""Unit tests for tests/e2e/env_builder.py -- the WHISP-46 incident barrier.

An e2e run overwrote the operator's real Wayland clipboard even though
WAYLAND_DISPLAY/DISPLAY were unset in the daemon subprocess's env, because
unsetting those two variables alone does not stop PipeWire/wl-clipboard from
falling back to real sockets under the real $XDG_RUNTIME_DIR. These tests
inspect the plain dict build_daemon_env() returns -- no real bus, no real
subprocess, no real env mutation (a `base_env` dict is always injected) --
so they run as part of the default fast unit suite, unlike tests/e2e/ itself.
"""
from pathlib import Path

from tests.e2e.env_builder import build_daemon_env


def _fake_real_env(tmp_path):
    """A base env shaped like a real desktop session, so a passing test
    proves the barrier actively strips/overrides these, not that they were
    merely absent to begin with."""
    runtime_dir = tmp_path / "real-run-user-1000"
    runtime_dir.mkdir()
    (runtime_dir / "wayland-0").touch()
    (runtime_dir / "pipewire-0").touch()
    (runtime_dir / "bus").touch()
    return {
        "PATH": "/usr/bin:/bin",
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_SESSION_TYPE": "wayland",
        "PULSE_SERVER": "/run/user/1000/pulse/native",
        "XDG_RUNTIME_DIR": str(runtime_dir),
    }


def test_display_and_wayland_display_are_removed(tmp_path):
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path))
    assert "DISPLAY" not in env
    assert "WAYLAND_DISPLAY" not in env
    assert "XDG_SESSION_TYPE" not in env


def test_pulse_server_is_removed(tmp_path):
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path))
    assert "PULSE_SERVER" not in env


def test_xdg_runtime_dir_is_redirected_away_from_the_real_one(tmp_path):
    real_env = _fake_real_env(tmp_path)
    real_runtime_dir = real_env["XDG_RUNTIME_DIR"]

    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=real_env)

    assert env["XDG_RUNTIME_DIR"] != real_runtime_dir
    new_runtime_dir = Path(env["XDG_RUNTIME_DIR"])
    assert str(new_runtime_dir).startswith(str(tmp_path))
    # No real wayland-*/pipewire-*/bus socket must be reachable from the new dir.
    assert not (new_runtime_dir / "wayland-0").exists()
    assert not (new_runtime_dir / "pipewire-0").exists()
    assert not (new_runtime_dir / "bus").exists()


def test_pipewire_remote_points_at_a_path_that_cannot_exist(tmp_path):
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path))
    assert "PIPEWIRE_REMOTE" in env
    assert not Path(env["PIPEWIRE_REMOTE"]).exists()


def test_dbus_session_bus_address_is_the_explicit_private_address(tmp_path):
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-AbCdEf,guid=0123456789abcdef", base_env=_fake_real_env(tmp_path)
    )
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/dbus-AbCdEf,guid=0123456789abcdef"


def test_home_and_xdg_dirs_are_under_the_given_xdg_home(tmp_path):
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path))
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert Path(env[key]).is_relative_to(tmp_path), f"{key}={env[key]!r} not under {tmp_path}"


def test_test_mode_and_fake_engine_are_set(tmp_path):
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path))
    assert env["WHISPER_ALOUD_TEST_MODE"] == "1"
    assert env["WHISPER_ALOUD_MODEL_ENGINE"] == "fake"
    assert env["WHISPER_ALOUD_MODEL_NAME"] == "fake"
    assert env["WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE"] == "0"
    assert env["WHISPER_ALOUD_RAISE_MIC_GAIN"] == "0"
    assert env["WHISPER_ALOUD_PAUSE_MEDIA"] == "0"
    assert env["WHISPER_ALOUD_NOTIFICATIONS_ENABLED"] == "0"


def test_worktree_src_is_prepended_to_pythonpath(tmp_path):
    from tests.e2e.env_builder import WORKTREE_SRC

    base = dict(_fake_real_env(tmp_path))
    base["PYTHONPATH"] = "/some/other/path"
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=base)
    assert env["PYTHONPATH"].split(":")[0] == WORKTREE_SRC
    assert "/some/other/path" in env["PYTHONPATH"]


def test_config_overrides_become_prefixed_env_vars(tmp_path):
    env = build_daemon_env(
        tmp_path,
        "unix:path=/tmp/dbus-private",
        config_overrides={"MAX_RECORDING_DURATION": "0.3"},
        base_env=_fake_real_env(tmp_path),
    )
    assert env["WHISPER_ALOUD_MAX_RECORDING_DURATION"] == "0.3"


def test_does_not_mutate_the_real_process_environment(tmp_path, monkeypatch):
    """Sanity: calling build_daemon_env must never touch os.environ itself --
    base_env is always a fresh dict copy."""
    import os

    monkeypatch.delenv("WHISPER_ALOUD_TEST_MODE", raising=False)
    build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path))
    assert "WHISPER_ALOUD_TEST_MODE" not in os.environ
