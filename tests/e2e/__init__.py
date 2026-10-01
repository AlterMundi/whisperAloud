"""E2E test package: real daemon subprocess on a private dbus-run-session bus.

See docs/superpowers/specs/2026-10-01-whisp46-e2e-harness-design.md. Run only
via scripts/test_e2e.sh -- tests/e2e/conftest.py refuses to run (pytest.exit)
unless it detects a private bus (see bus_guard.py).
"""
