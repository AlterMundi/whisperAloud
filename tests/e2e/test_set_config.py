"""WHISP-70 e2e coverage: WhisperAloudClient.set_config() against a real
daemon over a real D-Bus connection.

Before the fix, set_config() forwarded a plain Python dict straight to the
pydbus proxy's SetConfig(a{sv}) call without wrapping each value as a
GLib.Variant. pydbus validates outgoing argument types against the real
introspected signature, so this raised "Expected GLib.Variant, but got str"
and the call never reached the daemon -- set_config() always returned False
against a real daemon (tests/test_dbus_client.py's set_config tests mock the
proxy, which skips real argument-type checking and never caught this).

tests/e2e/test_config.py (added on branch test/WHISP-47-e2e-scenarios,
not yet on master at the time this file was written) covers the same bug
as test_set_config_fails_due_to_missing_variant_wrapping; if/when that
branch merges, these two files should be reconciled/deduplicated.
Run only via scripts/test_e2e.sh -- see tests/e2e/conftest.py."""
import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_set_config_applies_language_change_against_real_daemon(client):
    """A real set_config() call must actually reach the daemon (not fail
    client-side on variant wrapping), apply the change, and be reflected in
    both the ConfigChanged signal and a follow-up GetConfig."""
    changed = SignalCapture(client, "ConfigChanged")

    assert client.set_config({"transcription.language": "en"}) is True

    changes = changed.wait(timeout=5.0)[0]
    assert "transcription.language" in changes

    config = client.get_config()
    assert config["transcription.language"] == "en"


def test_set_config_invalid_value_fails_at_the_daemon_not_the_client(client):
    """An invalid value should fail validation *inside* the daemon
    (config_invalid), proving the call actually reached SetConfig rather
    than erroring out client-side before it was ever sent."""
    error = SignalCapture(client, "Error")

    assert client.set_config({"model.engine": "not-a-real-engine"}) is False

    code, message = error.wait(timeout=5.0)
    assert code == "config_invalid"
    assert message


def test_set_config_wraps_bool_and_int_values_too(client):
    """Cover the other variant types the daemon's config schema uses (not
    just str), since each needs its own GLib.Variant type code."""
    changed = SignalCapture(client, "ConfigChanged")

    assert client.set_config({"transcription.vad_filter": False, "transcription.beam_size": 3}) is True

    changed.wait(timeout=5.0)
    config = client.get_config()
    assert config["transcription.vad_filter"] is False
    assert config["transcription.beam_size"] == 3
