"""Unit tests for ui/daemon_connect_logic.py (WHISP-60).

Pure decision/backoff logic for the GUI's daemon-connection lifecycle: no GTK,
no D-Bus, no threading -- see src/whisper_aloud/ui/daemon_connect_logic.py for
why this needs to be pure (MainWindow drives real D-Bus activation attempts
from a background thread and must marshal the *decision* this module makes
back onto the GLib main loop; that marshaling is not testable here, but the
decision itself is).
"""

import pytest

from whisper_aloud.ui.daemon_connect_logic import (
    ConnectDecision,
    ConnectPhase,
    backoff_delay_seconds,
    decide_after_attempt,
)


class TestBackoffDelaySeconds:
    def test_first_attempt_uses_base_delay(self):
        assert backoff_delay_seconds(1, base_delay=1.0, max_delay=8.0) == 1.0

    def test_delay_doubles_each_attempt(self):
        assert backoff_delay_seconds(2, base_delay=1.0, max_delay=8.0) == 2.0
        assert backoff_delay_seconds(3, base_delay=1.0, max_delay=8.0) == 4.0

    def test_delay_is_capped_at_max_delay(self):
        assert backoff_delay_seconds(10, base_delay=1.0, max_delay=8.0) == 8.0

    def test_custom_base_delay_scales_linearly(self):
        assert backoff_delay_seconds(1, base_delay=0.5, max_delay=8.0) == 0.5
        assert backoff_delay_seconds(2, base_delay=0.5, max_delay=8.0) == 1.0

    @pytest.mark.parametrize("attempt", [0, -1, -5])
    def test_rejects_non_positive_attempt(self, attempt):
        with pytest.raises(ValueError):
            backoff_delay_seconds(attempt)


class TestDecideAfterAttempt:
    def test_connected_short_circuits_to_connected_phase(self):
        decision = decide_after_attempt(connected=True, attempt=1)
        assert decision == ConnectDecision(phase=ConnectPhase.CONNECTED, attempt=1)

    def test_connected_on_a_later_attempt_still_reports_connected(self):
        decision = decide_after_attempt(connected=True, attempt=5, max_auto_retries=3)
        assert decision.phase is ConnectPhase.CONNECTED

    def test_first_failure_schedules_a_retry_with_backoff(self):
        decision = decide_after_attempt(
            connected=False, attempt=1, max_auto_retries=3, base_delay=1.0, max_delay=8.0
        )
        assert decision.phase is ConnectPhase.WAITING_RETRY
        assert decision.attempt == 1
        assert decision.retry_delay_seconds == 1.0

    def test_delay_grows_on_successive_failures(self):
        first = decide_after_attempt(connected=False, attempt=1, max_auto_retries=5)
        second = decide_after_attempt(connected=False, attempt=2, max_auto_retries=5)
        assert second.retry_delay_seconds > first.retry_delay_seconds

    def test_exhausting_auto_retries_reports_unavailable(self):
        decision = decide_after_attempt(connected=False, attempt=3, max_auto_retries=3)
        assert decision.phase is ConnectPhase.UNAVAILABLE
        assert decision.retry_delay_seconds == 0.0

    def test_failure_past_the_retry_budget_still_reports_unavailable(self):
        decision = decide_after_attempt(connected=False, attempt=4, max_auto_retries=3)
        assert decision.phase is ConnectPhase.UNAVAILABLE

    def test_zero_max_auto_retries_goes_straight_to_unavailable(self):
        decision = decide_after_attempt(connected=False, attempt=1, max_auto_retries=0)
        assert decision.phase is ConnectPhase.UNAVAILABLE

    @pytest.mark.parametrize("attempt", [0, -1])
    def test_rejects_non_positive_attempt(self, attempt):
        with pytest.raises(ValueError):
            decide_after_attempt(connected=False, attempt=attempt)

    def test_decision_is_immutable(self):
        decision = decide_after_attempt(connected=True, attempt=1)
        with pytest.raises(AttributeError):
            decision.attempt = 99
