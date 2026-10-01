"""Unit tests for tests/e2e/env_builder.py -- the WHISP-46 incident barrier.

Round 1: an e2e run overwrote the operator's real Wayland clipboard because
unsetting WAYLAND_DISPLAY/DISPLAY alone does not stop PipeWire/wl-clipboard
from falling back to real sockets under the real $XDG_RUNTIME_DIR.

Round 2 (audit CHANGES_REQUIRED): the fix still started from
`dict(os.environ)` and only removed four variables, which leaked
XAUTHORITY, SSH_AUTH_SOCK, GNOME_KEYRING_*, and AT_SPI_BUS_ADDRESS -- a
*second* real D-Bus address that bypasses DBUS_SESSION_BUS_ADDRESS
isolation entirely. build_daemon_env() now builds the subprocess env from
an ALLOWLIST instead of a blocklist.

These tests inspect the plain dict build_daemon_env() returns -- no real
bus, no real subprocess, no real env mutation (a `base_env` dict is always
injected) -- so they run as part of the default fast unit suite, unlike
tests/e2e/ itself.
"""
from pathlib import Path

from tests.e2e.env_builder import WORKTREE_SRC, build_daemon_env

# Every dangerous variable the WHISP-46 audit called out, plus the random
# unknown one it asked for, standing in for "anything else a real desktop
# session might export that we have never heard of."
_DANGEROUS_VARS = {
    "XAUTHORITY": "/home/user/.Xauthority",
    "SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh",
    "GNOME_KEYRING_CONTROL": "/run/user/1000/keyring",
    "GNOME_KEYRING_PID": "12345",
    "AT_SPI_BUS_ADDRESS": "unix:path=/run/user/1000/at-spi/bus",
    "DISPLAY": ":0",
    "WAYLAND_DISPLAY": "wayland-0",
    "XDG_SESSION_TYPE": "wayland",
    "PULSE_SERVER": "/run/user/1000/pulse/native",
    "FOO_SESSION_SOCKET": "/tmp/something-nobody-thought-of",
}

_ALLOWLISTED_VARS = {
    "PATH": "/usr/bin:/bin",
    "LANG": "en_US.UTF-8",
    "LANGUAGE": "en_US",
    "LC_ALL": "en_US.UTF-8",
    "LC_TIME": "es_AR.UTF-8",
    "TERM": "xterm-256color",
    "TZ": "America/Argentina/Buenos_Aires",
}


def _fake_real_env(tmp_path, extra=None):
    """A base env shaped like a real desktop session: every dangerous var
    the audit listed, every allowlisted var (so a passing test proves they
    really are carried through, not merely never excluded), real sockets
    under a fake XDG_RUNTIME_DIR, and anything the caller adds."""
    runtime_dir = tmp_path / "real-run-user-1000"
    runtime_dir.mkdir()
    (runtime_dir / "wayland-0").touch()
    (runtime_dir / "pipewire-0").touch()
    (runtime_dir / "bus").touch()
    (runtime_dir / "at-spi").mkdir()
    (runtime_dir / "at-spi" / "bus").touch()

    env = dict(_DANGEROUS_VARS)
    env.update(_ALLOWLISTED_VARS)
    env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    env["PYTHONPATH"] = "/some/other/path"
    if extra:
        env.update(extra)
    return env


def _expected_overridden_keys():
    return {
        "PYTHONPATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "PIPEWIRE_REMOTE",
        "NO_AT_BRIDGE",
        "DBUS_SESSION_BUS_ADDRESS",
        "WHISPER_ALOUD_TEST_MODE",
        "WHISPER_ALOUD_MODEL_ENGINE",
        "WHISPER_ALOUD_MODEL_NAME",
        "WHISPER_ALOUD_RAISE_MIC_GAIN",
        "WHISPER_ALOUD_PAUSE_MEDIA",
        "WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE",
        "WHISPER_ALOUD_NOTIFICATIONS_ENABLED",
        "WHISPER_ALOUD_DB_PATH",
    }


