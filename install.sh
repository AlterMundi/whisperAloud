#!/bin/bash
# WhisperAloud Installer
# Automated installation script for Debian/Ubuntu and Fedora systems

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
VENV_PATH="${WHISPER_VENV:-$HOME/.venvs/whisper_aloud}"
INSTALL_DEV=false
SKIP_SYSTEM_DEPS=false

# Print colored message
print_msg() {
    echo -e "${BLUE}==>${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Show usage
usage() {
    cat << EOF
WhisperAloud Installer

Usage: ./install.sh [OPTIONS]

Options:
    -h, --help              Show this help message
    -d, --dev               Install development dependencies
    -v, --venv PATH         Custom virtual environment path
                            (default: ~/.venvs/whisper_aloud)
    -s, --skip-system       Skip system dependency installation
                            (useful if already installed)
    --uninstall             Remove WhisperAloud installation

Examples:
    ./install.sh                    # Standard installation
    ./install.sh --dev              # With dev tools (pytest, black, etc.)
    ./install.sh -v ~/my_venv       # Custom venv location
    ./install.sh --uninstall        # Remove installation

EOF
}

# Detect Linux distribution
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO=$ID
        DISTRO_FAMILY=$ID_LIKE
    elif [ -f /etc/debian_version ]; then
        DISTRO="debian"
        DISTRO_FAMILY="debian"
    elif [ -f /etc/fedora-release ]; then
        DISTRO="fedora"
        DISTRO_FAMILY="fedora"
    else
        DISTRO="unknown"
        DISTRO_FAMILY="unknown"
    fi
}

# Install system dependencies for Debian/Ubuntu
install_debian_deps() {
    print_msg "Installing system dependencies (Debian/Ubuntu)..."

    local packages=(
        # Build essentials
        build-essential
        python3-dev
        python3-venv
        python3-pip
        # Audio
        portaudio19-dev
        libportaudio2
        # GTK4 and GObject
        python3-gi
        python3-gi-cairo
        gir1.2-gtk-4.0
        libgtk-4-dev
        # GSound for audio feedback
        gir1.2-gsound-1.0
        libgsound-dev
        # Adwaita theming
        libadwaita-1-dev
        gir1.2-adw-1
        # Clipboard tools
        wl-clipboard
        xclip
    )

    # Optional: ydotool for paste simulation (may not be in all repos)
    if apt-cache show ydotool &>/dev/null; then
        packages+=(ydotool)
    fi

    sudo apt update
    sudo apt install -y "${packages[@]}"

    print_success "System dependencies installed"
}

# Install system dependencies for Fedora
install_fedora_deps() {
    print_msg "Installing system dependencies (Fedora)..."

    local packages=(
        # Build essentials
        gcc
        gcc-c++
        python3-devel
        # Audio
        portaudio-devel
        # GTK4 and GObject
        python3-gobject
        gtk4-devel
        # GSound
        gsound-devel
        # Adwaita
        libadwaita-devel
        # Clipboard
        wl-clipboard
        xclip
        ydotool
    )

    sudo dnf install -y "${packages[@]}"

    print_success "System dependencies installed"
}

# Install system dependencies for Arch
install_arch_deps() {
    print_msg "Installing system dependencies (Arch Linux)..."

    local packages=(
        base-devel
        python
        python-pip
        portaudio
        python-gobject
        gtk4
        gsound
        libadwaita
        wl-clipboard
        xclip
        ydotool
    )

    sudo pacman -S --needed "${packages[@]}"

    print_success "System dependencies installed"
}

# Install system dependencies based on distro
install_system_deps() {
    detect_distro

    case "$DISTRO" in
        debian|ubuntu|linuxmint|pop)
            install_debian_deps
            ;;
        fedora)
            install_fedora_deps
            ;;
        arch|manjaro|endeavouros)
            install_arch_deps
            ;;
        *)
            if [[ "$DISTRO_FAMILY" == *"debian"* ]]; then
                install_debian_deps
            elif [[ "$DISTRO_FAMILY" == *"fedora"* ]] || [[ "$DISTRO_FAMILY" == *"rhel"* ]]; then
                install_fedora_deps
            elif [[ "$DISTRO_FAMILY" == *"arch"* ]]; then
                install_arch_deps
            else
                print_error "Unsupported distribution: $DISTRO"
                print_warning "Please install dependencies manually. See INSTALL.md"
                exit 1
            fi
            ;;
    esac
}

# Create virtual environment
create_venv() {
    print_msg "Creating virtual environment at $VENV_PATH..."

    # Remove existing venv if present
    if [ -d "$VENV_PATH" ]; then
        print_warning "Existing venv found, removing..."
        rm -rf "$VENV_PATH"
    fi

    # Create venv WITH system-site-packages for GTK4 access
    python3 -m venv "$VENV_PATH" --system-site-packages

    print_success "Virtual environment created"
}

# Install Python package
install_package() {
    print_msg "Installing WhisperAloud..."

    # Activate venv
    source "$VENV_PATH/bin/activate"

    # Upgrade pip
    pip install --upgrade pip setuptools wheel

    # Install package
    if [ "$INSTALL_DEV" = true ]; then
        pip install -e ".[dev]"
        print_success "WhisperAloud installed with dev dependencies"
    else
        pip install -e .
        print_success "WhisperAloud installed"
    fi
}

