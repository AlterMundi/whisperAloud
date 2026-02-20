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
INSTALL_CUDA=false
CUDA_AUTO_DETECT=true

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
    --cuda                  Install NVIDIA CUDA dependencies (cuDNN, cuBLAS)
                            for GPU acceleration
    --no-cuda               Skip CUDA even if NVIDIA GPU detected
    --uninstall             Remove WhisperAloud installation

Examples:
    ./install.sh                    # Standard install (prompts for CUDA if GPU found)
    ./install.sh --cuda             # Install with CUDA support
    ./install.sh --no-cuda          # CPU-only, skip GPU detection
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
        DISTRO_VERSION=$VERSION_ID
    elif [ -f /etc/debian_version ]; then
        DISTRO="debian"
        DISTRO_FAMILY="debian"
        DISTRO_VERSION=$(cat /etc/debian_version)
    elif [ -f /etc/fedora-release ]; then
        DISTRO="fedora"
        DISTRO_FAMILY="fedora"
        DISTRO_VERSION=""
    else
        DISTRO="unknown"
        DISTRO_FAMILY="unknown"
        DISTRO_VERSION=""
    fi
}

# Detect NVIDIA GPU
detect_nvidia_gpu() {
    # Check via lspci (most reliable, doesn't need drivers)
    if command -v lspci &>/dev/null; then
        if lspci | grep -qi "nvidia"; then
            return 0
        fi
    fi

    # Check via nvidia-smi (requires driver)
    if command -v nvidia-smi &>/dev/null; then
        if nvidia-smi &>/dev/null; then
            return 0
        fi
    fi

    return 1
}

# Get NVIDIA GPU name
get_gpu_name() {
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1
    elif command -v lspci &>/dev/null; then
        lspci | grep -i nvidia | grep -i vga | sed 's/.*: //' | head -1
    else
        echo "NVIDIA GPU"
    fi
}

# Get Ubuntu version code for CUDA repo
get_ubuntu_cuda_repo() {
    case "$DISTRO_VERSION" in
        24.04|24.10) echo "ubuntu2404" ;;
        22.04|22.10) echo "ubuntu2204" ;;
        20.04|20.10) echo "ubuntu2004" ;;
        *)
            # For Mint and derivatives, map to Ubuntu base
            if [ -f /etc/upstream-release/lsb-release ]; then
                . /etc/upstream-release/lsb-release
                case "$DISTRIB_RELEASE" in
                    24.04) echo "ubuntu2404" ;;
                    22.04) echo "ubuntu2204" ;;
                    20.04) echo "ubuntu2004" ;;
                    *) echo "ubuntu2204" ;;  # fallback
                esac
            else
                echo "ubuntu2204"  # safe fallback
            fi
            ;;
    esac
}

# Install CUDA dependencies for Debian/Ubuntu
install_cuda_debian() {
    print_msg "Installing CUDA dependencies (cuDNN, cuBLAS)..."

    local cuda_repo=$(get_ubuntu_cuda_repo)
    local keyring_url="https://developer.download.nvidia.com/compute/cuda/repos/${cuda_repo}/x86_64/cuda-keyring_1.1-1_all.deb"

    print_msg "Adding NVIDIA CUDA repository (${cuda_repo})..."

    # Download and install keyring
    local tmp_keyring="/tmp/cuda-keyring.deb"
    if ! wget -q "$keyring_url" -O "$tmp_keyring"; then
        print_error "Failed to download CUDA keyring"
        print_warning "You may need to install CUDA manually. See DEPENDENCIES.md"
        return 1
    fi

    sudo dpkg -i "$tmp_keyring" || true
    rm -f "$tmp_keyring"

    # Update and install CUDA libraries
    sudo apt update

    print_msg "Installing cuDNN and cuBLAS..."
    if sudo apt install -y libcudnn9-cuda-12 libcublas-12-8; then
        print_success "CUDA dependencies installed"
        return 0
    else
        print_error "Failed to install CUDA dependencies"
        print_warning "GPU acceleration may not work. Use device=cpu as fallback."
        return 1
    fi
}

# Install CUDA dependencies for Fedora
install_cuda_fedora() {
    print_msg "Installing CUDA dependencies (Fedora)..."

    # Fedora uses RPM Fusion for NVIDIA
    print_warning "Fedora CUDA setup requires RPM Fusion and manual steps"
    print_msg "See: https://rpmfusion.org/Howto/CUDA"

    # Try to install if repos are already set up
    if sudo dnf install -y cuda-cudnn cuda-cublas 2>/dev/null; then
        print_success "CUDA dependencies installed"
        return 0
    else
        print_warning "CUDA installation skipped - manual setup may be required"
        return 1
    fi
}

# Install CUDA dependencies for Arch
install_cuda_arch() {
    print_msg "Installing CUDA dependencies (Arch)..."

    if sudo pacman -S --needed cudnn 2>/dev/null; then
        print_success "CUDA dependencies installed"
        return 0
    else
        print_warning "CUDA installation failed - check AUR for cudnn"
        return 1
    fi
}

# Install CUDA dependencies based on distro
install_cuda_deps() {
    detect_distro

    case "$DISTRO" in
        debian|ubuntu|linuxmint|pop)
            install_cuda_debian
            ;;
        fedora)
            install_cuda_fedora
            ;;
        arch|manjaro|endeavouros)
            install_cuda_arch
            ;;
        *)
            if [[ "$DISTRO_FAMILY" == *"debian"* ]] || [[ "$DISTRO_FAMILY" == *"ubuntu"* ]]; then
                install_cuda_debian
            else
                print_warning "CUDA auto-install not supported for $DISTRO"
                print_msg "See DEPENDENCIES.md for manual installation"
                return 1
            fi
            ;;
    esac
}

