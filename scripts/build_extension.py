#!/usr/bin/env python3
"""
Build the Blender extension package without needing Blender:

    python scripts/build_extension.py        ->  dist/mat_baker-<version>.zip

The zip has blender_manifest.toml at its root, which is what Blender and
extensions.blender.org expect. Before uploading, validate it with Blender itself:

    blender --command extension validate dist/mat_baker-<version>.zip

(Blender can also build the package:  blender --command extension build --source-dir mat_baker --output-dir dist)
"""
import pathlib
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "mat_baker"


def main():
    manifest = tomllib.loads((SRC / "blender_manifest.toml").read_text(encoding="utf-8"))
    out = ROOT / "dist" / f"{manifest['id']}-{manifest['version']}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SRC.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            zf.write(path, path.relative_to(SRC).as_posix())
    print(out)


if __name__ == "__main__":
    main()
