#!/usr/bin/env bash
set -euo pipefail

# Full dependency CI profile:
# - requires runtime deps installed via `pip install -e .[dev]`
# - avoids hardware/display-bound opt-in tests
# - must pass in CI (not best-effort)
#
# Tests intentionally excluded here:
# - integration tests (`tests/test_integration.py`)
# - explicit GTK display opt-in tests (`tests/test_history_ui.py`, `tests/test_level_meter.py`)
# - hardware-dependent tests

python -m pytest \
  tests/test_config.py \
  tests/test_config_persistence.py \
  tests/test_config_reload_integration.py \
  tests/test_audio_device_manager.py \
  tests/test_dbus_client.py \
  tests/test_daemon.py \
  tests/test_cli.py \
  tests/test_history_daemon_manager.py \
  tests/test_indicator.py \
  tests/test_history_manager_transactions.py \
  tests/test_hotkey.py \
  tests/test_level_meter_logic.py \
  tests/test_main_window_logic.py \
  tests/test_validation_helpers.py \
  "$@"
