#!/usr/bin/env bash
# WhisperAloud daemon installer
# Enables the systemd user service and verifies D-Bus activation.
# Run as the target user (not root).
set -euo pipefail

UNIT="whisper-aloud.service"
CONTRIB_DESKTOP="$(dirname "$0")/../contrib/whisper-aloud-autostart.desktop"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

echo "[whisper-aloud] Reloading systemd user daemon..."
systemctl --user daemon-reload

echo "[whisper-aloud] Enabling and starting ${UNIT}..."
systemctl --user enable --now "${UNIT}"

BUS_NAME="org.fede.whisperaloud"
# Upper bound on how long we wait for the name to appear on the bus -- a UX
# limit only (so this script doesn't hang forever if something is wrong),
# never the correctness gate: the wait below is event-driven (watches the
# actual NameOwnerChanged condition), not a fixed sleep-then-probe-once.
WAIT_TIMEOUT_S=15

echo "[whisper-aloud] Waiting for ${BUS_NAME} to appear on the session bus..."
if command -v gdbus >/dev/null 2>&1 && gdbus help wait >/dev/null 2>&1; then
  # `gdbus wait` blocks on the real name-owner condition (NameOwnerChanged),
  # returning as soon as the name appears instead of guessing a fixed delay.
  if gdbus wait --session --timeout "${WAIT_TIMEOUT_S}" "${BUS_NAME}"; then
    echo "[whisper-aloud] ✓ Daemon is running and registered on D-Bus"
  else
    echo "[whisper-aloud] ⚠ ${BUS_NAME} did not appear within ${WAIT_TIMEOUT_S}s — daemon may still be starting. Check:"
    echo "    journalctl --user -u ${UNIT} -n 30"
  fi
else
  # Fallback for a glib/gdbus build without "wait" (older systems): poll the
  # actual name-owner condition ourselves instead of a single fixed-delay
  # probe, so a slow-starting daemon is still detected correctly. The loop
  # bound is still only a UX limit, not what decides correctness.
  echo "[whisper-aloud] (gdbus wait unavailable, polling name ownership instead)"
  max_ticks=$((WAIT_TIMEOUT_S * 2))  # polling in 0.5s ticks
  tick=0
  owned=false
  while [ "${tick}" -lt "${max_ticks}" ]; do
    if gdbus call --session \
         --dest org.freedesktop.DBus \
         --object-path /org/freedesktop/DBus \
         --method org.freedesktop.DBus.NameHasOwner "${BUS_NAME}" \
         2>/dev/null | grep -q "true"; then
      owned=true
      break
    fi
    sleep 0.5
    tick=$((tick + 1))
  done
  if [ "${owned}" = true ]; then
    echo "[whisper-aloud] ✓ Daemon is running and registered on D-Bus"
  else
    echo "[whisper-aloud] ⚠ ${BUS_NAME} did not appear within ${WAIT_TIMEOUT_S}s — daemon may still be starting. Check:"
    echo "    journalctl --user -u ${UNIT} -n 30"
  fi
fi

# Optional: install XDG autostart for non-systemd sessions
if [ -f "$CONTRIB_DESKTOP" ]; then
  echo ""
  echo "[whisper-aloud] Tip: for non-systemd login sessions, copy the autostart entry:"
  echo "    mkdir -p \"${AUTOSTART_DIR}\""
  echo "    cp \"${CONTRIB_DESKTOP}\" \"${AUTOSTART_DIR}/\""
fi

echo ""
echo "[whisper-aloud] Done. Use 'whisper-aloud status' to check daemon health."
