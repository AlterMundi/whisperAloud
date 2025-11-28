#!/usr/bin/env python3
"""
WhisperAloud Dependency Checker

Verifies all required system and Python dependencies are installed.
Run this script to diagnose installation issues.

Usage:
    python scripts/check_dependencies.py
    python scripts/check_dependencies.py --verbose
    python scripts/check_dependencies.py --fix  # Show fix commands
"""

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Status(Enum):
    OK = "ok"
    MISSING = "missing"
    WARNING = "warning"


@dataclass
class Dependency:
    name: str
    description: str
    status: Status = Status.MISSING
    fix_debian: str = ""
    fix_fedora: str = ""
    optional: bool = False
    details: str = ""


# Color codes
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def color(text: str, code: str) -> str:
    """Apply color code to text."""
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{RESET}"


def check_python_version() -> Dependency:
    """Check Python version."""
    dep = Dependency(
        name="Python 3.10+",
        description="Python interpreter",
        fix_debian="sudo apt install python3",
        fix_fedora="sudo dnf install python3",
    )

    version = sys.version_info
    if version >= (3, 10):
        dep.status = Status.OK
        dep.details = f"Python {version.major}.{version.minor}.{version.micro}"
    else:
        dep.details = f"Found {version.major}.{version.minor}, need 3.10+"

    return dep


def check_python_module(
    module: str,
    import_path: Optional[str] = None,
    description: str = "",
    fix_debian: str = "",
    fix_fedora: str = "",
    optional: bool = False,
    gi_version: Optional[tuple[str, str]] = None,
) -> Dependency:
    """Check if a Python module is importable."""
    dep = Dependency(
        name=module,
        description=description,
        fix_debian=fix_debian,
        fix_fedora=fix_fedora,
        optional=optional,
    )

    try:
        if gi_version:
            import gi

            gi.require_version(gi_version[0], gi_version[1])
            # For GI modules, import from gi.repository
            exec(f"from gi.repository import {gi_version[0]}")
        elif import_path:
            # Use exec for complex imports
            exec(f"import {import_path}")
        else:
            __import__(module)

        dep.status = Status.OK

        # Get version if available
        try:
            mod = __import__(module.split(".")[0])
            if hasattr(mod, "__version__"):
                dep.details = f"v{mod.__version__}"
            elif hasattr(mod, "version"):
                dep.details = f"v{mod.version}"
        except Exception:
            pass

    except ImportError as e:
        dep.details = str(e)
    except ValueError as e:
        # gi.require_version failed
        dep.details = str(e)
    except Exception as e:
        dep.details = str(e)

    return dep


