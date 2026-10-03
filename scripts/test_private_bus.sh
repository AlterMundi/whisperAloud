#!/bin/sh
# Run pytest on a throwaway D-Bus session bus. The bus lives exactly as long
# as this command: dbus-run-session tears it down on exit, so nothing ever
# needs to be killed. Example:
#   scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_portal_flow.py
#
# PYTHON can be overridden (e.g. in CI, to a --system-site-packages venv's
# interpreter) without changing local behavior, which defaults to whatever
# "python" resolves to on PATH (normally an activated venv).
#
# SAFETY (incident): this MUST always pass --config-file pointing at
# tests/dbus/session-test.conf. A bare `dbus-run-session` falls back to the
# system default session.conf, whose <standard_session_servicedirs/>
# directive scans XDG_DATA_DIRS for activatable *.service files -- which, on
# a machine with the real WhisperAloud Flatpak installed, includes
# ~/.local/share/flatpak/exports/share/dbus-1/services/org.fede.whisperaloud.service.
# The harness's first bus.get("org.fede.whisperaloud") against that
# "private" bus then D-Bus-activates the real, maintainer's installed app
# (real $HOME, real mic, real clipboard), not anything this harness spawned.
# See tests/dbus/session-test.conf and tests/test_private_bus_dbus_config.py.
#
# XDG_DATA_DIRS is also explicitly cleared below as defense in depth, in
# case --config-file is ever dropped or dbus-daemon's defaults change: with
# it unset, even a bare dbus-run-session has no XDG_DATA_DIRS-derived
# service dirs to find the Flatpak export in.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DBUS_TEST_CONFIG="$REPO_ROOT/tests/dbus/session-test.conf"

if [ ! -f "$DBUS_TEST_CONFIG" ]; then
    echo "scripts/test_private_bus.sh: missing $DBUS_TEST_CONFIG" >&2
    exit 1
fi

PYTHON="${PYTHON:-python}"
exec timeout "${WHISPER_ALOUD_TEST_TIMEOUT:-300}" \
    env -u XDG_DATA_DIRS \
    dbus-run-session "--config-file=$DBUS_TEST_CONFIG" -- \
    env WHISPER_ALOUD_PRIVATE_BUS=1 "$PYTHON" -m pytest "$@"
