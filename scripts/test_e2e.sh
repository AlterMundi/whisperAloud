#!/bin/sh
# Run the e2e harness (real daemon subprocess, fake audio + fake STT engine)
# against a private dbus-run-session bus. Thin wrapper around
# scripts/test_private_bus.sh, which is the one sanctioned way in this repo
# to get a throwaway bus -- see tests/_private_bus.py for the guard
# tests/e2e/conftest.py relies on to refuse anything else.
#
# PYTHON can be overridden to point at a specific interpreter (e.g. a
# --system-site-packages venv in CI); it defaults to "python" so local
# behavior (an activated venv's python first on PATH) is unchanged. It is
# only exported here so it reaches test_private_bus.sh, which is the script
# that actually invokes it.
#
# Usage:
#   scripts/test_e2e.sh [pytest args]
PYTHON="${PYTHON:-python}"
export PYTHON
exec "$(dirname "$0")/test_private_bus.sh" -m e2e tests/e2e "$@"
