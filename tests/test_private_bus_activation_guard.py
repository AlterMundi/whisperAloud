"""Unit tests for the fail-closed D-Bus activation guard added to
tests/_private_bus.py (incident follow-up: a "private" dbus-run-session bus
auto-activated the real, installed Flatpak app because the bus's default
config scanned XDG_DATA_DIRS for activatable .service files).

These are pure-function tests: no bus, no /proc, no subprocess. The real
I/O (ListActivatableNames() over D-Bus, reading /proc/<pid>/exe) happens
only at the conftest.py call sites.
"""
import pytest

from tests._private_bus import (
    UnsafeBusActivationError,
    assert_not_activatable,
    assert_process_is_not_flatpak,
    is_flatpak_or_bwrap_process,
)

BUS_NAME = "org.fede.whisperaloud"


class TestAssertNotActivatable:
    def test_passes_when_name_absent(self):
        assert_not_activatable(
            ["org.freedesktop.DBus", "org.freedesktop.portal.Desktop"], BUS_NAME
        )

    def test_passes_on_empty_list(self):
        assert_not_activatable([], BUS_NAME)

    def test_raises_when_name_present(self):
        with pytest.raises(UnsafeBusActivationError):
            assert_not_activatable([BUS_NAME, "org.freedesktop.DBus"], BUS_NAME)

    def test_error_message_names_the_bus_name(self):
        with pytest.raises(UnsafeBusActivationError, match=BUS_NAME):
            assert_not_activatable([BUS_NAME], BUS_NAME)


class TestIsFlatpakOrBwrapProcess:
    @pytest.mark.parametrize(
        "exe_path,cmdline_text",
        [
            ("/usr/bin/bwrap", ""),
            ("/usr/libexec/flatpak-bwrap", ""),
            ("/app/bin/whisper-aloud-daemon", "flatpak run org.fede.whisperaloud --daemon"),
            ("/usr/bin/flatpak", ""),
            ("", "/usr/bin/bwrap --ro-bind / / ..."),
        ],
    )
    def test_detects_flatpak_or_bwrap(self, exe_path, cmdline_text):
        assert is_flatpak_or_bwrap_process(exe_path, cmdline_text) is True

    @pytest.mark.parametrize(
        "exe_path,cmdline_text",
        [
            ("/home/fede/.venvs/whisper_aloud/bin/python3.13", "python -m whisper_aloud --daemon"),
            ("/usr/bin/python3", "python3 -m whisper_aloud --daemon"),
            ("", ""),
        ],
    )
    def test_accepts_plain_interpreter(self, exe_path, cmdline_text):
        assert is_flatpak_or_bwrap_process(exe_path, cmdline_text) is False


class TestAssertProcessIsNotFlatpak:
    def test_passes_for_this_test_process(self):
        # The pytest process running this very test is a plain interpreter,
        # never flatpak/bwrap -- a real, read-only /proc read of our own pid.
        import os

        assert_process_is_not_flatpak(os.getpid())

    def test_raises_for_a_synthesized_flatpak_like_pid(self, monkeypatch):
        import tests._private_bus as pb

        monkeypatch.setattr(pb.os, "readlink", lambda path: "/usr/bin/bwrap")
        with pytest.raises(UnsafeBusActivationError):
            assert_process_is_not_flatpak(1234567)

    def test_tolerates_unreadable_proc_entries(self):
        # A pid that no longer exists (or /proc/<pid>/exe we can't read)
        # must not raise OSError -- just fail open to "doesn't look like
        # flatpak" since there's nothing to go on (the ListActivatableNames
        # check is the primary guard; this is defense in depth).
        assert_process_is_not_flatpak(999999999)
