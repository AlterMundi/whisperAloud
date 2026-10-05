"""ExtensionTextInserter + ShellExtensionWatcher + backend selection against a
fake GNOME Shell extension implementing org.fede.whisperaloud.TextInput, on a
throwaway dbus-run-session bus (WHISP-119).

The real Gio call/unpack/error paths are exercised: capability probe on the
extension's appearance, commit/paste/none results, an old extension without
TextInput, the extension vanishing, and a refused call. Never touches the
session bus: run only via
    scripts/test_private_bus.sh -m dbus_private tests/test_text_insertion_private_bus.py
"""

import pytest

from tests._private_bus import private_bus_address
from whisper_aloud import shell_extension as se
from whisper_aloud.clipboard import text_insertion as ti
from whisper_aloud.clipboard.paste_selection import (
    CLIPBOARD,
    EXTENSION,
    KEYSTROKE,
    REMOTE_DESKTOP,
    select_paste_backend,
)

pytestmark = [
    pytest.mark.dbus_private,
    pytest.mark.skipif(private_bus_address() is None,
                       reason="no private bus: run via scripts/test_private_bus.sh"),
    pytest.mark.filterwarnings(
        "ignore:Gio.DBusConnection.register_object is deprecated:DeprecationWarning"),
]

TEXT_INPUT_XML = """
<node>
  <interface name="org.fede.whisperaloud.TextInput">
    <method name="InsertText">
      <arg direction="in" type="s" name="text"/>
      <arg direction="in" type="a{sv}" name="options"/>
      <arg direction="out" type="a{sv}" name="result"/>
    </method>
    <method name="GetCapabilities">
      <arg direction="out" type="a{sv}" name="capabilities"/>
    </method>
  </interface>
</node>
"""

INFO_XML = """
<node>
  <interface name="org.fede.whisperaloud.ShellExtension">
    <method name="GetInfo">
      <arg type="a{sv}" direction="out" name="info"/>
    </method>
  </interface>
</node>
"""


@pytest.fixture
def env():
    from gi.repository import Gio, GLib

    addr = private_bus_address()
    flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
    client = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    service = Gio.DBusConnection.new_for_address_sync(addr, flags, None, None)
    ctx = GLib.MainContext.default()

    def spin(pred, timeout=5.0):
        deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
        while not pred() and GLib.get_monotonic_time() < deadline:
            ctx.iteration(False)
        return pred()

    state = {
        "calls": [],
        "result": {"method": "commit", "reason": ""},
        "error": None,
        "regs": [],
    }

    def text_input_method(_conn, sender, _path, _iface, method, params, invocation):
        if method == "GetCapabilities":
            invocation.return_value(GLib.Variant("(a{sv})", ({
                "insert_text": GLib.Variant("b", True),
                "methods": GLib.Variant("as", ["commit", "paste"]),
                "max_commit_bytes": GLib.Variant("u", 4000),
                "version": GLib.Variant("u", 5),
            },)))
            return
        text, options = params.unpack()
        window_v = params.get_child_value(1).lookup_value("window_id", None)
        state["calls"].append({
            "text": text, "options": options, "sender": sender,
            "window_id_type": window_v.get_type_string() if window_v else None,
        })
        if state["error"]:
            invocation.return_dbus_error(state["error"], "refused by fake extension")
            return
        invocation.return_value(GLib.Variant("(a{sv})", ({
            k: GLib.Variant("s", v) for k, v in state["result"].items()},)))

    def info_method(_c, _s, _p, _i, _m, _params, invocation):
        invocation.return_value(GLib.Variant("(a{sv})", ({
            "uuid": GLib.Variant("s", se.EXTENSION_UUID),
            "version": GLib.Variant("u", 4)},)))

    def start_extension(text_input=True):
        if text_input:
            iface = Gio.DBusNodeInfo.new_for_xml(TEXT_INPUT_XML).interfaces[0]
            state["regs"].append(
                service.register_object(se.TEXT_INPUT_PATH, iface, text_input_method))
        iface = Gio.DBusNodeInfo.new_for_xml(INFO_XML).interfaces[0]
        state["regs"].append(service.register_object(se.EXTENSION_INFO_PATH, iface, info_method))
        reply = service.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "RequestName", GLib.Variant("(su)", (se.EXTENSION_BUS_NAME, 4)), None,
            Gio.DBusCallFlags.NONE, 2000, None)
        assert reply.unpack()[0] == 1

    def stop_extension():
        service.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "ReleaseName", GLib.Variant("(s)", (se.EXTENSION_BUS_NAME,)), None,
            Gio.DBusCallFlags.NONE, 2000, None)

    from whisper_aloud.service.shell_extension_watch import ShellExtensionWatcher

    inserter = ti.ExtensionTextInserter(connection=client)
    presence = []

    def on_change(present):
        presence.append(present)
        inserter.on_extension_presence_changed(present)

    watcher = ShellExtensionWatcher(on_change, connection=client)

    yield {
        "client": client, "spin": spin, "state": state, "inserter": inserter,
        "presence": presence, "start": start_extension, "stop": stop_extension,
    }

    watcher.close()
    for r in state["regs"]:
        service.unregister_object(r)
    client.close_sync(None)
    service.close_sync(None)


