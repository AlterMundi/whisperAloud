"""Root conftest: ensure worktree src is first in sys.path."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Isolate the suite from the developer's live desktop session. Without this,
# HotkeyManager tests reached the real GlobalShortcuts portal and popped an
# "Add Keyboard Shortcuts" dialog on every run, and anything resolving XDG
# dirs could touch the real config/history. Tests that need a bus must start
# a private one explicitly.
#
# Hugging Face keeps using the real model cache: redirecting it made every run
# re-download Whisper models into the temp tree (/tmp is a RAM-backed tmpfs).
_real_cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
os.environ.setdefault("HF_HOME", os.path.join(_real_cache, "huggingface"))
_test_home = tempfile.mkdtemp(prefix="whisper-aloud-tests-")
atexit.register(shutil.rmtree, _test_home, ignore_errors=True)
# Remember the original address so tests/_private_bus.py can decide (without
# connecting) whether it is a throwaway dbus-run-session bus.
os.environ.setdefault(
    "WHISPER_ALOUD_TEST_ORIGINAL_DBUS_ADDRESS", os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
)
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/whisper-aloud-tests-bus"
for _var in ("WAYLAND_DISPLAY", "DISPLAY"):
    os.environ.pop(_var, None)
for _var, _sub in (
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_STATE_HOME", "state"),
    ("XDG_RUNTIME_DIR", "runtime"),
):
    os.environ[_var] = os.path.join(_test_home, _sub)
# A real runtime dir holds the wayland-0, pipewire-0 and bus sockets; clipboard
# tools fall back to wayland-0 even without WAYLAND_DISPLAY.
os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)
for _var in ("PULSE_SERVER", "XAUTHORITY", "AT_SPI_BUS_ADDRESS"):
    os.environ.pop(_var, None)
os.environ["NO_AT_BRIDGE"] = "1"

# Last line of defence: no test may launch a tool that acts on the user's
# desktop (clipboard, input injection, volume, notifications, sounds). Tests
# that exercise these code paths mock subprocess themselves, above this layer.
import subprocess  # noqa: E402

_DESKTOP_TOOLS = frozenset({
    "wl-copy", "wl-paste", "xclip", "xsel", "xdotool", "ydotool", "wtype",
    "wpctl", "pactl", "notify-send", "paplay", "canberra-gtk-play",
})
_real_popen_init = subprocess.Popen.__init__


def _guarded_popen_init(self, args, *a, **kw):
    argv0 = args if isinstance(args, (str, bytes)) else (args[0] if args else "")
    if isinstance(argv0, bytes):
        argv0 = argv0.decode(errors="replace")
    name = os.path.basename(str(argv0).split()[0]) if str(argv0).strip() else ""
    if name in _DESKTOP_TOOLS:
        raise RuntimeError(f"test tried to run desktop tool {name!r}; mock it instead")
    _real_popen_init(self, args, *a, **kw)


subprocess.Popen.__init__ = _guarded_popen_init

# Keep the worktree's src ahead of any other whisper_aloud installation
# so that editable-install path resets don't affect test results.
_wt_src = str(Path(__file__).parent / "src")
if _wt_src not in sys.path:
    sys.path.insert(0, _wt_src)

# Pin the GI "Gtk" namespace to 4.0 before any test module can import it.
#
# The suite touches two different GI namespaces named "Gtk": ui/*.py
# requires GTK 4.0, while service/indicator.py requires GTK 3.0 for
# AyatanaAppIndicator3 (the daemon's system tray). In production those two
# live in separate OS processes (daemon vs. GUI) and never share an
# interpreter, so this is not a real conflict. But gi.require_version() is
# first-wins per *process* and raises ValueError on a later conflicting
# request for the same namespace — so inside one pytest run, whichever test
# module happened to be collected/imported first silently decided the
# version for every test after it, making results depend on test order
# (e.g. tests/test_indicator.py before tests/test_level_meter.py blew up
# with "Namespace Gtk is already loaded with version 3.0").
#
# Pinning GTK4 here, first, makes the outcome deterministic: when
# service/indicator.py later runs its own `gi.require_version('Gtk', '3.0')`,
# it now raises ValueError and is caught by the module's existing
# `except (ImportError, ValueError)` guard — the same graceful-degradation
# path already used when AyatanaAppIndicator3/GTK3 is simply absent from the
# host (HAS_INDICATOR=False, AyatanaAppIndicator3/Gtk set to None). Tests
# that exercise indicator behavior patch HAS_INDICATOR/AyatanaAppIndicator3/
# Gtk directly, so this has no effect on their assertions.
try:
    import gi

    gi.require_version("Gtk", "4.0")
except (ImportError, ValueError):
    pass
