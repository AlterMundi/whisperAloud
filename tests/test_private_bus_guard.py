"""The private-bus guard must reject the login bus without connecting to it."""

import pytest

from tests import _private_bus as pb


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv(pb.OPT_IN_ENV, "1")

    def set_address(address):
        monkeypatch.setenv(pb.ORIGINAL_ADDRESS_ENV, address)

    return set_address


@pytest.mark.parametrize(
    "address",
    [
        "unix:path=/run/user/1000/bus",
        "unix:path=/run/user/1000/bus,guid=0123456789abcdef",
        "unix:path=/run/user/1000/at-spi/bus",
        "unix:abstract=/tmp/x;unix:path=/run/user/1000/bus",
    ],
)
def test_login_bus_is_rejected(env, address):
    env(address)
    assert pb.private_bus_address() is None


@pytest.mark.parametrize(
    "address",
    [
        "unix:path=/tmp/dbus-AbCdEf,guid=0123456789abcdef",
        "unix:abstract=/tmp/dbus-XyZ,guid=0123456789abcdef",
    ],
)
def test_dbus_run_session_bus_is_accepted(env, address):
    env(address)
    assert pb.private_bus_address() == address


def test_requires_opt_in(env, monkeypatch):
    env("unix:path=/tmp/dbus-AbCdEf")
    monkeypatch.delenv(pb.OPT_IN_ENV)
    assert pb.private_bus_address() is None


def test_missing_address_is_rejected(env, monkeypatch):
    monkeypatch.delenv(pb.ORIGINAL_ADDRESS_ENV, raising=False)
    assert pb.private_bus_address() is None


def test_guard_never_touches_gio(env, monkeypatch):
    import sys

    env("unix:path=/run/user/1000/bus")
    monkeypatch.setitem(sys.modules, "gi", None)  # any gi import would raise
    assert pb.private_bus_address() is None
