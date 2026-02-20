#!/bin/bash
# Install script for WhisperAloud desktop integration
# Installs desktop file, D-Bus activation, and systemd user unit

set -e

echo "Installing WhisperAloud desktop integration..."

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo!"
    echo "Desktop integration must be installed for the current user."
    echo "Run: ./scripts/install_gnome_integration.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$SCRIPT_DIR/data"

# Install desktop file
echo "Installing desktop file..."
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cp "$DATA_DIR/org.fede.whisperaloud.desktop" "$APPS_DIR/"
update-desktop-database "$APPS_DIR" 2>/dev/null || true
echo "  -> $APPS_DIR/org.fede.whisperaloud.desktop"

# Install D-Bus activation service
echo "Installing D-Bus activation service..."
DBUS_DIR="$HOME/.local/share/dbus-1/services"
mkdir -p "$DBUS_DIR"
cp "$DATA_DIR/org.fede.whisperaloud.service" "$DBUS_DIR/"
echo "  -> $DBUS_DIR/org.fede.whisperaloud.service"

# Install systemd user unit
echo "Installing systemd user unit..."
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
cp "$DATA_DIR/whisper-aloud.service" "$SYSTEMD_DIR/"
systemctl --user daemon-reload
echo "  -> $SYSTEMD_DIR/whisper-aloud.service"

# Remove legacy autostart entry if present
LEGACY_AUTOSTART="$HOME/.config/autostart/whisperaloud-daemon.desktop"
if [ -f "$LEGACY_AUTOSTART" ]; then
    echo "Removing legacy autostart entry..."
    rm "$LEGACY_AUTOSTART"
fi

# Remove legacy desktop file if present
LEGACY_DESKTOP="$HOME/.local/share/applications/com.whisperaloud.App.desktop"
if [ -f "$LEGACY_DESKTOP" ]; then
    echo "Removing legacy desktop file..."
    rm "$LEGACY_DESKTOP"
fi

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  Enable the service:  systemctl --user enable whisper-aloud.service"
echo "  Start the service:   systemctl --user start whisper-aloud.service"
echo "  Check status:        systemctl --user status whisper-aloud.service"
echo "  View logs:           journalctl --user -u whisper-aloud.service"
echo ""
echo "The service will also auto-activate via D-Bus when needed."
echo ""
echo "To uninstall:"
echo "  systemctl --user disable --now whisper-aloud.service"
echo "  rm $SYSTEMD_DIR/whisper-aloud.service"
echo "  rm $DBUS_DIR/org.fede.whisperaloud.service"
echo "  rm $APPS_DIR/org.fede.whisperaloud.desktop"
echo "  systemctl --user daemon-reload"
