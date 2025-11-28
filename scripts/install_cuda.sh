#!/bin/bash
# WhisperAloud CUDA Dependencies Installer
# Standalone script to install NVIDIA CUDA libraries for GPU acceleration

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_msg() { echo -e "${BLUE}==>${NC} $1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

# Show usage
usage() {
    cat << EOF
WhisperAloud CUDA Installer

Installs NVIDIA CUDA dependencies (cuDNN, cuBLAS) for GPU acceleration.

Usage: ./scripts/install_cuda.sh [OPTIONS]

Options:
    -h, --help      Show this help message
    -y, --yes       Skip confirmation prompt
    --check         Only check GPU status, don't install

Requirements:
    - NVIDIA GPU with CUDA support
    - NVIDIA driver installed
    - Debian/Ubuntu/Mint or compatible system

EOF
}

# Detect distro
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO=$ID
        DISTRO_VERSION=$VERSION_ID
    else
        DISTRO="unknown"
        DISTRO_VERSION=""
    fi
}

# Detect NVIDIA GPU
detect_nvidia_gpu() {
    if command -v lspci &>/dev/null; then
        if lspci | grep -qi "nvidia"; then
            return 0
        fi
    fi
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        return 0
    fi
    return 1
}

# Get GPU info
get_gpu_info() {
    echo ""
    print_msg "GPU Detection"
    echo ""

    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        echo "NVIDIA Driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null || echo 'unknown')"
        echo "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'unknown')"
        echo "CUDA Version: $(nvidia-smi | grep -oP 'CUDA Version: \K[0-9.]+' 2>/dev/null || echo 'unknown')"
    elif command -v lspci &>/dev/null && lspci | grep -qi nvidia; then
        echo "GPU: $(lspci | grep -i nvidia | grep -i vga | sed 's/.*: //' | head -1)"
        print_warning "NVIDIA driver not installed or not loaded"
    else
        print_error "No NVIDIA GPU detected"
        return 1
    fi

    echo ""

    # Check for CUDA libraries
    print_msg "CUDA Libraries Status"
    echo ""

    local cudnn_found=false
    local cublas_found=false

    if ldconfig -p 2>/dev/null | grep -q libcudnn; then
        print_success "cuDNN: installed"
        cudnn_found=true
    else
        print_warning "cuDNN: not found"
    fi

    if ldconfig -p 2>/dev/null | grep -q libcublas; then
        print_success "cuBLAS: installed"
        cublas_found=true
    else
        print_warning "cuBLAS: not found"
    fi

    echo ""

    if [ "$cudnn_found" = true ] && [ "$cublas_found" = true ]; then
        print_success "All CUDA dependencies installed!"
        return 0
    else
        print_warning "Some CUDA dependencies missing"
        return 1
    fi
}

# Get Ubuntu version for CUDA repo
get_ubuntu_cuda_repo() {
    detect_distro

    case "$DISTRO_VERSION" in
        24.04|24.10) echo "ubuntu2404" ;;
        22.04|22.10) echo "ubuntu2204" ;;
        20.04|20.10) echo "ubuntu2004" ;;
        *)
            # For Mint and derivatives
            if [ -f /etc/upstream-release/lsb-release ]; then
                . /etc/upstream-release/lsb-release
                case "$DISTRIB_RELEASE" in
                    24.04) echo "ubuntu2404" ;;
                    22.04) echo "ubuntu2204" ;;
                    20.04) echo "ubuntu2004" ;;
                    *) echo "ubuntu2204" ;;
                esac
            else
                echo "ubuntu2204"
            fi
            ;;
    esac
}

# Install CUDA dependencies
install_cuda() {
    detect_distro

    case "$DISTRO" in
        debian|ubuntu|linuxmint|pop)
            install_cuda_debian
            ;;
        fedora)
            install_cuda_fedora
            ;;
        arch|manjaro)
            install_cuda_arch
            ;;
        *)
            print_error "Unsupported distribution: $DISTRO"
            echo ""
            echo "Manual installation:"
            echo "  1. Add NVIDIA CUDA repository for your distro"
            echo "  2. Install: libcudnn9-cuda-12 libcublas-12-*"
            echo ""
            echo "See DEPENDENCIES.md for details."
            exit 1
            ;;
    esac
}

