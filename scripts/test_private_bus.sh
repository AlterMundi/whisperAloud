#!/bin/sh
# Run pytest on a throwaway D-Bus session bus. The bus lives exactly as long
# as this command: dbus-run-session tears it down on exit, so nothing ever
# needs to be killed. Example:
#   scripts/test_private_bus.sh -m dbus_private tests/test_hotkey_portal_flow.py
exec timeout "${WHISPER_ALOUD_TEST_TIMEOUT:-300}" \
    dbus-run-session -- env WHISPER_ALOUD_PRIVATE_BUS=1 python -m pytest "$@"
