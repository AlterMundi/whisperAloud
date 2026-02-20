#!/usr/bin/env bash
set -euo pipefail

# Deterministic CI profile:
# - avoids optional heavy runtime deps (audio HW, model runtime, GUI)
# - runs stable fast tests suitable for every PR
#
# Usage:
#   . .venv/bin/activate
#   ./scripts/test_ci.sh

python -m pytest \
  tests/test_config.py \
  tests/test_config_persistence.py \
  tests/test_dbus_client.py \
  tests/test_cli.py \
  tests/test_history_ui.py \
  tests/test_settings_dialog_logic.py \
  "$@"
