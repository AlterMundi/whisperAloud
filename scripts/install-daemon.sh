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

echo "[whisper-aloud] Waiting for D-Bus registration..."
sleep 2

echo "[whisper-aloud] Probing D-Bus..."
if gdbus call --session \
     --dest org.fede.whisperaloud \
     --object-path /org/fede/whisperaloud \
     --method org.freedesktop.DBus.Peer.Ping \
     >/dev/null 2>&1; then
  echo "[whisper-aloud] ✓ Daemon is running and responding on D-Bus"
else
  echo "[whisper-aloud] ⚠ D-Bus probe failed — daemon may still be starting. Check:"
  echo "    journalctl --user -u ${UNIT} -n 30"
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
