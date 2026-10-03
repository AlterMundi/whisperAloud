#!/usr/bin/env bash
# Run the GUI smoke tests (tests/gui/, pytest marker "gui") under a private Xvfb
# display and a private D-Bus session, with a disposable HOME/XDG tree.
#
# These tests build real GTK4/libadwaita widgets (SettingsDialog) and therefore need
# a live display, unlike the rest of the suite. The root conftest.py (see commit
# 4c877c5) unconditionally strips DISPLAY/WAYLAND_DISPLAY from the test process for
# every other test — on purpose, so no test can ever reach the developer's real
# desktop. We do not weaken that guard here. Instead:
#   - xvfb-run starts a private Xvfb server and sets DISPLAY (and an Xauthority
#     cookie via XAUTHORITY) for its child process.
#   - We capture both under WHISPER_ALOUD_GUI_DISPLAY/WHISPER_ALOUD_GUI_XAUTHORITY
#     (names conftest.py never touches) before pytest/conftest.py runs and pops the
#     real DISPLAY/XAUTHORITY vars.
#   - tests/gui/test_settings_dialog_smoke.py restores os.environ["DISPLAY"]/
#     ["XAUTHORITY"] from those two, but only when WHISPER_ALOUD_GUI_TEST=1 (set
#     below) — so a plain `pytest`/`pytest -q` run (which never sets that var) still
#     gets the fully-isolated, no-display environment conftest.py guarantees.
#
# SAFETY: the `env -i` below already means dbus-run-session is started with no
# XDG_DATA_DIRS at all, so the system default session.conf's
# <standard_session_servicedirs/> has nothing Flatpak-derived to resolve from
# the environment -- but we still pass --config-file explicitly (same
# dedicated, servicedir-free config as scripts/test_private_bus.sh) rather
# than rely on that as the only guard. See tests/dbus/session-test.conf.
#
# Usage: scripts/test_gui.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${WHISPER_ALOUD_VENV_PYTHON:-$HOME/.venvs/whisper_aloud/bin/python}"
DBUS_TEST_CONFIG="$REPO_ROOT/tests/dbus/session-test.conf"

if [ ! -f "$DBUS_TEST_CONFIG" ]; then
    echo "scripts/test_gui.sh: missing $DBUS_TEST_CONFIG" >&2
    exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "scripts/test_gui.sh: venv python not found/executable: $VENV_PYTHON" >&2
    echo "Set WHISPER_ALOUD_VENV_PYTHON to override." >&2
    exit 1
fi

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

mkdir -p "$T/home" "$T/config" "$T/data" "$T/cache"
mkdir -m 0700 -p "$T/run"

env -i \
    PATH=/usr/bin:/bin \
    HOME="$T/home" \
    XDG_CONFIG_HOME="$T/config" \
    XDG_DATA_HOME="$T/data" \
    XDG_CACHE_HOME="$T/cache" \
    XDG_RUNTIME_DIR="$T/run" \
    GDK_BACKEND=x11 \
    GSK_RENDERER=cairo \
    NO_AT_BRIDGE=1 \
    PYTHONPATH="$REPO_ROOT/src" \
    WHISPER_ALOUD_GUI_TEST=1 \
    VENV_PYTHON="$VENV_PYTHON" \
    REPO_ROOT="$REPO_ROOT" \
    timeout 120 \
    dbus-run-session "--config-file=$DBUS_TEST_CONFIG" -- \
    xvfb-run -a bash -c '
        set -euo pipefail
        # Preserve the Xvfb DISPLAY xvfb-run just set, under a name the root
        # conftest.py guard (4c877c5) does not strip, before pytest ever imports it.
        export WHISPER_ALOUD_GUI_DISPLAY="$DISPLAY"
        export WHISPER_ALOUD_GUI_XAUTHORITY="${XAUTHORITY:-}"
        # Override addopts: the default --cov=whisper_aloud needs pytest-cov, which
        # (on this host) lives under the real $HOME and is unreachable from the
        # disposable $T/home this wrapper intentionally uses. Coverage is not the
        # point of this smoke test.
        exec "$VENV_PYTHON" -m pytest -o addopts="" -m gui "$REPO_ROOT/tests/gui"
    '
