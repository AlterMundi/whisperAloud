"""Packaging of the GNOME Shell extension: the zip and the Flatpak manifest.

- scripts/pack_shell_extension.py produces an installable zip
  (metadata.json at the root, every module extension.js imports, schemas).
- The Flatpak manifest builds that zip to the path the app looks for and
  grants the daemon exactly the extension's bus name -- and nothing broader
  such as org.gnome.Shell.Extensions (which can disable/uninstall any
  extension).
"""

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from whisper_aloud import shell_extension as se

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = REPO_ROOT / "contrib" / "gnome-shell-extension" / se.EXTENSION_UUID
PACK = REPO_ROOT / "scripts" / "pack_shell_extension.py"
MANIFEST = REPO_ROOT / "flatpak" / "org.fede.whisperaloud.yml"


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack") / "sub" / se.ZIP_NAME
    proc = subprocess.run([sys.executable, str(PACK), str(out)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out


def test_zip_layout(packed):
    with zipfile.ZipFile(packed) as zf:
        names = set(zf.namelist())
        metadata = json.loads(zf.read("metadata.json"))
    assert {"metadata.json", "extension.js", "status.js", "stylesheet.css"} <= names
    assert f"schemas/{metadata['settings-schema']}.gschema.xml" in names
    assert metadata["uuid"] == se.EXTENSION_UUID
    assert not any(n.startswith("/") or ".." in n for n in names)


def test_zip_contains_every_local_import(packed):
    js = (EXT_DIR / "extension.js").read_text()
    local = set(re.findall(r"from '\./([^']+)'", js))
    assert local, "extension.js should import status.js"
    with zipfile.ZipFile(packed) as zf:
        assert local <= set(zf.namelist())


def test_zip_version_matches_source(packed):
    assert se.read_zip_version(packed) == se.read_extension_dir_version(EXT_DIR)


def test_pack_is_reproducible(packed, tmp_path):
    again = tmp_path / se.ZIP_NAME
    subprocess.run([sys.executable, str(PACK), str(again)], check=True, timeout=60,
                   capture_output=True)
    assert again.read_bytes() == packed.read_bytes()


def _manifest():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(MANIFEST.read_text())


def test_manifest_talk_name_for_extension_only():
    finish_args = _manifest()["finish-args"]
    assert f"--talk-name={se.EXTENSION_BUS_NAME}" in finish_args
    talk = [a for a in finish_args if a.startswith("--talk-name=org.gnome.Shell")]
    assert talk == [f"--talk-name={se.EXTENSION_BUS_NAME}"]
    assert "--socket=session-bus" not in finish_args


def test_manifest_builds_zip_to_flatpak_path():
    module = next(m for m in _manifest()["modules"]
                  if isinstance(m, dict) and m.get("name") == "whisper-aloud")
    cmds = " ".join(module["build-commands"])
    assert "scripts/pack_shell_extension.py" in cmds
    expected = str(se.FLATPAK_ZIP_PATH).replace("/app", "${FLATPAK_DEST}", 1)
    assert expected in cmds
