"""WHISP-87 guards for "start in background at login":

- the `whisper-aloud --daemon` path (what the login entry runs, natively and
  through the Background portal's `commandline`) never imports the GTK4 GUI;
- closing the GUI never stops the daemon: nothing in the GUI package calls
  the daemon's Quit().
"""

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "whisper_aloud"


def test_daemon_entry_point_does_not_import_the_gui():
    code = (
        "import sys\n"
        "import whisper_aloud.__main__ as m\n"
        "import whisper_aloud.service.daemon\n"
        "m.build_parser().parse_args(['--daemon'])\n"
        "bad = sorted(n for n in sys.modules if n.startswith('whisper_aloud.ui'))\n"
        "gtk4 = 'gi.repository.Gtk' in sys.modules and "
        "sys.modules['gi.repository.Gtk']._version == '4.0'\n"
        "print(bad, gtk4)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True
    ).stdout.strip()
    assert out == "[] False", out


def test_gui_never_asks_the_daemon_to_quit():
    offenders = [
        str(p.relative_to(SRC))
        for p in (SRC / "ui").rglob("*.py")
        if "quit_daemon" in p.read_text() or ".Quit(" in p.read_text()
    ]
    assert offenders == []


def test_main_window_cleanup_only_disconnects():
    text = (SRC / "ui" / "main_window.py").read_text()
    start = text.index("    def cleanup(self)")
    body = text[start:]
    assert "self.client.disconnect()" in body
    assert "quit" not in body.lower().split("def ", 2)[1]
