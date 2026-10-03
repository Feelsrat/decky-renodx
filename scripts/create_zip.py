#!/usr/bin/env python3
"""Build decky-renodx.zip: one plugin folder with exactly what the plugin needs at runtime."""
import sys
import zipfile
from pathlib import Path
from stat import S_IFREG

PLUGIN_FOLDER = "decky-renodx"
OUTPUT_FILENAME = "decky-renodx.zip"
ROOT_FILES = ["plugin.json", "main.py", "package.json", "README.md", "LICENSE", "compatibility.json"]
FOLDERS = ["dist", "defaults", "backend"]
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".map"}
# Files the running plugin (or the self-updater of older versions) requires.
REQUIRED = ["dist/index.js", "main.py", "backend/service.py", "backend/cache.py", "defaults/assets/specialk-delayed-launch.sh"]
FIXED_DATE = (2020, 1, 1, 0, 0, 0)


def include(path: Path) -> bool:
    return not (set(path.parts) & EXCLUDED_PARTS) and path.suffix.lower() not in EXCLUDED_SUFFIXES


def add(archive: zipfile.ZipFile, source: Path, name: str) -> None:
    data = source.read_bytes()
    executable = source.suffix == ".sh"
    if executable:
        data = data.replace(b"\r\n", b"\n")
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.external_attr = (S_IFREG | (0o755 if executable else 0o644)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def create_plugin_zip(output_filename: str = OUTPUT_FILENAME) -> Path:
    root = Path(__file__).resolve().parents[1]
    for name in [*ROOT_FILES, *REQUIRED]:
        if not (root / name).exists():
            raise FileNotFoundError(f"{name} is missing (run the build first?)")
    zip_path = root / output_filename
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ROOT_FILES:
            add(archive, root / name, f"{PLUGIN_FOLDER}/{name}")
        for folder in FOLDERS:
            for path in sorted((root / folder).rglob("*")):
                if path.is_file() and include(path.relative_to(root)):
                    add(archive, path, f"{PLUGIN_FOLDER}/{path.relative_to(root).as_posix()}")
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if [name for name in names if name.endswith("plugin.json") and name.count("/") == 1] != [f"{PLUGIN_FOLDER}/plugin.json"]:
            raise ValueError("Decky zip must contain exactly one folder/plugin.json")
    print(f"Created {zip_path} ({len(names)} files)")
    return zip_path


if __name__ == "__main__":
    try:
        create_plugin_zip()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
