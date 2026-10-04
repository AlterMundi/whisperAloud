#!/usr/bin/env python3
"""Pack the GNOME Shell extension into an installable zip.

Usage: scripts/pack_shell_extension.py OUT_ZIP [--src DIR]

Produces the same layout as `gnome-extensions pack` (metadata.json at the
root, the JS modules, stylesheet.css, schemas/) without needing GNOME Shell
installed, so it also runs inside the Flatpak SDK. If glib-compile-schemas is
available, schemas/gschemas.compiled is included too (GNOME 44+ compiles
schemas on `gnome-extensions install` anyway; older tools expect it).

The output is reproducible: sorted entries and a fixed timestamp.

Install the result on the host with:
    gnome-extensions install --force OUT_ZIP
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "contrib" / "gnome-shell-extension" / "whisperaloud@fede"
FIXED_DATE = (2020, 1, 1, 0, 0, 0)
TOP_LEVEL = ("metadata.json", "*.js", "stylesheet.css")


def _collect(src: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for pattern in TOP_LEVEL:
        for p in src.glob(pattern):
            files[p.name] = p
    for p in (src / "schemas").glob("*.gschema.xml"):
        files[f"schemas/{p.name}"] = p
    if "metadata.json" not in files or "extension.js" not in files:
        raise SystemExit(f"{src}: not an extension directory")
    return files


def _compile_schemas(src: Path, workdir: Path) -> Path | None:
    compiler = shutil.which("glib-compile-schemas")
    if not compiler or not list((src / "schemas").glob("*.gschema.xml")):
        return None
    target = workdir / "schemas"
    target.mkdir()
    for p in (src / "schemas").glob("*.gschema.xml"):
        shutil.copy(p, target / p.name)
    subprocess.run([compiler, "--strict", str(target)], check=True)
    compiled = target / "gschemas.compiled"
    return compiled if compiled.exists() else None


def pack(src: Path, out: Path) -> Path:
    files = _collect(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        compiled = _compile_schemas(src, Path(tmp))
        if compiled is not None:
            files["schemas/gschemas.compiled"] = compiled
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, files[name].read_bytes())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="output .zip path")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="extension directory")
    args = parser.parse_args(argv)
    print(pack(args.src, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
