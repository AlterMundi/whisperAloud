"""D-Bus interface contract test for org.fede.whisperaloud.Control.

Guards against accidental drift between:

1. The pydbus introspection XML (the ``WhisperAloudService`` class docstring
   in ``src/whisper_aloud/service/daemon.py``) and a committed snapshot
   (``tests/contracts/org.fede.whisperaloud.Control.xml``).
2. The XML and the client modules that call/subscribe to it
   (``src/whisper_aloud/service/client.py``).
3. The XML and the Python daemon class itself (methods + ``signal()``
   descriptors).

Everything here is static (AST-based / source-text based): no D-Bus session
bus is started and the daemon module is never imported, so this test has no
GTK/audio/pydbus runtime dependency and cannot hang or flake on an
environment that lacks a session bus.

Regenerating the snapshot after an intentional, reviewed interface change:

    python -m tests.contracts.regen

or:

    UPDATE_DBUS_SNAPSHOT=1 python -m pytest tests/test_dbus_contract.py

See tests/contracts/README.md for the policy.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
from pathlib import Path

import pytest

from tests.contracts import regen

CLIENT_SOURCE_PATH = (
    regen.REPO_ROOT / "src" / "whisper_aloud" / "service" / "client.py"
)
HISTORY_CLIENT_SOURCE_PATH = (
    regen.REPO_ROOT / "src" / "whisper_aloud" / "service" / "history_client.py"
)

# ─── Explicit, hand-maintained map of what client.py calls/subscribes to ────
#
# WhisperAloudClient (service/client.py) is a thin wrapper: every public
# method either calls exactly one ``self._proxy.<Name>(...)`` D-Bus method,
# or subscribes to exactly one signal via ``self._subscribe_signal("<Name>",
# ...)``. history_client.py never touches the D-Bus proxy directly — it only
# calls WhisperAloudClient's own wrapper methods — so it has no separate
# entry here.
#
# If client.py starts calling/subscribing to something new, update this map
# in the same change (the self-check test below will fail loudly otherwise).
CLIENT_METHOD_CALLS = {
    # proxy method name -> number of positional args client.py passes
    "StartRecording": 0,
    "StopRecording": 0,
    "ToggleRecording": 0,
    "CancelRecording": 0,
    "GetStatus": 0,
    "GetHistory": 1,
    "SearchHistory": 2,
    "GetFavoriteHistory": 1,
    "ToggleHistoryFavorite": 1,
    "DeleteHistoryEntry": 1,
    "UpdateHistoryEntry": 2,
    "GetConfig": 0,
    "SetConfig": 1,
    "ReloadConfig": 0,
    "Quit": 0,
}

CLIENT_SIGNAL_SUBSCRIPTIONS = {
    "RecordingStarted",
    "RecordingStopped",
    "TranscriptionReady",
    "LevelUpdate",
    "PartialTranscript",
    "StatusChanged",
    "ConfigChanged",
    "Error",
}

# ─── Regex self-check: keeps the explicit lists above honest ───────────────
#
# A robust full static type-check of call sites isn't feasible here (pydbus
# proxies are dynamic attribute access resolved at runtime against whatever
# the daemon actually published), so per the ticket we keep this pragmatic:
# a regex scan over client.py's source text cross-checks the maintained
# lists above instead of trusting them blindly.
_PROXY_CALL_RE = re.compile(r"self\._proxy\.([A-Za-z_][A-Za-z0-9_]*)\(")
_SIGNAL_SUBSCRIBE_RE = re.compile(r'_subscribe_signal\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def _client_source() -> str:
    return CLIENT_SOURCE_PATH.read_text(encoding="utf-8")


def _history_client_source() -> str:
    return HISTORY_CLIENT_SOURCE_PATH.read_text(encoding="utf-8")


def _scanned_proxy_calls(source: str) -> set:
    return set(_PROXY_CALL_RE.findall(source))


def _scanned_signal_subscriptions(source: str) -> set:
    return set(_SIGNAL_SUBSCRIBE_RE.findall(source))


# ─── AST helpers for the daemon class (no import, no bus) ──────────────────


def _daemon_method_arg_counts(class_node: ast.ClassDef) -> dict:
    """Map method name -> positional arg count (excluding ``self``)."""
    counts = {}
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue  # private helpers aren't part of the D-Bus surface
            positional = node.args.args
            # Drop "self".
            count = max(0, len(positional) - 1)
            counts[node.name] = count
    return counts


def _daemon_signal_descriptors(class_node: ast.ClassDef) -> set:
    """Names assigned to ``signal()`` directly in the class body."""
    names = set()
    for node in class_node.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        is_signal_call = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "signal"
        )
        if is_signal_call:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def daemon_source() -> str:
    return regen.DAEMON_SOURCE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def daemon_class_node(daemon_source) -> ast.ClassDef:
    return regen.daemon_class_node(daemon_source)


@pytest.fixture(scope="module")
def docstring_xml(daemon_source) -> str:
    return regen.extract_docstring_xml(daemon_source)


@pytest.fixture(scope="module")
def canonical_xml(docstring_xml) -> str:
    return regen.canonicalize_xml(docstring_xml)


@pytest.fixture(scope="module")
def interface(canonical_xml) -> dict:
    return regen.parse_interface(canonical_xml)


# ─── 1. Docstring XML parses and matches the committed snapshot ───────────


def test_docstring_xml_is_well_formed(docstring_xml):
    """The docstring must be parseable XML before anything else matters."""
    # regen.canonicalize_xml raises (via ET.fromstring) on malformed XML.
    regen.canonicalize_xml(docstring_xml)


def test_docstring_matches_snapshot(canonical_xml):
    """Fails on *any* interface drift (intentional or not) with a readable diff.

    Regenerate with `python -m tests.contracts.regen`, or re-run this test
    with `UPDATE_DBUS_SNAPSHOT=1` to update the snapshot in place.
    """
    if os.environ.get("UPDATE_DBUS_SNAPSHOT") == "1":
        regen.write_snapshot(canonical_xml)
        pytest.skip("UPDATE_DBUS_SNAPSHOT=1: snapshot regenerated, skipping comparison")

    if not regen.SNAPSHOT_PATH.exists():
        pytest.fail(
            f"Snapshot missing at {regen.SNAPSHOT_PATH}.\n"
            "Generate it with: python -m tests.contracts.regen"
        )

    expected = regen.read_snapshot()
    if expected != canonical_xml:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                canonical_xml.splitlines(),
                fromfile="tests/contracts/org.fede.whisperaloud.Control.xml (snapshot)",
                tofile="daemon.py docstring (current, canonicalized)",
                lineterm="",
            )
        )
        pytest.fail(
            "D-Bus interface contract changed for org.fede.whisperaloud.Control.\n\n"
            f"{diff}\n\n"
            "If this change is intentional, regenerate the snapshot in the same "
            "commit:\n"
            "    python -m tests.contracts.regen\n"
            "or:\n"
            "    UPDATE_DBUS_SNAPSHOT=1 python -m pytest tests/test_dbus_contract.py\n"
            "and note the interface change in the commit message "
            "(see tests/contracts/README.md)."
        )


# ─── 2. Every client call/subscription exists in the interface ────────────


def test_client_method_call_list_matches_source_scan():
    """Self-check: the maintained CLIENT_METHOD_CALLS map matches client.py."""
    scanned = _scanned_proxy_calls(_client_source())
    maintained = set(CLIENT_METHOD_CALLS)
    missing_from_list = scanned - maintained
    stale_in_list = maintained - scanned
    assert not missing_from_list, (
        f"client.py calls self._proxy.<method>() for {sorted(missing_from_list)} "
        "which is not in the maintained CLIENT_METHOD_CALLS map in "
        "tests/test_dbus_contract.py — update the map."
    )
    assert not stale_in_list, (
        f"CLIENT_METHOD_CALLS lists {sorted(stale_in_list)} but client.py no "
        "longer calls them — update the map in tests/test_dbus_contract.py."
    )


def test_client_signal_subscription_list_matches_source_scan():
    """Self-check: the maintained CLIENT_SIGNAL_SUBSCRIPTIONS set matches client.py."""
    scanned = _scanned_signal_subscriptions(_client_source())
    assert scanned == CLIENT_SIGNAL_SUBSCRIPTIONS, (
        f"Scanned signal subscriptions {sorted(scanned)} differ from the "
        f"maintained CLIENT_SIGNAL_SUBSCRIPTIONS {sorted(CLIENT_SIGNAL_SUBSCRIPTIONS)} "
        "in tests/test_dbus_contract.py — update the set."
    )


def test_history_client_never_touches_proxy_directly():
    """history_client.py should only call WhisperAloudClient's own wrappers.

    If this ever fails, history_client.py has started talking to the D-Bus
    proxy directly and needs its own entry in CLIENT_METHOD_CALLS /
    CLIENT_SIGNAL_SUBSCRIPTIONS.
    """
    source = _history_client_source()
    assert not _PROXY_CALL_RE.search(source)
    assert not _SIGNAL_SUBSCRIBE_RE.search(source)


def test_every_client_method_call_exists_in_interface(interface):
    methods = interface["methods"]
    for name, arg_count in CLIENT_METHOD_CALLS.items():
        assert name in methods, (
            f"client.py calls '{name}' which is not declared in the "
            "org.fede.whisperaloud.Control interface."
        )
        assert methods[name]["in"] == arg_count, (
            f"client.py calls '{name}' with {arg_count} arg(s), but the "
            f"interface declares {methods[name]['in']} 'in' arg(s)."
        )


def test_every_client_signal_subscription_exists_in_interface(interface):
    signals = interface["signals"]
    for name in CLIENT_SIGNAL_SUBSCRIPTIONS:
        assert name in signals, (
            f"client.py subscribes to signal '{name}' which is not declared "
            "in the org.fede.whisperaloud.Control interface."
        )


# ─── 3. XML <-> Python daemon class agreement ──────────────────────────────


def test_every_xml_method_has_a_matching_python_method(interface, daemon_class_node):
    python_methods = _daemon_method_arg_counts(daemon_class_node)
    for name, spec in interface["methods"].items():
        assert name in python_methods, (
            f"Interface declares method '{name}' but WhisperAloudService has "
            "no matching Python method."
        )
        assert python_methods[name] == spec["in"], (
            f"Interface method '{name}' declares {spec['in']} 'in' arg(s) but "
            f"the Python method takes {python_methods[name]} positional "
            "arg(s) (excluding self)."
        )


def test_every_xml_signal_has_a_signal_descriptor(interface, daemon_class_node):
    python_signals = _daemon_signal_descriptors(daemon_class_node)
    for name in interface["signals"]:
        assert name in python_signals, (
            f"Interface declares signal '{name}' but WhisperAloudService has "
            "no matching `signal()` descriptor."
        )


def test_no_orphan_python_signal_descriptors(interface, daemon_class_node):
    """Every `signal()` descriptor on the class should be declared in the XML."""
    python_signals = _daemon_signal_descriptors(daemon_class_node)
    orphans = python_signals - set(interface["signals"])
    assert not orphans, (
        f"WhisperAloudService declares signal() descriptor(s) {sorted(orphans)} "
        "that are not in the org.fede.whisperaloud.Control introspection XML."
    )
