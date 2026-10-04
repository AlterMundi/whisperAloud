"""WHISP-47 e2e scenarios: auto-copy via TestClipboardStub, hotkeys disabled
under TEST_MODE, and the ApiVersion property. Run only via
scripts/test_e2e.sh -- see tests/e2e/conftest.py."""
import time

import pytest

from tests.e2e.conftest import SignalCapture

pytestmark = pytest.mark.e2e


def test_auto_copy_writes_to_test_clipboard_file(daemon, audio_fixture, tmp_path):
    """clipboard.auto_copy defaults to True and is not forced off by the
    harness (only auto_paste/real-paste risk is). The daemon's
    TestClipboardStub (WHISPER_ALOUD_TEST_MODE=1) writes every copy to
    $WHISPER_ALOUD_TEST_CLIPBOARD_FILE instead of touching a real
    clipboard -- see src/whisper_aloud/clipboard/clipboard_manager.py."""
    clipboard_file = tmp_path / "clipboard.log"
    fixture = audio_fixture("tone")
    handle = daemon(
        config_overrides={"TEST_CLIPBOARD_FILE": str(clipboard_file)},
        fake_audio="tone",
    )
    client = handle.client()
    ready = SignalCapture(client, "TranscriptionReady")

    assert client.start_recording() is True
    time.sleep(0.1)
    assert client.stop_recording() == "transcribing"
    text, _meta = ready.wait(timeout=10.0)
    assert text == fixture.expected_text

    # The copy happens synchronously in the same GLib.idle_add callback that
    # emits TranscriptionReady, but give the daemon one more context pump
    # worth of margin before reading the file.
    time.sleep(0.1)
    assert clipboard_file.exists(), "TestClipboardStub never wrote the clipboard file"
    assert fixture.expected_text in clipboard_file.read_text()

    client.disconnect()


def test_hotkeys_disabled_and_api_version(client):
    """GetStatus()["hotkey_backend"] == "none": under WHISPER_ALOUD_TEST_MODE=1
    the daemon skips hotkey setup entirely (no portal/libkeybinder contact at
    all), independent of DISPLAY/WAYLAND_DISPLAY/XDG_SESSION_TYPE. Combined
    with the ApiVersion property check (both cheap, read-only, same daemon)
    to save a daemon spawn."""
    status = client.get_status()
    assert status["hotkey_backend"] == "none"

    # Input normalization (docs/design/input-gain-loop.md): a nested a{sv}
    # must survive the real D-Bus round trip, NaN doubles included.
    input_gain = status["input_gain"]
    assert input_gain["mode"] == "digital"
    assert input_gain["enabled"] is True
    assert input_gain["hardware_control"] is False
    assert input_gain["target_dbfs"] == -20.0
    assert input_gain["last_gain_db"] != input_gain["last_gain_db"]  # NaN before any recording

    # 1.1.0 as of WHISP-57's additive PartialTranscript signal -- assert the
    # major is still 1 (no breaking Control2 bump) rather than pinning the
    # exact minor, so the next additive bump doesn't require editing this.
    version = client.get_api_version()
    assert version.startswith("1."), version
