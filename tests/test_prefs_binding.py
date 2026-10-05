"""Pure tests for the instant-apply Preferences binding (HIG-07, WHISP-102).

No GTK: PrefsBinding is the row <-> config key <-> client.set_config layer the
Preferences dialog is thin glue over.
"""

from types import SimpleNamespace

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.ui.prefs_binding import (
    DEVICE_ID_DEFAULT,
    PrefsBinding,
    apply_flat_changes,
    decode_value,
    encode_changes,
    flatten_config,
    initial_values,
    unflatten,
    validate_values,
)


class FakeClient:
    def __init__(self, accept=True, config=None):
        self.accept = accept
        self.sent = []
        self.config = config if config is not None else {}

    def set_config(self, changes):
        self.sent.append(dict(changes))
        return self.accept

    def get_config(self):
        return dict(self.config)


def _binding(client=None, **overrides):
    values = flatten_config(WhisperAloudConfig())
    values.update(overrides)
    errors = []
    binding = PrefsBinding(client, values, on_error=errors.append)
    return binding, errors


class _Variant:
    """Stands in for a GLib.Variant (has unpack())."""

    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


# ─── flatten / unflatten / validation ──────────────────────────────────────


def test_flatten_uses_dotted_section_keys():
    flat = flatten_config(WhisperAloudConfig())
    assert flat["clipboard.auto_copy"] is True
    assert flat["clipboard.paste_backend"] == "auto"
    assert flat["audio.device_id"] is None


def test_unflatten_round_trips_through_from_dict():
    config = WhisperAloudConfig()
    config.clipboard.paste_delay_ms = 250
    rebuilt = WhisperAloudConfig.from_dict(unflatten(flatten_config(config)))
    assert rebuilt.clipboard.paste_delay_ms == 250


def test_validate_values_accepts_defaults():
    assert validate_values(flatten_config(WhisperAloudConfig())) is None


def test_validate_values_reports_the_config_message():
    flat = flatten_config(WhisperAloudConfig())
    flat["clipboard.paste_backend"] = "carrier-pigeon"
    error = validate_values(flat)
    assert error is not None and "paste_backend" in error


def test_decode_value_unpacks_variants_and_device_sentinel():
    assert decode_value("clipboard.auto_copy", _Variant(False)) is False
    assert decode_value("audio.device_id", DEVICE_ID_DEFAULT) is None
    assert decode_value("audio.device_id", _Variant(DEVICE_ID_DEFAULT)) is None
    assert decode_value("audio.device_id", 4) == 4


def test_encode_changes_maps_default_microphone_to_sentinel():
    assert encode_changes({"audio.device_id": None}) == {"audio.device_id": DEVICE_ID_DEFAULT}
    assert encode_changes({"audio.device_id": 2}) == {"audio.device_id": 2}


def test_initial_values_prefer_the_daemon_config():
    config = WhisperAloudConfig()
    client = FakeClient(config={"clipboard.auto_copy": _Variant(False)})
    values = initial_values(config, client)
    assert values["clipboard.auto_copy"] is False
    # Keys GetConfig omits (None values) come from the local config.
    assert values["audio.device_id"] is None


def test_initial_values_without_daemon_use_the_local_config():
    config = WhisperAloudConfig()
    config.clipboard.auto_paste = False
    assert initial_values(config, None)["clipboard.auto_paste"] is False


def test_initial_values_ignore_a_non_dict_get_config():
    client = SimpleNamespace(get_config=lambda: object())
    assert initial_values(WhisperAloudConfig(), client)["clipboard.auto_copy"] is True


def test_initial_values_treat_a_missing_device_id_as_default():
    config = WhisperAloudConfig()
    config.audio.device_id = 5  # stale local copy
    client = FakeClient(config={"audio.sample_rate": 16000})
    assert initial_values(config, client)["audio.device_id"] is None


def test_apply_flat_changes_returns_an_updated_config_copy():
    config = WhisperAloudConfig()
    updated = apply_flat_changes(config, {"clipboard.auto_copy": _Variant(False)})
    assert updated.clipboard.auto_copy is False
    assert config.clipboard.auto_copy is True  # input untouched


def test_apply_flat_changes_handles_the_device_sentinel():
    config = WhisperAloudConfig()
    config.audio.device_id = 3
    assert apply_flat_changes(config, {"audio.device_id": -1}).audio.device_id is None


# ─── PrefsBinding.apply ────────────────────────────────────────────────────


def test_apply_sends_set_config_immediately():
    client = FakeClient()
    binding, errors = _binding(client)
    result = binding.apply({"clipboard.auto_copy": False})
    assert result.ok is True
    assert client.sent == [{"clipboard.auto_copy": False}]
    assert binding.get("clipboard.auto_copy") is False
    assert errors == []


def test_apply_skips_unchanged_values():
    client = FakeClient()
    binding, _ = _binding(client)
    result = binding.apply({"clipboard.auto_copy": True})
    assert result.ok is True
    assert client.sent == []