# Verify installation
verify_installation() {
    print_msg "Verifying installation..."

    source "$VENV_PATH/bin/activate"

    local all_ok=true

    # Check GTK4
    if python3 -c "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk" 2>/dev/null; then
        print_success "GTK4 bindings"
    else
        print_error "GTK4 bindings not available"
        all_ok=false
    fi

    # Check GSound
    if python3 -c "import gi; gi.require_version('GSound', '1.0'); from gi.repository import GSound" 2>/dev/null; then
        print_success "GSound bindings"
    else
        print_warning "GSound not available (sound feedback disabled)"
    fi

    # Check whisper_aloud
    if python3 -c "from whisper_aloud import Transcriber, WhisperAloudConfig" 2>/dev/null; then
        print_success "WhisperAloud core"
    else
        print_error "WhisperAloud import failed"
        all_ok=false
    fi

    # Check GUI
    if python3 -c "from whisper_aloud.ui import main" 2>/dev/null; then
        print_success "WhisperAloud GUI"
    else
        print_error "WhisperAloud GUI import failed"
        all_ok=false
    fi

    # Check CLI
    if command -v whisper-aloud-gui &>/dev/null; then
        print_success "CLI commands installed"
    else
        print_warning "CLI commands not in PATH (activate venv first)"
    fi

    if [ "$all_ok" = true ]; then
        return 0
    else
        return 1
    fi
}

# Install desktop file
install_desktop_file() {
    print_msg "Installing desktop file..."

    local desktop_dir="$HOME/.local/share/applications"
    mkdir -p "$desktop_dir"

    # Create desktop file with correct path
    cat > "$desktop_dir/com.whisperaloud.App.desktop" << EOF
[Desktop Entry]
Type=Application
Name=WhisperAloud
Comment=Voice dictation and transcription
Exec=$VENV_PATH/bin/whisper-aloud-gui
Icon=audio-input-microphone
Terminal=false
Categories=Utility;Audio;Accessibility;
Keywords=voice;dictation;transcription;speech;whisper;
StartupNotify=true
X-GNOME-UsesNotifications=true

Actions=StartDaemon;StopDaemon;

[Desktop Action StartDaemon]
Name=Start Daemon
Exec=$VENV_PATH/bin/whisper-aloud --daemon
Icon=media-playback-start

[Desktop Action StopDaemon]
Name=Stop Daemon
Exec=$VENV_PATH/bin/whisper-aloud quit
Icon=media-playback-stop
EOF

    # Update desktop database
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$desktop_dir" 2>/dev/null || true
    fi

    print_success "Desktop file installed"
}

# Uninstall
uninstall() {
    print_msg "Uninstalling WhisperAloud..."

    # Remove venv
    if [ -d "$VENV_PATH" ]; then
        rm -rf "$VENV_PATH"
        print_success "Removed virtual environment"
    fi

    # Remove desktop file
    local desktop_file="$HOME/.local/share/applications/com.whisperaloud.App.desktop"
    if [ -f "$desktop_file" ]; then
        rm "$desktop_file"
        print_success "Removed desktop file"
    fi

    # Note about config and cache
    print_warning "Config and cache directories not removed:"
    echo "  - ~/.config/whisper_aloud/"
    echo "  - ~/.local/share/whisper_aloud/"
    echo "  - ~/.cache/huggingface/ (Whisper models)"
    echo ""
    echo "Remove manually if desired."

    print_success "WhisperAloud uninstalled"
}

# Print final instructions
print_instructions() {
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════${NC}"
    echo -e "${GREEN}   WhisperAloud installed successfully!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════${NC}"
    echo ""
    echo "To use WhisperAloud:"
    echo ""
    echo "  1. Activate the virtual environment:"
    echo -e "     ${BLUE}source $VENV_PATH/bin/activate${NC}"
    echo ""
    echo "  2. Launch the GUI:"
    echo -e "     ${BLUE}whisper-aloud-gui${NC}"
    echo ""
    echo "  Or use the application menu (WhisperAloud)"
    echo ""
    echo "Add this to your ~/.bashrc for convenience:"
    echo -e "  ${YELLOW}alias wa='source $VENV_PATH/bin/activate && whisper-aloud-gui'${NC}"
    echo ""

    # Note about first run
    echo -e "${YELLOW}Note:${NC} First transcription will download the Whisper model (~150MB)."
    echo "      This may take 1-2 minutes depending on your connection."
    echo ""
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        -d|--dev)
            INSTALL_DEV=true
            shift
            ;;
        -v|--venv)
            VENV_PATH="$2"
            shift 2
            ;;
        -s|--skip-system)
            SKIP_SYSTEM_DEPS=true
            shift
            ;;
        --uninstall)
            uninstall
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main installation flow
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║       WhisperAloud Installer v0.1.0        ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
    echo ""

    # Check we're in the right directory
    if [ ! -f "pyproject.toml" ]; then
        print_error "Please run this script from the WhisperAloud repository root"
        exit 1
    fi

    # Check Python version
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    print_msg "Detected Python $PYTHON_VERSION"

    if python3 -c 'import sys; exit(0 if sys.version_info >= (3, 10) else 1)'; then
        print_success "Python version OK"
    else
        print_error "Python 3.10+ required"
        exit 1
    fi

    # Install system dependencies
    if [ "$SKIP_SYSTEM_DEPS" = false ]; then
        install_system_deps
    else
        print_warning "Skipping system dependencies (--skip-system)"
    fi

    # Create venv and install
    create_venv
    install_package

    # Verify
    if verify_installation; then
        install_desktop_file
        print_instructions
    else
        print_error "Installation verification failed"
        print_warning "Some features may not work correctly"
        exit 1
    fi
}

# Run main
main