def check_command(
    command: str,
    description: str = "",
    fix_debian: str = "",
    fix_fedora: str = "",
    optional: bool = False,
    version_flag: str = "--version",
) -> Dependency:
    """Check if a command is available in PATH."""
    dep = Dependency(
        name=command,
        description=description,
        fix_debian=fix_debian,
        fix_fedora=fix_fedora,
        optional=optional,
    )

    path = shutil.which(command)
    if path:
        dep.status = Status.OK
        dep.details = path

        # Try to get version
        try:
            result = subprocess.run(
                [command, version_flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout or result.stderr
            # Get first line, truncate if too long
            first_line = output.strip().split("\n")[0][:50]
            if first_line:
                dep.details = first_line
        except Exception:
            pass
    else:
        dep.details = "Not found in PATH"

    return dep


def check_library(
    lib_name: str,
    description: str = "",
    fix_debian: str = "",
    fix_fedora: str = "",
) -> Dependency:
    """Check if a shared library is available."""
    dep = Dependency(
        name=lib_name,
        description=description,
        fix_debian=fix_debian,
        fix_fedora=fix_fedora,
    )

    # Try multiple methods to find the library
    found = False

    # Method 1: Try ldconfig (with full path)
    for ldconfig_path in ["/sbin/ldconfig", "/usr/sbin/ldconfig", "ldconfig"]:
        try:
            result = subprocess.run(
                [ldconfig_path, "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and lib_name in result.stdout:
                found = True
                for line in result.stdout.split("\n"):
                    if lib_name in line:
                        parts = line.split("=>")
                        if len(parts) > 1:
                            dep.details = parts[1].strip()
                            break
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Method 2: Try to find the .so file directly
    if not found:
        lib_paths = ["/usr/lib", "/usr/lib/x86_64-linux-gnu", "/lib", "/lib/x86_64-linux-gnu"]
        for lib_path in lib_paths:
            try:
                result = subprocess.run(
                    ["find", lib_path, "-name", f"*{lib_name}*.so*", "-type", "f"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    found = True
                    dep.details = result.stdout.strip().split("\n")[0]
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

    # Method 3: Try pkg-config
    if not found:
        pkg_name = lib_name.replace("lib", "")
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", pkg_name],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                found = True
                dep.details = f"Found via pkg-config ({pkg_name})"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if found:
        dep.status = Status.OK
    else:
        dep.details = "Library not found"

    return dep


def check_venv_system_packages() -> Dependency:
    """Check if venv has access to system packages."""
    dep = Dependency(
        name="venv --system-site-packages",
        description="Virtual environment with system package access",
        fix_debian="Recreate venv: python3 -m venv ~/.venvs/whisper_aloud --system-site-packages",
        fix_fedora="Recreate venv: python3 -m venv ~/.venvs/whisper_aloud --system-site-packages",
    )

    # Check if we're in a venv
    if sys.prefix == sys.base_prefix:
        dep.status = Status.WARNING
        dep.details = "Not running in a virtual environment"
        dep.optional = True
        return dep

    # Check for system site-packages access
    system_paths = [p for p in sys.path if "dist-packages" in p or "site-packages" in p]
    has_system = any("/usr/" in p for p in system_paths)

    if has_system:
        dep.status = Status.OK
        dep.details = "System packages accessible"
    else:
        dep.details = "No system site-packages in path (GTK4 won't work)"

    return dep


def check_all_dependencies() -> list[Dependency]:
    """Check all dependencies and return results."""
    deps = []

    # Python version
    deps.append(check_python_version())

    # Virtual environment check
    deps.append(check_venv_system_packages())

    # Core Python packages (pip installable)
    deps.append(
        check_python_module(
            "numpy",
            description="Numerical computing",
            fix_debian="pip install numpy",
            fix_fedora="pip install numpy",
        )
    )
    deps.append(
        check_python_module(
            "faster_whisper",
            description="Whisper speech recognition",
            fix_debian="pip install faster-whisper",
            fix_fedora="pip install faster-whisper",
        )
    )
    deps.append(
        check_python_module(
            "sounddevice",
            description="Audio recording",
            fix_debian="pip install sounddevice",
            fix_fedora="pip install sounddevice",
        )
    )
    deps.append(
        check_python_module(
            "scipy",
            description="Scientific computing",
            fix_debian="pip install scipy",
            fix_fedora="pip install scipy",
        )
    )

    # GTK4 and GObject (system packages only)
    deps.append(
        check_python_module(
            "gi",
            description="GObject Introspection",
            fix_debian="sudo apt install python3-gi",
            fix_fedora="sudo dnf install python3-gobject",
        )
    )
    deps.append(
        check_python_module(
            "gi.repository.Gtk",
            import_path="gi.repository.Gtk",
            description="GTK4 UI toolkit",
            fix_debian="sudo apt install gir1.2-gtk-4.0",
            fix_fedora="sudo dnf install gtk4-devel",
            gi_version=("Gtk", "4.0"),
        )
    )
    deps.append(
        check_python_module(
            "gi.repository.Adw",
            import_path="gi.repository.Adw",
            description="Adwaita widgets",
            fix_debian="sudo apt install gir1.2-adw-1",
            fix_fedora="sudo dnf install libadwaita-devel",
            gi_version=("Adw", "1"),
            optional=True,
        )
    )
    deps.append(
        check_python_module(
            "gi.repository.GSound",
            import_path="gi.repository.GSound",
            description="Sound feedback",
            fix_debian="sudo apt install gir1.2-gsound-1.0",
            fix_fedora="sudo dnf install gsound-devel",
            gi_version=("GSound", "1.0"),
            optional=True,
        )
    )

    # System libraries
    deps.append(
        check_library(
            "libportaudio",
            description="PortAudio for audio I/O",
            fix_debian="sudo apt install portaudio19-dev libportaudio2",
            fix_fedora="sudo dnf install portaudio-devel",
        )
    )

    # Clipboard tools
    deps.append(
        check_command(
            "wl-copy",
            description="Wayland clipboard",
            fix_debian="sudo apt install wl-clipboard",
            fix_fedora="sudo dnf install wl-clipboard",
            optional=True,
        )
    )
    deps.append(
        check_command(
            "xclip",
            description="X11 clipboard",
            fix_debian="sudo apt install xclip",
            fix_fedora="sudo dnf install xclip",
            optional=True,
        )
    )
    deps.append(
        check_command(
            "ydotool",
            description="Input simulation (Wayland)",
            fix_debian="sudo apt install ydotool",
            fix_fedora="sudo dnf install ydotool",
            optional=True,
        )
    )

    return deps


def print_results(deps: list[Dependency], verbose: bool = False, show_fix: bool = False):
    """Print dependency check results."""
    print()
    print(color("=" * 60, BOLD))
    print(color("  WhisperAloud Dependency Check", BOLD))
    print(color("=" * 60, BOLD))
    print()

    required_ok = 0
    required_fail = 0
    optional_ok = 0
    optional_warn = 0

    for dep in deps:
        # Status indicator
        if dep.status == Status.OK:
            indicator = color("[OK]", GREEN)
            if dep.optional:
                optional_ok += 1
            else:
                required_ok += 1
        elif dep.optional:
            indicator = color("[--]", YELLOW)
            optional_warn += 1
        else:
            indicator = color("[!!]", RED)
            required_fail += 1

        # Optional marker
        opt_marker = color(" (optional)", YELLOW) if dep.optional else ""

        # Print main line
        print(f"  {indicator} {dep.name}{opt_marker}")

        # Verbose: show description and details
        if verbose:
            if dep.description:
                print(f"       {color(dep.description, BLUE)}")
            if dep.details:
                print(f"       {dep.details}")

        # Show fix command if missing and requested
        if show_fix and dep.status == Status.MISSING:
            print(f"       {color('Fix (Debian):', YELLOW)} {dep.fix_debian}")
            if dep.fix_fedora != dep.fix_debian:
                print(f"       {color('Fix (Fedora):', YELLOW)} {dep.fix_fedora}")

        if verbose:
            print()

    # Summary
    print()
    print(color("-" * 60, BOLD))
    total_required = required_ok + required_fail
    total_optional = optional_ok + optional_warn

    print(f"  Required: {color(str(required_ok), GREEN)}/{total_required} OK", end="")
    if required_fail > 0:
        print(f"  {color(f'({required_fail} missing)', RED)}", end="")
    print()

    print(f"  Optional: {color(str(optional_ok), GREEN)}/{total_optional} OK", end="")
    if optional_warn > 0:
        print(f"  {color(f'({optional_warn} missing)', YELLOW)}", end="")
    print()

    print()

    # Final verdict
    if required_fail == 0:
        print(color("  All required dependencies satisfied!", GREEN))
        print()
        return 0
    else:
        print(color("  Some required dependencies are missing!", RED))
        print(f"  Run with {color('--fix', YELLOW)} to see installation commands.")
        print()
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Check WhisperAloud dependencies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed information for each dependency",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Show commands to fix missing dependencies",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    deps = check_all_dependencies()

    if args.json:
        import json

        results = []
        for dep in deps:
            results.append({
                "name": dep.name,
                "description": dep.description,
                "status": dep.status.value,
                "optional": dep.optional,
                "details": dep.details,
                "fix_debian": dep.fix_debian,
                "fix_fedora": dep.fix_fedora,
            })
        print(json.dumps(results, indent=2))
        return 0

    return print_results(deps, verbose=args.verbose, show_fix=args.fix)


if __name__ == "__main__":
    sys.exit(main())
