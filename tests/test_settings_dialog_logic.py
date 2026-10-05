"""Logic tests for the Preferences dialog's view model, without GTK runtime."""

from types import SimpleNamespace

import pytest

from whisper_aloud.config import PASTE_BACKENDS
from whisper_aloud.stt import catalog
from whisper_aloud.ui.settings_logic import (
    LANGUAGE_OPTIONS,
    PASTE_BACKEND_OPTIONS,
    PASTE_BACKEND_SUBTITLE,
    include_current_entry,
    language_options,
    microphone_changes,
    microphone_options,
    normalize_language_input,
    option_index,
    option_value,
    paste_shortcut_for,
    should_auto_close_on_focus_loss,
    uses_preferences_dialog,
    uses_terminal_shortcut,
)


def _device(id_, name, channels=1, is_default=False):
    return SimpleNamespace(id=id_, name=name, channels=channels, is_default=is_default)


# ─── libadwaita version gate ──────────────────────────────────────────────


@pytest.mark.parametrize("minor, expected", [(4, False), (5, True), (7, True)])
def test_preferences_dialog_needs_libadwaita_1_5(minor, expected):
    assert uses_preferences_dialog(minor) is expected


# ─── generic option helpers ───────────────────────────────────────────────


def test_option_index_finds_value_or_falls_back():
    options = [("a", "A"), ("b", "B")]
    assert option_index(options, "b") == 1
    assert option_index(options, "zzz") == 0
    assert option_index(options, "zzz", default=None) is None


def test_option_value_bounds():
    options = [("a", "A"), ("b", "B")]
    assert option_value(options, 1) == "b"
    assert option_value(options, 7) is None
    assert option_value(options, -1) is None


# ─── paste backend (WHISP-119 config key) ─────────────────────────────────


def test_paste_backend_options_cover_every_config_value():
    assert [value for value, _label in PASTE_BACKEND_OPTIONS] == list(PASTE_BACKENDS)


def test_paste_backend_subtitle_mentions_screen_sharing_indicator():
    assert "Remote Desktop" in PASTE_BACKEND_SUBTITLE
    assert "screen" in PASTE_BACKEND_SUBTITLE.lower()


# ─── terminal shortcut switch <-> clipboard.paste_shortcut ────────────────


def test_terminal_shortcut_mapping_round_trips():
    assert paste_shortcut_for(True) == "ctrl+shift+v"
    assert paste_shortcut_for(False) == "ctrl+v"
    assert uses_terminal_shortcut("ctrl+shift+v") is True
    assert uses_terminal_shortcut("ctrl+v") is False


# ─── language ─────────────────────────────────────────────────────────────


def test_language_options_start_with_auto_detect():
    assert LANGUAGE_OPTIONS[0][0] == "auto"
    assert language_options("en") == list(LANGUAGE_OPTIONS)


def test_language_options_keep_an_unlisted_configured_code():
    options = language_options("nl")
    assert options[-1][0] == "nl"
    assert option_index(options, "nl") == len(options) - 1


# ─── microphone ───────────────────────────────────────────────────────────


def test_microphone_options_put_system_default_first_without_emoji():
    devices = [_device(3, "USB Mic", is_default=False), _device(1, "Built-in", is_default=True)]
    options = microphone_options(devices)
    assert options[0] == (None, "System Default")
    assert options[1:] == [(3, "USB Mic"), (1, "Built-in")]
    assert all("⭐" not in label for _v, label in options)


def test_microphone_changes_default_clears_device():
    assert microphone_changes(None, [], current_channels=2) == {"audio.device_id": None}


def test_microphone_changes_clamps_channels_to_a_mono_device():
    devices = [_device(4, "Mono", channels=1)]
    assert microphone_changes(4, devices, current_channels=2) == {
        "audio.device_id": 4,
        "audio.channels": 1,
    }


def test_microphone_changes_keeps_channels_for_a_stereo_device():
    devices = [_device(4, "Stereo", channels=2)]
    assert microphone_changes(4, devices, current_channels=1) == {"audio.device_id": 4}


# ─── model row ────────────────────────────────────────────────────────────


def test_include_current_entry_appends_a_filtered_out_configured_model():
    entries = catalog.models_for("faster-whisper")[:1]
    parakeet = catalog.get("parakeet", "parakeet-tdt-0.6b-v3")
    result = include_current_entry(entries, "parakeet", "parakeet-tdt-0.6b-v3")
    assert result[-1] is parakeet
    assert result[:-1] == entries


def test_include_current_entry_keeps_list_when_present_or_unknown():
    entries = catalog.models_for("faster-whisper")
    first = entries[0]
    assert include_current_entry(entries, first.engine, first.name) == entries
    assert include_current_entry(entries, "nope", "nothing") == entries


# ─── kept helpers ─────────────────────────────────────────────────────────


def test_focus_loss_close_ignored_when_child_dialog_open():
    assert should_auto_close_on_focus_loss(
        child_dialog_open=True,
        contains_focus=False,
        is_visible=True,
    ) is False


def test_focus_loss_close_when_no_focus_and_visible():
    assert should_auto_close_on_focus_loss(
        child_dialog_open=False,
        contains_focus=False,
        is_visible=True,
    ) is True


def test_focus_loss_no_close_when_focus_retained():
    # contains_focus=True means a child widget (e.g. DropDown popup) has focus
    assert should_auto_close_on_focus_loss(
        child_dialog_open=False,
        contains_focus=True,
        is_visible=True,
    ) is False


def test_focus_loss_no_close_when_not_visible():
    assert should_auto_close_on_focus_loss(
        child_dialog_open=False,
        contains_focus=False,
        is_visible=False,
    ) is False


def test_normalize_language_input_empty_to_auto():
    assert normalize_language_input("") == "auto"
    assert normalize_language_input("   ") == "auto"


def test_normalize_language_input_valid_code():
    assert normalize_language_input("EN") == "en"


def test_normalize_language_input_keeps_auto():
    assert normalize_language_input("auto") == "auto"


def test_normalize_language_input_invalid_raises():
    with pytest.raises(ValueError, match="Invalid language code"):
        normalize_language_input("english")
