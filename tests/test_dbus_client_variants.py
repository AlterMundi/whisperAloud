"""Unit tests for the a{sv} GLib.Variant wrapping used by
WhisperAloudClient.set_config() (WHISP-70).

These import whisper_aloud.service.client directly, with the real pydbus
and gi (PyGObject) bindings -- no proxy mock needed, since the functions
under test are pure value->Variant converters that never touch the bus.
PyGObject only comes from the system package (python3-gi), not pip (see
CLAUDE.md), so this file skips itself where that isn't on the path (e.g. a
bare pip-only CI job) rather than failing collection -- the dev venv and
the e2e/gui CI jobs (which build a --system-site-packages venv) do have it.
"""
import pytest

gi = pytest.importorskip("gi")
pytest.importorskip("gi.repository")
from gi.repository import GLib  # noqa: E402

pydbus = pytest.importorskip("pydbus")

from whisper_aloud.service.client import _variant_for_value, _wrap_config_changes  # noqa: E402


class TestVariantForValue:
    """_variant_for_value(value) -> GLib.Variant, one Python value at a time."""

    def test_bool_true_wraps_as_b(self):
        v = _variant_for_value(True)
        assert isinstance(v, GLib.Variant)
        assert v.get_type_string() == "b"
        assert v.unpack() is True

    def test_bool_false_wraps_as_b(self):
        v = _variant_for_value(False)
        assert v.get_type_string() == "b"
        assert v.unpack() is False

    def test_bool_checked_before_int(self):
        """bool is an int subclass in Python -- must not fall through to 'i'."""
        v = _variant_for_value(True)
        assert v.get_type_string() == "b"

    def test_int_wraps_as_i(self):
        v = _variant_for_value(42)
        assert v.get_type_string() == "i"
        assert v.unpack() == 42

    def test_negative_int_wraps_as_i(self):
        v = _variant_for_value(-7)
        assert v.get_type_string() == "i"
        assert v.unpack() == -7

    def test_float_wraps_as_d(self):
        v = _variant_for_value(3.5)
        assert v.get_type_string() == "d"
        assert v.unpack() == 3.5

    def test_str_wraps_as_s(self):
        v = _variant_for_value("en")
        assert v.get_type_string() == "s"
        assert v.unpack() == "en"

    def test_existing_variant_passed_through_unchanged(self):
        original = GLib.Variant("s", "already-wrapped")
        result = _variant_for_value(original)
        assert result is original

    def test_unsupported_type_raises_type_error(self):
        import pytest

        with pytest.raises(TypeError):
            _variant_for_value(["not", "a", "scalar"])

    def test_none_raises_type_error(self):
        import pytest

        with pytest.raises(TypeError):
            _variant_for_value(None)


class TestWrapConfigChanges:
    """_wrap_config_changes(dict) -> dict of GLib.Variant, for SetConfig(a{sv})."""

    def test_wraps_each_value(self):
        changes = {
            "transcription.language": "en",
            "transcription.beam_size": 5,
            "transcription.vad_filter": True,
            "recording_flow.target_gain_linear": 0.85,
        }
        wrapped = _wrap_config_changes(changes)

        assert set(wrapped.keys()) == set(changes.keys())
        assert wrapped["transcription.language"].get_type_string() == "s"
        assert wrapped["transcription.beam_size"].get_type_string() == "i"
        assert wrapped["transcription.vad_filter"].get_type_string() == "b"
        assert wrapped["recording_flow.target_gain_linear"].get_type_string() == "d"

    def test_empty_dict_wraps_to_empty_dict(self):
        assert _wrap_config_changes({}) == {}

    def test_raises_clear_error_for_unsupported_value(self):
        import pytest

        with pytest.raises(TypeError):
            _wrap_config_changes({"bad.key": {"nested": "dict"}})
