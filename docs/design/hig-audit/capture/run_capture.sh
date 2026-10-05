#!/usr/bin/env bash
# One-shot: render WhisperAloud's GTK UI headless for the GNOME HIG audit.
# Same isolation as scripts/test_gui.sh: env -i, private dbus-run-session with
# tests/dbus/session-test.conf (nothing activatable), private Xvfb, temp HOME.
# Usage: run_capture.sh <worktree> <outdir> [mode...]
set -euo pipefail
WT="$1"; OUT="$2"; shift 2
VENV_PYTHON="${WHISPER_ALOUD_VENV_PYTHON:-$HOME/.venvs/whisper_aloud/bin/python}"
CONF="$WT/tests/dbus/session-test.conf"
[ -f "$CONF" ] || { echo "missing $CONF" >&2; exit 1; }
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/home" "$T/config" "$T/data" "$T/cache" "$OUT"
mkdir -m 0700 -p "$T/run"
env -i \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    HOME="$T/home" \
    XDG_CONFIG_HOME="$T/config" \
    XDG_DATA_HOME="$T/data" \
    XDG_CACHE_HOME="$T/cache" \
    XDG_RUNTIME_DIR="$T/run" \
    PIPEWIRE_REMOTE="$T/no-such-pipewire" \
    PULSE_SERVER="unix:$T/no-such-pulse" \
    GDK_BACKEND=x11 \
    GSK_RENDERER=cairo \
    NO_AT_BRIDGE=1 \
    WHISPER_ALOUD_TEST_MODE=1 \
    PYTHONPATH="$WT/src:$WT" \
    HIG_WT="$WT" HIG_OUT="$OUT" HIG_TMP="$T" \
    timeout 300 \
    dbus-run-session "--config-file=$CONF" -- \
    xvfb-run -a -s "-screen 0 1600x2400x24" \
    "$VENV_PYTHON" "$(dirname "$0")/capture.py" "$@"
