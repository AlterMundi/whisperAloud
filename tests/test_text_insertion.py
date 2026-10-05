"""ExtensionTextInserter and its pure helpers (WHISP-119), without a bus.

The D-Bus round trips against a fake extension on a private bus are in
tests/test_text_insertion_private_bus.py.
"""

from unittest.mock import MagicMock

import pytest

from whisper_aloud.clipboard import text_insertion as ti


class TestParseResult:
    def test_commit(self):
        r = ti.parse_insert_result({"method": "commit", "reason": ""})
        assert r == ti.InsertResult("commit", "")
        assert r.inserted

    def test_paste(self):
        assert ti.parse_insert_result({"method": "paste", "reason": ""}).inserted

    def test_none_with_reason(self):
        r = ti.parse_insert_result({"method": "none", "reason": "locked"})
        assert r == ti.InsertResult("none", "locked")
        assert not r.inserted

    def test_garbage_is_not_inserted(self):
        assert ti.parse_insert_result({}) == ti.InsertResult("none", "")
        assert not ti.parse_insert_result({"method": "teleport"}).inserted
        assert not ti.parse_insert_result(None).inserted


class TestNotices:
    @pytest.mark.parametrize("method", ["commit", "paste"])
    def test_success_has_no_notice(self, method):
        assert ti.notice_for_result(ti.InsertResult(method, "")) is None

    @pytest.mark.parametrize("reason", ["window-mismatch", "no-focus"])
    def test_focus_reasons(self, reason):
        assert ti.notice_for_result(ti.InsertResult("none", reason)) == ti.NOTICE_FOCUS_CHANGED

    def test_locked(self):
        assert ti.notice_for_result(ti.InsertResult("none", "locked")) == ti.NOTICE_LOCKED

    def test_password(self):
        assert ti.notice_for_result(
            ti.InsertResult("none", "password-field")) == ti.NOTICE_PASSWORD

    @pytest.mark.parametrize("reason", [
        "modal", "no-im-focus", "preedit", "terminal-newline", "invalid-option",
        "no-virtual-keyboard", ti.CALL_FAILED, "something-new"])
    def test_everything_else_says_press_ctrl_v(self, reason):
        assert ti.notice_for_result(ti.InsertResult("none", reason)) == ti.NOTICE_COPIED

    def test_empty_needs_no_notice(self):
        assert ti.notice_for_result(ti.InsertResult("none", "empty")) is None

    def test_every_notice_has_a_message(self):
        for notice in (ti.NOTICE_COPIED, ti.NOTICE_LOCKED, ti.NOTICE_PASSWORD):
            assert ti.NOTICE_MESSAGES[notice]
        assert "Ctrl+V" in ti.NOTICE_MESSAGES[ti.NOTICE_COPIED]


class TestCapabilityGate:
    def test_parse_capabilities(self):
        assert ti.capabilities_allow_insert({"insert_text": True}) is True
        assert ti.capabilities_allow_insert({"insert_text": False}) is False
        assert ti.capabilities_allow_insert({}) is False
        assert ti.capabilities_allow_insert(None) is False


class _FakeConnection:
    """Records Gio.DBusConnection.call() and lets the test answer it."""

    def __init__(self):
        self.calls = []

    def call(self, bus_name, path, iface, method, params, reply_type, flags, timeout,
             cancellable, callback):
        self.calls.append({"bus_name": bus_name, "path": path, "iface": iface,
                           "method": method, "params": params, "callback": callback})

    def answer(self, index, value=None, error=None):
        call = self.calls[index]
        res = MagicMock()
        conn = MagicMock()
        if error is not None:
            conn.call_finish.side_effect = error
        else:
            conn.call_finish.return_value = value
        call["callback"](conn, res)


def _variant_reply(sig, value):
    from gi.repository import GLib
    return GLib.Variant(sig, value)


class TestInserterWithFakeConnection:
    def test_not_capable_until_probed(self):
        ins = ti.ExtensionTextInserter(connection=_FakeConnection())
        assert ins.can_insert is False

    def test_presence_probes_capabilities(self):
        from gi.repository import GLib

        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        ins.on_extension_presence_changed(True)
        assert conn.calls[0]["method"] == "GetCapabilities"
        assert conn.calls[0]["path"] == "/org/fede/whisperaloud/TextInput"
        assert conn.calls[0]["iface"] == "org.fede.whisperaloud.TextInput"
        conn.answer(0, _variant_reply("(a{sv})", ({"insert_text": GLib.Variant("b", True)},)))
        assert ins.can_insert is True

    def test_vanish_clears_capability(self):
        from gi.repository import GLib

        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        ins.on_extension_presence_changed(True)
        conn.answer(0, _variant_reply("(a{sv})", ({"insert_text": GLib.Variant("b", True)},)))
        ins.on_extension_presence_changed(False)
        assert ins.can_insert is False

    def test_stale_probe_reply_ignored(self):
        """A reply to a probe issued before the extension vanished must not
        resurrect the capability."""
        from gi.repository import GLib

        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        ins.on_extension_presence_changed(True)
        ins.on_extension_presence_changed(False)
        conn.answer(0, _variant_reply("(a{sv})", ({"insert_text": GLib.Variant("b", True)},)))
        assert ins.can_insert is False

    def test_probe_error_means_not_capable(self):
        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        ins.on_extension_presence_changed(True)
        assert ins.capabilities_known is False
        conn.answer(0, error=Exception("UnknownMethod"))
        assert ins.capabilities_known is True
        assert ins.can_insert is False

    def test_absent_extension_is_known(self):
        ins = ti.ExtensionTextInserter(connection=_FakeConnection())
        ins.on_extension_presence_changed(False)
        assert ins.capabilities_known is True
        assert ins.can_insert is False

    def test_insert_sends_text_and_window_id(self):
        from gi.repository import GLib

        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        results = []
        ins.insert("hola", window_id=42, on_done=results.append)
        call = conn.calls[0]
        assert call["method"] == "InsertText"
        text, options = call["params"].unpack()
        assert text == "hola"
        assert options == {"window_id": 42}
        assert call["params"].get_child_value(1).lookup_value(
            "window_id", None).get_type_string() == "u"
        conn.answer(0, _variant_reply("(a{sv})", ({"method": GLib.Variant("s", "commit"),
                                                    "reason": GLib.Variant("s", "")},)))
        assert results == [ti.InsertResult("commit", "")]

    def test_insert_without_window_id_sends_no_options(self):
        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        ins.insert("x", on_done=lambda r: None)
        assert conn.calls[0]["params"].unpack() == ("x", {})

    def test_insert_error_reports_call_failed(self):
        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        results = []
        ins.insert("secret words", on_done=results.append)
        conn.answer(0, error=Exception("AccessDenied"))
        assert results == [ti.InsertResult("none", ti.CALL_FAILED)]

    def test_insert_error_does_not_log_text(self, caplog):
        import logging

        conn = _FakeConnection()
        ins = ti.ExtensionTextInserter(connection=conn)
        with caplog.at_level(logging.DEBUG):
            ins.insert("secret words", on_done=lambda r: None)
            conn.answer(0, error=Exception("boom"))
        assert "secret" not in caplog.text

    def test_call_raising_synchronously_reports_call_failed(self):
        conn = MagicMock()
        conn.call.side_effect = RuntimeError("no bus")
        ins = ti.ExtensionTextInserter(connection=conn)
        results = []
        ins.insert("x", on_done=results.append)
        assert results == [ti.InsertResult("none", ti.CALL_FAILED)]