install_cuda_debian() {
    local cuda_repo=$(get_ubuntu_cuda_repo)
    local keyring_url="https://developer.download.nvidia.com/compute/cuda/repos/${cuda_repo}/x86_64/cuda-keyring_1.1-1_all.deb"

    print_msg "Installing CUDA dependencies for Debian/Ubuntu..."
    echo ""
    echo "Repository: $cuda_repo"
    echo "Packages: libcudnn9-cuda-12, libcublas-12-8"
    echo ""

    # Download keyring
    print_msg "Adding NVIDIA CUDA repository..."
    local tmp_keyring="/tmp/cuda-keyring.deb"

    if ! wget -q --show-progress "$keyring_url" -O "$tmp_keyring"; then
        print_error "Failed to download CUDA keyring"
        echo ""
        echo "Try manual installation:"
        echo "  wget $keyring_url"
        echo "  sudo dpkg -i cuda-keyring_1.1-1_all.deb"
        exit 1
    fi

    sudo dpkg -i "$tmp_keyring" || true
    rm -f "$tmp_keyring"

    # Update package list
    print_msg "Updating package list..."
    sudo apt update

    # Install CUDA libraries
    print_msg "Installing cuDNN and cuBLAS..."
    if sudo apt install -y libcudnn9-cuda-12 libcublas-12-8; then
        echo ""
        print_success "CUDA dependencies installed successfully!"
        echo ""
        echo "You can now use GPU acceleration in WhisperAloud."
        echo "Set device to 'cuda' or 'auto' in settings."
    else
        print_error "Installation failed"
        exit 1
    fi
}

install_cuda_fedora() {
    print_warning "Fedora requires RPM Fusion for CUDA"
    echo ""
    echo "Manual steps:"
    echo "  1. Enable RPM Fusion: https://rpmfusion.org/"
    echo "  2. Install NVIDIA driver: sudo dnf install akmod-nvidia"
    echo "  3. Install CUDA: sudo dnf install cuda"
    echo ""
    echo "See: https://rpmfusion.org/Howto/CUDA"
    exit 1
}

install_cuda_arch() {
    print_msg "Installing CUDA dependencies for Arch..."

    if sudo pacman -S --needed cuda cudnn; then
        print_success "CUDA dependencies installed!"
    else
        print_warning "pacman install failed - try AUR"
        echo ""
        echo "Install from AUR:"
        echo "  yay -S cudnn"
        exit 1
    fi
}

# Main
SKIP_CONFIRM=false
CHECK_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        -y|--yes)
            SKIP_CONFIRM=true
            shift
            ;;
        --check)
            CHECK_ONLY=true
            shift
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

echo ""
echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    WhisperAloud CUDA Installer             ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"

# Check for GPU
if ! detect_nvidia_gpu; then
    print_error "No NVIDIA GPU detected"
    echo ""
    echo "This script requires an NVIDIA GPU with CUDA support."
    echo "If you have an NVIDIA GPU, make sure the driver is installed."
    exit 1
fi

# Show GPU info
get_gpu_info
gpu_status=$?

if [ "$CHECK_ONLY" = true ]; then
    exit $gpu_status
fi

# If already installed, exit
if [ $gpu_status -eq 0 ]; then
    echo "Nothing to install."
    exit 0
fi

# Confirm installation
if [ "$SKIP_CONFIRM" = false ]; then
    echo ""
    read -p "Install CUDA dependencies now? [Y/n] " response
    case "$response" in
        [nN][oO]|[nN])
            echo "Cancelled."
            exit 0
            ;;
    esac
fi

# Install
install_cuda

echo ""
print_msg "Verifying installation..."
get_gpu_info
