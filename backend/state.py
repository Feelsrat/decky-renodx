"""Install records, per-game settings, and cleanup of installs made by older plugin versions."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from . import fsutil
from .detect import walk
from .config import LEGACY_MARKER, Paths

RECORD_SCHEMA = 2
PROXY_DLLS = {"dxgi", "d3d9", "d3d8", "d3d11", "d3d12", "ddraw", "dinput8", "opengl32"}


class InstallStore:
    """One JSON record per game describing exactly what an install changed."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, appid: str) -> Path:
        if not re.fullmatch(r"\d+", str(appid)):
            raise ValueError(f"Invalid appid: {appid}")
        return self.directory / f"{appid}.json"

    def get(self, appid: str) -> dict[str, Any] | None:
        record = fsutil.read_json(self._path(appid))
        return record if isinstance(record, dict) and record.get("schema") == RECORD_SCHEMA else None

    def put(self, appid: str, record: dict[str, Any]) -> None:
        fsutil.write_json(self._path(appid), {**record, "schema": RECORD_SCHEMA})

    def delete(self, appid: str) -> None:
        path = self._path(appid)
        if path.exists():
            path.unlink()

    # An in-progress install's journal (see service._install_plan). If one exists
    # when the plugin starts, the install was interrupted and gets undone.
    def _pending_path(self, appid: str) -> Path:
        return self._path(appid).with_suffix(".pending.json")

    def get_pending(self, appid: str) -> dict[str, Any] | None:
        data = fsutil.read_json(self._pending_path(appid))
        return data if isinstance(data, dict) else None

    def put_pending(self, appid: str, data: dict[str, Any]) -> None:
        fsutil.write_json(self._pending_path(appid), data)

    def delete_pending(self, appid: str) -> None:
        self._pending_path(appid).unlink(missing_ok=True)

    def pending_appids(self) -> list[str]:
        return sorted(path.name.split(".", 1)[0] for path in self.directory.glob("*.pending.json"))


class Settings:
    """Small preferences: per game (executable override, Special K verified) and plugin-wide."""

    def __init__(self, path: Path):
        self.path = Path(path)
        data = fsutil.read_json(self.path, {})
        self.data: dict[str, Any] = data if isinstance(data, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        """A plugin-wide preference."""
        return self.data.get("plugin", {}).get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data.setdefault("plugin", {})[key] = value
        fsutil.write_json(self.path, self.data)

    def game(self, appid: str) -> dict[str, Any]:
        value = self.data.get("games", {}).get(str(appid), {})
        return value if isinstance(value, dict) else {}

    def set_game(self, appid: str, key: str, value: Any) -> None:
        games = self.data.setdefault("games", {})
        entry = games.setdefault(str(appid), {})
        if value in (None, "", False):
            entry.pop(key, None)
        else:
            entry[key] = value
        if not entry:
            games.pop(str(appid), None)
        fsutil.write_json(self.path, self.data)


# ---------------------------------------------------------------- legacy (<= 0.0.x) installs

def find_legacy(paths: Paths, appid: str, install_path: Path | None) -> dict[str, Any] | None:
    manifest_path = paths.legacy_manifests / f"{appid}.json"
    manifest = fsutil.read_json(manifest_path) if manifest_path.exists() else None
    markers: list[Path] = []
    if install_path and install_path.is_dir():
        for dirpath, _depth, filenames in walk(install_path):
            if LEGACY_MARKER in filenames:
                markers.append(dirpath)
    if not manifest and not markers:
        return None
    method = ""
    for directory in markers:
        method = str((fsutil.read_json(directory / LEGACY_MARKER, {}) or {}).get("method", "")) or method
    if not method and isinstance(manifest, dict):
        method = str(manifest.get("method", ""))
    return {"manifest_path": str(manifest_path) if manifest else "", "manifest": manifest or {}, "markers": [str(m) for m in markers], "method": method}


def _is_tool_dll(path: Path, needles: tuple[bytes, ...]) -> bool:
    """Only delete a proxy DLL if it really is ReShade / Special K, never the game's own."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(16 * 1024 * 1024)
    except OSError:
        return False
    return any(needle in data for needle in needles)


def remove_legacy(legacy: dict[str, Any], install_path: Path, logger: logging.Logger) -> tuple[list[str], list[str]]:
    """Conservative cleanup of an old-style install. Never touches Shaders/Textures or game DLLs."""
    removed: list[str] = []
    errors: list[str] = []

    def remove(path: Path) -> None:
        if not (path.exists() or path.is_symlink()):
            return
        try:
            fsutil.remove_path(path)
            removed.append(str(path))
            logger.info("Removed legacy file %s", path)
        except OSError as error:
            errors.append(f"{path}: {error}")

    candidates: set[Path] = set()
    for directory in map(Path, legacy.get("markers", [])):
        marker = fsutil.read_json(directory / LEGACY_MARKER, {}) or {}
        for name in ("ReShade.ini", "ReShadePreset.ini", "ReShade_README.txt", "SpecialK.ini", LEGACY_MARKER, "zzz_display_commander.addon64"):
            candidates.add(directory / name)
        candidates.add(directory / "ReShade_shaders")
        for pattern in ("AutoHDR*.addon*", "renodx*.addon*"):
            candidates.update(directory.glob(pattern))
        dll = Path(str(marker.get("dll", ""))).stem.lower()
        if dll in PROXY_DLLS:
            candidates.add(directory / f"{dll}.dll")
            if "specialk" in str(marker.get("method", "")):
                candidates.add(directory / f"{dll}.ini")
        for proxy in PROXY_DLLS:
            candidates.add(directory / f"{proxy}.dll")

    manifest = legacy.get("manifest") or {}
    for item in manifest.get("installed_files", []) if isinstance(manifest, dict) else []:
        path = Path(str(item))
        if path.name.lower() == "d3dcompiler_47.dll":
            continue  # old versions overwrote it without a backup; keeping it is safest
        if fsutil.is_within(path, install_path) or "My Mods/SpecialK" in str(path):
            candidates.add(path)

    for path in sorted(candidates, key=str):
        name = path.name.lower()
        if path.is_dir() and name not in {"reshade_shaders", "specialk"}:
            continue
        if name.endswith(".dll") and Path(name).stem in PROXY_DLLS:
            if not _is_tool_dll(path, (b"ReShade", b"reshade.me", b"SpecialK", b"Special K")):
                continue
        remove(path)

    overrides = manifest.get("wine_dll_overrides", {}) if isinstance(manifest, dict) else {}
    for reg in manifest.get("modified_files", []) if isinstance(manifest, dict) else []:
        reg_path = Path(str(reg))
        if reg_path.name == "user.reg" and reg_path.exists() and overrides:
            try:
                text = reg_path.read_text(encoding="utf-8", errors="replace")
                for dll in overrides:
                    text = re.sub(rf'(?m)^"{re.escape(str(dll))}"="native,builtin"\n?', "", text)
                fsutil.atomic_write_text(reg_path, text)
                removed.append(f"{reg_path} ({', '.join(overrides)} override)")
            except OSError as error:
                errors.append(f"{reg_path}: {error}")

    if legacy.get("manifest_path") and not errors:
        remove(Path(legacy["manifest_path"]))
    return removed, errors
