#!/usr/bin/env python3
import sys
import zipfile
from stat import S_IFREG
from pathlib import Path


PLUGIN_FOLDER = "decky-renodx"
OUTPUT_FILENAME = "decky-renodx.zip"


def write_plugin_file(zipf: zipfile.ZipFile, source: Path, archive_name: str) -> None:
    if source.suffix == ".sh":
        data = source.read_bytes().replace(b"\r\n", b"\n")
        info = zipfile.ZipInfo(archive_name)
        info.external_attr = (S_IFREG | 0o755) << 16
        zipf.writestr(info, data)
        return

    zipf.write(source, archive_name)


def create_plugin_zip(output_filename: str = OUTPUT_FILENAME) -> str:
    root_dir = Path(__file__).resolve().parents[1]
    zip_path = root_dir / output_filename
    root_files = ["plugin.json", "main.py", "package.json", "README.md", "LICENSE"]
    folders = ["dist", "defaults", "backend"]

    if zip_path.exists():
        zip_path.unlink()

    for filename in root_files:
        if not (root_dir / filename).exists():
            raise FileNotFoundError(f"{filename} not found")

    for folder in folders:
        if not (root_dir / folder).exists():
            raise FileNotFoundError(f"{folder} folder not found")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filename in root_files:
            source = root_dir / filename
            write_plugin_file(zipf, source, f"{PLUGIN_FOLDER}/{filename}")

        for folder in folders:
            for file_path in (root_dir / folder).rglob("*"):
                if file_path.is_file():
                    relative_path = file_path.relative_to(root_dir).as_posix()
                    write_plugin_file(zipf, file_path, f"{PLUGIN_FOLDER}/{relative_path}")

    with zipfile.ZipFile(zip_path) as zipf:
        plugin_json_files = [
            name for name in zipf.namelist()
            if name.endswith("/plugin.json") and name.count("/") == 1
        ]
        if len(plugin_json_files) != 1:
            raise ValueError("Decky zip must contain exactly one folder/plugin.json")

    print(f"Created {zip_path}")
    return str(zip_path)


if __name__ == "__main__":
    try:
        create_plugin_zip()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