# Prompt user for CUDA installation
prompt_cuda_install() {
    local gpu_name=$(get_gpu_name)

    echo ""
    print_msg "NVIDIA GPU detected: $gpu_name"
    echo ""
    echo "CUDA support enables GPU acceleration for faster transcription."
    echo "This requires ~500MB download and sudo access."
    echo ""
    read -p "Install CUDA dependencies? [Y/n] " response

    case "$response" in
        [nN][oO]|[nN])
            print_warning "Skipping CUDA installation"
            print_msg "You can install later with: ./scripts/install_cuda.sh"
            return 1
            ;;
        *)
            return 0
            ;;
    esac
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

# Install user-level CLI shims in ~/.local/bin
install_cli_shims() {
    print_msg "Installing user CLI shims in ~/.local/bin..."

    local user_bin="$HOME/.local/bin"
    mkdir -p "$user_bin"

    ln -sf "$VENV_PATH/bin/whisper-aloud" "$user_bin/whisper-aloud"
    ln -sf "$VENV_PATH/bin/whisper-aloud-daemon" "$user_bin/whisper-aloud-daemon"
    ln -sf "$VENV_PATH/bin/whisper-aloud-gui" "$user_bin/whisper-aloud-gui"

    if [[ ":$PATH:" != *":$user_bin:"* ]]; then
        print_warning "$user_bin is not in PATH for this shell."
        print_msg "Add this to your shell profile:"
        echo "  export PATH=\"$user_bin:\$PATH\""
    fi

    print_success "CLI shims installed"
}

# Install user systemd service and D-Bus activation
install_user_service() {
    print_msg "Installing user systemd service..."

    local user_systemd_dir="$HOME/.config/systemd/user"
    local user_dbus_dir="$HOME/.local/share/dbus-1/services"
    local service_file="$user_systemd_dir/whisper-aloud.service"
    local dbus_file="$user_dbus_dir/org.fede.whisperaloud.service"

    mkdir -p "$user_systemd_dir"
    mkdir -p "$user_dbus_dir"

    cat > "$service_file" << EOF
[Unit]
Description=WhisperAloud Transcription Service
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=dbus
BusName=org.fede.whisperaloud
ExecStart=$VENV_PATH/bin/whisper-aloud-daemon
Restart=on-failure
RestartSec=3
TimeoutStartSec=120

[Install]
WantedBy=default.target
EOF

    cat > "$dbus_file" << EOF
[D-BUS Service]
Name=org.fede.whisperaloud
SystemdService=whisper-aloud.service
EOF

    if command -v systemctl &>/dev/null; then
        if systemctl --user daemon-reload 2>/dev/null; then
            if systemctl --user enable --now whisper-aloud.service 2>/dev/null; then
                print_success "User service enabled and started (whisper-aloud.service)"
            else
                print_warning "Could not enable/start user service automatically"
                print_msg "You can run: systemctl --user enable --now whisper-aloud.service"
            fi
        else
            print_warning "Could not reload user systemd daemon (no user session?)"
            print_msg "After login, run: systemctl --user daemon-reload"
            print_msg "Then run: systemctl --user enable --now whisper-aloud.service"
        fi
    else
        print_warning "systemctl not found; user service installed but not activated"
    fi

    print_success "User service files installed"
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

    # Remove user-level CLI shims
    for shim in whisper-aloud whisper-aloud-daemon whisper-aloud-gui; do
        local shim_path="$HOME/.local/bin/$shim"
        if [ -L "$shim_path" ]; then
            rm "$shim_path"
            print_success "Removed CLI shim: $shim"
        fi
    done

    # Remove user systemd service + D-Bus activation
    local user_systemd_dir="$HOME/.config/systemd/user"
    local user_dbus_dir="$HOME/.local/share/dbus-1/services"
    local service_file="$user_systemd_dir/whisper-aloud.service"
    local dbus_file="$user_dbus_dir/org.fede.whisperaloud.service"

    if command -v systemctl &>/dev/null; then
        systemctl --user disable --now whisper-aloud.service 2>/dev/null || true
    fi

    if [ -f "$service_file" ]; then
        rm "$service_file"
        print_success "Removed user service file"
    fi

    if [ -f "$dbus_file" ]; then
        rm "$dbus_file"
        print_success "Removed D-Bus activation file"
    fi

    if command -v systemctl &>/dev/null; then
        systemctl --user daemon-reload 2>/dev/null || true
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
    echo "  Commands are installed in ~/.local/bin:"
    echo -e "     ${BLUE}whisper-aloud${NC}"
    echo -e "     ${BLUE}whisper-aloud-daemon${NC}"
    echo -e "     ${BLUE}whisper-aloud-gui${NC}"
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
        --cuda)
            INSTALL_CUDA=true
            CUDA_AUTO_DETECT=false
            shift
            ;;
        --no-cuda)
            INSTALL_CUDA=false
            CUDA_AUTO_DETECT=false
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

    # Handle CUDA installation
    if [ "$INSTALL_CUDA" = true ]; then
        # Explicit --cuda flag: install CUDA
        install_cuda_deps
    elif [ "$CUDA_AUTO_DETECT" = true ]; then
        # Auto-detect: check for NVIDIA GPU and prompt
        if detect_nvidia_gpu; then
            if prompt_cuda_install; then
                install_cuda_deps
            fi
        else
            print_msg "No NVIDIA GPU detected, skipping CUDA"
        fi
    else
        # --no-cuda flag: skip silently
        print_msg "Skipping CUDA installation (--no-cuda)"
    fi

    # Create venv and install
    create_venv
    install_package

    # Verify
    if verify_installation; then
        install_cli_shims
        install_user_service
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
