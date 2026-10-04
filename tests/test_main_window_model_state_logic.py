"""Pure-logic tests for how MainWindow presents the daemon's model readiness
state (0.3.0 model preload, D-Bus API 1.6.0). No GTK runtime needed."""

import pytest

from whisper_aloud.ui.main_window_logic import (
    describe_model_state,
    resolve_model_state,
    transcribing_status_text,
)


class _Variant:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


def test_resolve_model_state_reads_state_and_error():
    assert resolve_model_state({"model_state": "missing", "model_error": "gone"}) == (
        "missing",
        "gone",
    )
    assert resolve_model_state(
        {"model_state": _Variant("ready"), "model_error": _Variant("")}
    ) == ("ready", "")


@pytest.mark.parametrize("payload", [None, {}, {"state": "idle"}, "junk"])
def test_resolve_model_state_older_daemon_or_garbage(payload):
    assert resolve_model_state(payload) == ("", "")


@pytest.mark.parametrize(
    "state,detail,text,download,ready",
    [
        ("loading", "", "Loading model…", False, False),
        ("ready", "", "Model ready", False, True),
        ("missing", "Model 'base' is not downloaded", "Model missing — Download", True, False),
        ("downloading", "faster-whisper/base", "Downloading model…", False, False),
        ("error", "CUDA exploded", "Model error: CUDA exploded", False, False),
        ("unloaded", "", "Model not loaded", False, False),
        # Older daemon without model state: show nothing, assume usable.
        ("", "", "", False, True),
    ],
)
def test_describe_model_state(state, detail, text, download, ready):
    view = describe_model_state(state, detail)
    assert view.text == text
    assert view.show_download is download
    assert view.is_ready is ready


def test_describe_model_state_truncates_long_errors():
    view = describe_model_state("error", "x" * 500)
    assert len(view.text) < 120
    assert view.text.endswith("…")


def test_transcribing_status_text_makes_model_wait_visible():
    assert "waiting for the model" in transcribing_status_text("loading").lower()
    assert transcribing_status_text("ready") == "Transcribing... (press Ctrl+X to cancel)"
    assert transcribing_status_text("") == "Transcribing... (press Ctrl+X to cancel)"
