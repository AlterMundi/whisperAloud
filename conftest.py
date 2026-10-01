"""Root conftest: ensure worktree src is first in sys.path."""
import sys
from pathlib import Path

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
