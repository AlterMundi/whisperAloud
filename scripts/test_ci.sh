#!/usr/bin/env bash
set -euo pipefail

# Deterministic CI profile:
# - excludes tests requiring hardware/display/live D-Bus
# - excludes integration tests
#
# Usage:
#   . .venv/bin/activate
#   ./scripts/test_ci.sh

python -m pytest \
  -m "not integration and not requires_audio_hw and not requires_display and not requires_dbus" \
  "$@"