def _insert(env, text="hola mundo", **kw):
    out = []
    env["inserter"].insert(text, on_done=out.append, **kw)
    assert env["spin"](lambda: out)
    return out[0]


def _auto(env, sandboxed=True):
    return select_paste_backend(
        "auto", is_sandboxed=sandboxed, extension_can_insert=env["inserter"].can_insert)


def test_without_extension_auto_is_clipboard_in_flatpak_and_keystroke_native(env):
    assert env["spin"](lambda: env["presence"] == [False])
    assert env["inserter"].can_insert is False
    assert _auto(env, sandboxed=True) == CLIPBOARD
    assert _auto(env, sandboxed=False) == KEYSTROKE


def test_extension_appearing_is_probed_and_selected(env):
    env["start"]()
    assert env["spin"](lambda: env["inserter"].can_insert)
    assert _auto(env, sandboxed=True) == EXTENSION
    assert _auto(env, sandboxed=False) == EXTENSION
    # Remote desktop is only ever the explicit opt-in.
    assert select_paste_backend("remote-desktop", is_sandboxed=True,
                                extension_can_insert=True) == REMOTE_DESKTOP


def test_commit_result_and_arguments(env):
    env["start"]()
    assert env["spin"](lambda: env["inserter"].can_insert)
    result = _insert(env, "hola mundo", window_id=77)
    assert result == ti.InsertResult("commit", "")
    assert ti.notice_for_result(result) is None
    call = env["state"]["calls"][0]
    assert call["text"] == "hola mundo"
    assert call["options"] == {"window_id": 77}
    assert call["window_id_type"] == "u"
    # The caller is the daemon's own connection (what the extension checks).
    assert call["sender"] == env["client"].get_unique_name()


def test_paste_result(env):
    env["start"]()
    env["state"]["result"] = {"method": "paste", "reason": ""}
    result = _insert(env)
    assert result.inserted and result.method == "paste"


@pytest.mark.parametrize("reason,notice", [
    ("window-mismatch", ti.NOTICE_FOCUS_CHANGED),
    ("locked", ti.NOTICE_LOCKED),
    ("password-field", ti.NOTICE_PASSWORD),
    ("modal", ti.NOTICE_COPIED),
])
def test_none_results_map_to_notices(env, reason, notice):
    env["start"]()
    env["state"]["result"] = {"method": "none", "reason": reason}
    result = _insert(env)
    assert result == ti.InsertResult("none", reason)
    assert ti.notice_for_result(result) == notice


def test_refused_call_reports_call_failed(env):
    env["start"]()
    env["state"]["error"] = "org.freedesktop.DBus.Error.AccessDenied"
    result = _insert(env)
    assert result == ti.InsertResult("none", ti.CALL_FAILED)
    assert ti.notice_for_result(result) == ti.NOTICE_COPIED


def test_old_extension_without_text_input_is_not_selected(env):
    env["start"](text_input=False)
    assert env["spin"](lambda: True in env["presence"])
    # Wait for the probe's (error) reply itself, then check the outcome.
    assert env["spin"](lambda: env["inserter"].capabilities_known)
    assert env["inserter"].can_insert is False
    assert env["inserter"].capabilities is None
    assert _auto(env, sandboxed=True) == CLIPBOARD


def test_extension_vanishing_drops_the_capability(env):
    env["start"]()
    assert env["spin"](lambda: env["inserter"].can_insert)
    env["stop"]()
    assert env["spin"](lambda: not env["inserter"].can_insert)
    assert _auto(env, sandboxed=True) == CLIPBOARD


def test_insert_with_extension_gone_reports_call_failed(env):
    result = _insert(env)
    assert result == ti.InsertResult("none", ti.CALL_FAILED)