def test_no_dangerous_or_unknown_variable_survives(tmp_path):
    """The audit's exact scenario: every dangerous var it listed, plus an
    arbitrary unknown FOO_SESSION_SOCKET, must not appear in the output."""
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    for key in _DANGEROUS_VARS:
        assert key not in env, f"{key} leaked into the daemon subprocess env"


def test_exact_key_set_equals_allowlist_plus_overrides(tmp_path):
    """No other key may be present -- this is the allowlist property itself,
    not just an absence check on specific dangerous names."""
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    expected = set(_ALLOWLISTED_VARS) | _expected_overridden_keys()
    assert set(env.keys()) == expected


def test_allowlisted_locale_and_terminal_vars_are_inherited(tmp_path):
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    for key, value in _ALLOWLISTED_VARS.items():
        assert env[key] == value


def test_an_allowlisted_var_absent_from_base_env_is_absent_from_output(tmp_path):
    """Nothing is synthesized for an allowlisted name that was never set --
    the allowlist only lets a value through, it never invents one."""
    base = _fake_real_env(tmp_path)
    del base["TZ"]
    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=base)
    assert "TZ" not in env


def test_xdg_runtime_dir_is_redirected_away_from_the_real_one(tmp_path):
    real_env = _fake_real_env(tmp_path)
    real_runtime_dir = real_env["XDG_RUNTIME_DIR"]

    env = build_daemon_env(tmp_path, "unix:path=/tmp/dbus-private", base_env=real_env)

    assert env["XDG_RUNTIME_DIR"] != real_runtime_dir
    new_runtime_dir = Path(env["XDG_RUNTIME_DIR"])
    assert str(new_runtime_dir).startswith(str(tmp_path))
    # No real wayland-*/pipewire-*/bus/at-spi socket must be reachable from the new dir.
    assert not (new_runtime_dir / "wayland-0").exists()
    assert not (new_runtime_dir / "pipewire-0").exists()
    assert not (new_runtime_dir / "bus").exists()
    assert not (new_runtime_dir / "at-spi").exists()


def test_pipewire_remote_points_at_a_path_that_cannot_exist(tmp_path):
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    assert "PIPEWIRE_REMOTE" in env
    assert not Path(env["PIPEWIRE_REMOTE"]).exists()


def test_no_at_bridge_is_set(tmp_path):
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    assert env["NO_AT_BRIDGE"] == "1"


def test_dbus_session_bus_address_is_the_explicit_private_address(tmp_path):
    env = build_daemon_env(
        tmp_path,
        "unix:path=/tmp/dbus-AbCdEf,guid=0123456789abcdef",
        base_env=_fake_real_env(tmp_path),
    )
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/dbus-AbCdEf,guid=0123456789abcdef"


def test_home_and_xdg_dirs_are_under_the_given_xdg_home(tmp_path):
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert Path(env[key]).is_relative_to(tmp_path), f"{key}={env[key]!r} not under {tmp_path}"


def test_test_mode_and_fake_engine_are_set(tmp_path):
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    assert env["WHISPER_ALOUD_TEST_MODE"] == "1"
    assert env["WHISPER_ALOUD_MODEL_ENGINE"] == "fake"
    assert env["WHISPER_ALOUD_MODEL_NAME"] == "fake"
    assert env["WHISPER_ALOUD_CLIPBOARD_AUTO_PASTE"] == "0"
    assert env["WHISPER_ALOUD_RAISE_MIC_GAIN"] == "0"
    assert env["WHISPER_ALOUD_PAUSE_MEDIA"] == "0"
    assert env["WHISPER_ALOUD_NOTIFICATIONS_ENABLED"] == "0"


def test_pythonpath_is_forced_to_the_worktree_src_only(tmp_path):
    """PYTHONPATH must never carry through the real process's own value --
    it is forced, not merged."""
    env = build_daemon_env(
        tmp_path, "unix:path=/tmp/dbus-private", base_env=_fake_real_env(tmp_path)
    )
    assert env["PYTHONPATH"] == WORKTREE_SRC


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
