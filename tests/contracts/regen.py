"""Extract, canonicalize, and (re)generate the D-Bus contract snapshot.

The daemon's D-Bus introspection XML lives as the docstring of
``WhisperAloudService`` in ``src/whisper_aloud/service/daemon.py`` (pydbus
reads it directly as the introspection data). This module extracts that XML
at the *source* level, via ``ast``, without importing the daemon module —
so no D-Bus/GTK/audio dependency is touched and no session bus is started.

Regenerate the committed snapshot (``org.fede.whisperaloud.Control.xml``)
after an intentional interface change with either:

    python -m tests.contracts.regen

or by running the contract test with the environment variable set:

    UPDATE_DBUS_SNAPSHOT=1 python -m pytest tests/test_dbus_contract.py

Per tests/contracts/README.md, any snapshot regeneration must land in the
same commit as the interface change, noted in the commit message.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_SOURCE_PATH = REPO_ROOT / "src" / "whisper_aloud" / "service" / "daemon.py"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "org.fede.whisperaloud.Control.xml"
DAEMON_CLASS_NAME = "WhisperAloudService"
INTERFACE_NAME = "org.fede.whisperaloud.Control"


class ContractExtractionError(RuntimeError):
    """Raised when the daemon source cannot yield the introspection XML."""


def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise ContractExtractionError(
        f"class {class_name!r} not found in {DAEMON_SOURCE_PATH}"
    )


def daemon_class_node(source_text: Optional[str] = None) -> ast.ClassDef:
    """Return the AST node for the daemon service class (source-level, no import)."""
    if source_text is not None:
        text = source_text
    else:
        text = DAEMON_SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(DAEMON_SOURCE_PATH))
    return _find_class(tree, DAEMON_CLASS_NAME)


def extract_docstring_xml(source_text: Optional[str] = None) -> str:
    """Extract the raw D-Bus introspection XML from the daemon class docstring."""
    class_node = daemon_class_node(source_text)
    docstring = ast.get_docstring(class_node, clean=True)
    if not docstring:
        raise ContractExtractionError(
            f"{DAEMON_CLASS_NAME} has no docstring to extract introspection XML from"
        )
    return docstring


def canonicalize_xml(xml_text: str) -> str:
    """Parse and deterministically re-serialize introspection XML.

    Declaration order of elements/attributes is preserved as written (methods
    and signals are NOT sorted) — only whitespace/indentation is normalized,
    so formatting-only edits to the docstring don't produce a snapshot diff.
    """
    root = ET.fromstring(xml_text)
    ET.indent(root, space="  ")
    canonical = ET.tostring(root, encoding="unicode")
    return canonical.rstrip("\n") + "\n"


def current_canonical_xml() -> str:
    """Canonical XML extracted from the daemon source right now."""
    return canonicalize_xml(extract_docstring_xml())


def parse_interface(xml_text: str, interface_name: str = INTERFACE_NAME) -> dict:
    """Parse canonical/raw introspection XML into a plain dict for assertions.

    Returns:
        {
          "methods": {name: {"in": <count>, "out": <count>}},
          "signals": {name: {"args": <count>}},
        }
    """
    root = ET.fromstring(xml_text)
    iface = None
    for node in root.findall("interface"):
        if node.get("name") == interface_name:
            iface = node
            break
    if iface is None:
        raise ContractExtractionError(f"interface {interface_name!r} not found")

    methods = {}
    for method in iface.findall("method"):
        in_args = [a for a in method.findall("arg") if a.get("direction", "in") == "in"]
        out_args = [a for a in method.findall("arg") if a.get("direction") == "out"]
        methods[method.get("name")] = {"in": len(in_args), "out": len(out_args)}

    signals = {}
    for sig in iface.findall("signal"):
        signals[sig.get("name")] = {"args": len(sig.findall("arg"))}

    return {"methods": methods, "signals": signals}


def read_snapshot() -> str:
    return SNAPSHOT_PATH.read_text(encoding="utf-8")


def write_snapshot(xml_text: Optional[str] = None) -> str:
    canonical = xml_text if xml_text is not None else current_canonical_xml()
    SNAPSHOT_PATH.write_text(canonical, encoding="utf-8")
    return canonical


def main() -> None:
    canonical = write_snapshot()
    print(f"Wrote {SNAPSHOT_PATH} ({len(canonical.splitlines())} lines)")


if __name__ == "__main__":
    main()
