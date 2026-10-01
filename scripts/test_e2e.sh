#!/bin/sh
# Run the e2e harness (real daemon subprocess, fake audio + fake STT engine)
# against a private dbus-run-session bus. Thin wrapper around
# scripts/test_private_bus.sh, which is the one sanctioned way in this repo
# to get a throwaway bus -- see tests/_private_bus.py for the guard
# tests/e2e/conftest.py relies on to refuse anything else.
#
# Usage:
#   scripts/test_e2e.sh [pytest args]
exec "$(dirname "$0")/test_private_bus.sh" -m e2e tests/e2e "$@"
