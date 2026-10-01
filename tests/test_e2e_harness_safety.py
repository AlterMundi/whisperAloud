"""Static safety check for the e2e harness (WHISP-46 orchestrator amendment
4): daemon processes must only ever be stopped through their own Popen
object (Quit() -> terminate() -> kill()), never by name or pattern. This
greps the harness source so a future edit introducing pkill/killall/os.kill
fails the fast unit suite immediately, without needing to run the (opt-in,
dbus-run-session-only) e2e suite itself."""
import re
from pathlib import Path

E2E_DIR = Path(__file__).parent / "e2e"

_FORBIDDEN = re.compile(r"\bpkill\b|\bkillall\b|os\.kill\(")


def test_no_name_or_pattern_based_kills_in_e2e_harness():
    offenders = []
    for path in E2E_DIR.rglob("*.py"):
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, "forbidden kill pattern found in tests/e2e/:\n" + "\n".join(offenders)
