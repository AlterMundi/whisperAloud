#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import importlib.metadata as md
import shutil
import subprocess
import sys

expected = {
    "whisper-aloud",
    "whisper-aloud-transcribe",
    "whisper-aloud-gui",
    "whisper-aloud-daemon",
}

entrypoints = {ep.name for ep in md.entry_points(group="console_scripts")}
missing = sorted(expected - entrypoints)
if missing:
    raise SystemExit(f"Missing console scripts in metadata: {missing}")

for cmd in sorted(expected):
    if shutil.which(cmd) is None:
        raise SystemExit(f"Console script not found in PATH: {cmd}")

help_cmds = [
    ["whisper-aloud", "--help"],
    ["whisper-aloud-transcribe", "--help"],
]
for cmd in help_cmds:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"Command failed: {' '.join(cmd)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

# daemon entrypoint is long-running by design; validate importability instead.
from whisper_aloud.__main__ import daemon_main, main  # noqa: E402

if not callable(main) or not callable(daemon_main):
    raise SystemExit("Entry point callables are not importable")

print("Packaging smoke checks passed")
PY