def test_apply_sends_only_the_changed_keys_of_a_batch():
    client = FakeClient()
    binding, _ = _binding(client)
    binding.apply({"model.engine": "faster-whisper", "model.name": "small"})
    assert client.sent == [{"model.name": "small"}]


def test_apply_encodes_the_default_microphone():
    client = FakeClient()
    binding, _ = _binding(client, **{"audio.device_id": 2})
    binding.apply({"audio.device_id": None})
    assert client.sent == [{"audio.device_id": DEVICE_ID_DEFAULT}]
    assert binding.get("audio.device_id") is None


def test_invalid_value_is_not_sent_and_reverts_the_row():
    client = FakeClient()
    binding, errors = _binding(client)
    seen = []
    binding.watch("clipboard.paste_backend", seen.append)
    result = binding.apply({"clipboard.paste_backend": "carrier-pigeon"})
    assert result.ok is False
    assert client.sent == []
    assert seen == ["auto"]  # the row is put back
    assert binding.get("clipboard.paste_backend") == "auto"
    assert errors and "paste_backend" in errors[0]


def test_invalid_value_keeps_other_settings():
    client = FakeClient()
    binding, _ = _binding(client)
    binding.apply({"clipboard.auto_copy": False})
    binding.apply({"clipboard.paste_backend": "nope"})
    assert binding.get("clipboard.auto_copy") is False


def test_daemon_rejection_reverts_and_reports():
    client = FakeClient(accept=False)
    binding, errors = _binding(client)
    seen = []
    binding.watch("clipboard.auto_copy", seen.append)
    result = binding.apply({"clipboard.auto_copy": False})
    assert result.ok is False
    assert seen == [True]
    assert binding.get("clipboard.auto_copy") is True
    assert errors and errors[0]


def test_no_client_reverts_and_reports():
    binding, errors = _binding(None)
    seen = []
    binding.watch("clipboard.auto_copy", seen.append)
    assert binding.apply({"clipboard.auto_copy": False}).ok is False
    assert seen == [True]
    assert errors


def test_set_config_exception_is_a_rejection():
    class Boom(FakeClient):
        def set_config(self, changes):
            raise RuntimeError("bus gone")

    binding, errors = _binding(Boom())
    assert binding.apply({"clipboard.auto_copy": False}).ok is False
    assert errors


def test_on_applied_runs_after_a_successful_apply():
    client = FakeClient()
    applied = []
    values = flatten_config(WhisperAloudConfig())
    binding = PrefsBinding(client, values, on_applied=applied.append)
    binding.apply({"clipboard.auto_copy": False})
    binding.apply({"clipboard.auto_copy": False})  # no-op, not reported
    assert applied == [{"clipboard.auto_copy": False}]


# ─── PrefsBinding.config_changed (another client / CLI) ────────────────────


def test_config_changed_updates_watchers():
    binding, _ = _binding(FakeClient())
    seen = []
    binding.watch("clipboard.auto_paste", seen.append)
    changed = binding.config_changed({"clipboard.auto_paste": _Variant(False)})
    assert changed == {"clipboard.auto_paste"}
    assert seen == [False]
    assert binding.get("clipboard.auto_paste") is False


def test_config_changed_echo_of_own_change_is_silent():
    client = FakeClient()
    binding, _ = _binding(client)
    seen = []
    binding.watch("clipboard.auto_copy", seen.append)
    binding.apply({"clipboard.auto_copy": False})
    assert binding.config_changed({"clipboard.auto_copy": False}) == set()
    assert seen == []


def test_config_changed_decodes_the_device_sentinel():
    binding, _ = _binding(FakeClient(), **{"audio.device_id": 3})
    seen = []
    binding.watch("audio.device_id", seen.append)
    binding.config_changed({"audio.device_id": -1})
    assert seen == [None]


def test_empty_config_changed_refetches_everything():
    """ReloadConfig emits ConfigChanged({}): re-read GetConfig."""
    client = FakeClient(config={"clipboard.auto_copy": False, "hotkey.toggle_recording": "<Ctrl>r"})
    binding, _ = _binding(client)
    seen = {}
    binding.watch("clipboard.auto_copy", lambda v: seen.setdefault("copy", v))
    binding.watch("hotkey.toggle_recording", lambda v: seen.setdefault("hk", v))
    changed = binding.config_changed({})
    assert changed == {"clipboard.auto_copy", "hotkey.toggle_recording"}
    assert seen == {"copy": False, "hk": "<Ctrl>r"}


def test_watcher_exception_does_not_break_other_watchers():
    binding, _ = _binding(FakeClient())
    seen = []

    def bad(_value):
        raise RuntimeError("widget gone")

    binding.watch("clipboard.auto_copy", bad)
    binding.watch("clipboard.auto_copy", seen.append)
    binding.config_changed({"clipboard.auto_copy": False})
    assert seen == [False]
