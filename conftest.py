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
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/whisper-aloud-tests-bus"
for _var in ("WAYLAND_DISPLAY", "DISPLAY"):
    os.environ.pop(_var, None)
for _var, _sub in (
    ("XDG_CONFIG_HOME", "config"),
    ("XDG_DATA_HOME", "data"),
    ("XDG_CACHE_HOME", "cache"),
    ("XDG_STATE_HOME", "state"),
):
    os.environ[_var] = os.path.join(_test_home, _sub)

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
