#!/bin/bash
# Install script for GNOME integration

set -e

echo "Installing WhisperAloud GNOME integration..."

# Check if we're on GNOME
if ! command -v gnome-shell &> /dev/null; then
    echo "GNOME Shell not found. This script is for GNOME integration."
    exit 1
fi

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    echo "ERROR: Do not run this script with sudo!"
    echo "GNOME extensions must be installed for the current user, not root."
    echo "Run: ./scripts/install_gnome_integration.sh"
    exit 1
fi

# Get GNOME version
GNOME_VERSION=$(gnome-shell --version | grep -oP '\d+\.\d+')
echo "Detected GNOME version: $GNOME_VERSION"

# Install extension
EXTENSIONS_DIR="$HOME/.local/share/gnome-shell/extensions"
EXT_UUID="whisperaloud@fede"
EXT_DIR="$EXTENSIONS_DIR/$EXT_UUID"

echo "Installing GNOME Shell extension..."
mkdir -p "$EXT_DIR"
cp -r gnome-extension/* "$EXT_DIR/"

# Compile schemas
if command -v glib-compile-schemas &> /dev/null; then
    echo "Compiling GSettings schemas..."
    glib-compile-schemas "$EXT_DIR/schemas"
else
    echo "WARNING: glib-compile-schemas not found. Shortcuts may not work."
fi

# Enable extension
echo "Enabling extension..."
gnome-extensions enable "$EXT_UUID"

# Install desktop file
echo "Installing desktop file..."
mkdir -p "$HOME/.local/share/applications"
if [ -f "com.whisperaloud.App.desktop" ]; then
    cp "com.whisperaloud.App.desktop" "$HOME/.local/share/applications/"
    echo "Desktop file installed. You can find WhisperAloud in your applications menu."
else
    echo "Desktop file not found, skipping"
fi

# Install autostart
echo "Setting up autostart..."
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/whisperaloud-daemon.desktop" << EOF
[Desktop Entry]
Type=Application
Name=WhisperAloud Daemon
Comment=Background daemon for WhisperAloud voice dictation
Exec=whisper-aloud --daemon
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

echo "Installation complete!"
echo ""
echo "GNOME Integration Options:"
echo ""
echo "Option 1 - GNOME Shell Extension (Recommended):"
echo "1. Log out and log back in (Wayland doesn't support shell restart)"
echo "2. You should see a microphone icon in the top panel"
echo "3. Start the daemon: whisper-aloud --daemon"
echo "4. Click the icon to control recording"
echo ""
echo "Option 2 - Desktop Integration (Alternative):"
echo "1. Find 'WhisperAloud' in your applications menu"
echo "2. Right-click the icon for daemon controls"
echo "3. Use terminal commands: whisper-aloud start/stop/status"
echo ""
echo "To uninstall:"
echo "  gnome-extensions disable $EXT_UUID"
echo "  rm -rf $EXT_DIR"
echo "  rm $HOME/.local/share/applications/com.whisperaloud.App.desktop"
echo "  rm $AUTOSTART_DIR/whisperaloud-daemon.desktop"