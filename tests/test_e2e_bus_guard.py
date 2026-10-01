"""Unit tests for tests/e2e/bus_guard.py -- the safety gate that must refuse
to let the e2e harness run against the user's real session bus.

This must be implemented and green BEFORE anything in the harness spawns a
daemon subprocess (WHISP-46 orchestrator amendment 1): a previous agent
working on a similar harness killed the user's real desktop session by
mismanaging a session bus, so the guard is the first line of defense and is
tested in isolation here with no real bus involved at all (systemd1-owned
detection is injected as a fake callable, never a real D-Bus connection).
"""
import os

import pytest

from tests.e2e.bus_guard import UnsafeBusError, assert_private_e2e_bus, real_session_bus_address


class TestRealSessionBusAddress:
    def test_reads_the_captured_env_var(self, monkeypatch):
        monkeypatch.setenv("_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS", "unix:path=/tmp/dbus-abc123")
        assert real_session_bus_address() == "unix:path=/tmp/dbus-abc123"

    def test_empty_string_when_unset(self, monkeypatch):
        monkeypatch.delenv("_WHISPER_ALOUD_TEST_REAL_DBUS_ADDRESS", raising=False)
        assert real_session_bus_address() == ""


class TestAssertPrivateE2eBus:
    def test_rejects_empty_address(self):
        with pytest.raises(UnsafeBusError, match="dbus-run-session"):
            assert_private_e2e_bus("", systemd1_owned_check=lambda addr: False)

    def test_rejects_the_systemd_user_session_bus_path(self, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        # Never actually calls the check: path match alone must be enough to refuse.
        with pytest.raises(UnsafeBusError, match="real session bus"):
            assert_private_e2e_bus(
                "unix:path=/run/user/1000/bus",
                systemd1_owned_check=lambda addr: (_ for _ in ()).throw(
                    AssertionError("must not probe systemd1 when the path alone is unsafe")
                ),
            )

    def test_rejects_when_systemd1_is_owned_even_with_a_different_path(self, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        with pytest.raises(UnsafeBusError, match="systemd1"):
            assert_private_e2e_bus(
                "unix:path=/tmp/some-other-bus",
                systemd1_owned_check=lambda addr: True,
            )

    def test_accepts_a_private_dbus_run_session_bus(self, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        # Must not raise.
        assert_private_e2e_bus(
            "unix:path=/tmp/dbus-ULhJqdUbHO,guid=88385a8174e5e3ff14ddbd3a6abecc24",
            systemd1_owned_check=lambda addr: False,
        )

    def test_accepts_when_xdg_runtime_dir_is_unset(self, monkeypatch):
        # No real-bus path to compare against; still must consult systemd1_owned_check.
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert_private_e2e_bus(
            "unix:path=/tmp/dbus-xyz",
            systemd1_owned_check=lambda addr: False,
        )

    def test_default_systemd1_check_is_not_called_when_path_is_unsafe(self, monkeypatch):
        """Sanity: the real (non-mocked) default checker must never run against
        the real bus just by calling assert_private_e2e_bus with no override --
        the path-match short-circuit must fire first."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        with pytest.raises(UnsafeBusError):
            assert_private_e2e_bus("unix:path=/run/user/1000/bus")
