"""Steam library discovery: Steam games, non-Steam shortcuts, compatdata and console logs."""
from __future__ import annotations

import binascii
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import vdf

EXCLUDED_NAME_PREFIXES = ("Proton", "Steam Linux Runtime", "Steamworks Common Redistributables", "SteamVR")
CONSOLE_LOG_TAIL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class SteamApp:
    """A Steam game or a non-Steam shortcut ("kind" tells which)."""

    appid: str
    name: str
    install_path: Path
    compatdata: Path
    kind: str = "steam"
    exe: str = ""           # shortcuts: the .exe the shortcut launches
    buildid: str = ""       # Steam games: changes when Steam updates the game
    launch_options: str = ""  # shortcuts: as last saved in shortcuts.vdf

    @property
    def is_shortcut(self) -> bool:
        return self.kind == "shortcut"

    def to_dict(self) -> dict[str, str]:
        return {"appid": self.appid, "name": self.name, "kind": self.kind}


def valid_appid(appid: object) -> bool:
    return bool(re.fullmatch(r"\d{1,10}", str(appid or "")))


class SteamLibrary:
    def __init__(self, home: Path):
        self.home = Path(home)

    def roots(self) -> list[Path]:
        candidates = [
            self.home / ".local" / "share" / "Steam",
            self.home / ".steam" / "steam",
            self.home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        ]
        return _unique_existing(candidates)

    def libraries(self) -> list[Path]:
        libraries: list[Path] = []
        for root in self.roots():
            libraries.append(root)
            library_file = root / "steamapps" / "libraryfolders.vdf"
            if not library_file.exists():
                continue
            try:
                data = vdf.load(library_file)
            except OSError:
                continue
            folders = vdf.get(data, "libraryfolders", default={}) or {}
            for entry in folders.values():
                if isinstance(entry, dict) and entry.get("path"):
                    libraries.append(Path(entry["path"]))
        return _unique_existing(libraries)

    def apps(self) -> list[SteamApp]:
        apps: dict[str, SteamApp] = {}
        for library in self.libraries():
            steamapps = library / "steamapps"
            try:
                manifests = sorted(steamapps.glob("appmanifest_*.acf"))
            except OSError:
                continue
            for manifest in manifests:
                app = _read_manifest(manifest, library)
                if app and app.appid not in apps:
                    apps[app.appid] = app
        return sorted(apps.values(), key=lambda app: app.name.lower())

    def shortcuts(self) -> list[SteamApp]:
        """Non-Steam games added to Steam, from every local user's shortcuts.vdf."""
        roots = self.roots()
        if not roots:
            return []
        found: dict[str, SteamApp] = {}
        for root in roots:
            for path in sorted(root.glob("userdata/*/config/shortcuts.vdf")):
                try:
                    data = vdf.load_binary(path)
                except (OSError, ValueError):
                    continue
                entries = vdf.get(data, "shortcuts", default={}) or {}
                for entry in entries.values():
                    app = _shortcut_app(entry, roots[0]) if isinstance(entry, dict) else None
                    if app and app.appid not in found:
                        found[app.appid] = app
        return sorted(found.values(), key=lambda app: app.name.lower())

    def games(self) -> list[SteamApp]:
        steam = [app for app in self.apps() if not app.name.startswith(EXCLUDED_NAME_PREFIXES)]
        return sorted(steam + self.shortcuts(), key=lambda app: app.name.lower())

    def app(self, appid: str) -> SteamApp | None:
        appid = str(appid)
        for library in self.libraries():
            manifest = library / "steamapps" / f"appmanifest_{appid}.acf"
            if manifest.exists():
                return _read_manifest(manifest, library)
        return next((app for app in self.shortcuts() if app.appid == appid), None)

    def launched_executable(self, appid: str, install_path: Path) -> Path | None:
        """Most recent .exe Steam launched for ``appid``, from the console log tail."""
        pattern = re.compile(rf"\bAppId={re.escape(str(appid))}\b")
        exe_re = re.compile(r"(/[^'\"\n]*?\.exe)\b", re.I)
        found: Path | None = None
        for root in self.roots():
            log_file = root / "logs" / "console-linux.txt"
            if not log_file.exists():
                continue
            for line in _tail_lines(log_file, CONSOLE_LOG_TAIL_BYTES):
                if not pattern.search(line):
                    continue
                for match in exe_re.finditer(line):
                    candidate = Path(match.group(1))
                    if _is_within(candidate, install_path) and candidate.is_file():
                        found = candidate
        return found


def running_appids() -> set[str]:
    """AppIDs that currently have a Steam reaper process (i.e. are running)."""
    running: set[str] = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return running
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                args = handle.read().split(b"\0")
        except OSError:
            continue
        if not args or not args[0].endswith(b"reaper"):
            continue
        for arg in args:
            if arg.startswith(b"AppId="):
                running.add(arg[6:].decode("ascii", "ignore"))
    return running


def _read_manifest(path: Path, library: Path) -> SteamApp | None:
    try:
        data = vdf.load(path)
    except OSError:
        return None
    state = vdf.get(data, "AppState", default={}) or {}
    appid, name, installdir = str(state.get("appid", "")), str(state.get("name", "")), str(state.get("installdir", ""))
    if not (valid_appid(appid) and name and installdir) or "/" in installdir or installdir in {".", ".."}:
        return None
    return SteamApp(
        appid=appid, name=name,
        install_path=library / "steamapps" / "common" / installdir,
        # Steam keeps the Proton prefix in the same library as the game.
        compatdata=library / "steamapps" / "compatdata" / appid,
        buildid=str(state.get("buildid", "")),
    )


def _strip_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _shortcut_app(entry: dict, steam_root: Path) -> SteamApp | None:
    name = str(vdf.get(entry, "AppName", default="") or "").strip()
    raw_exe = str(vdf.get(entry, "Exe", default="") or "")
    if not name:
        return None
    raw_id = vdf.get(entry, "appid")
    if not isinstance(raw_id, int):
        # Old shortcuts have no stored id; Steam derives it the same way.
        raw_id = binascii.crc32((raw_exe + name).encode("utf-8")) | 0x80000000
    appid = str(raw_id & 0xFFFFFFFF)
    exe = _strip_quotes(raw_exe)
    start_dir = _strip_quotes(str(vdf.get(entry, "StartDir", default="") or ""))
    install = Path(start_dir) if start_dir else Path(exe).parent if exe else None
    if exe and install is not None and not _is_within(Path(exe), install):
        install = Path(exe).parent
    if install is None or not install.is_absolute():
        return None
    return SteamApp(
        appid=appid, name=name, install_path=install,
        # Non-Steam games get their Proton prefix in the main Steam library.
        compatdata=steam_root / "steamapps" / "compatdata" / appid,
        kind="shortcut", exe=exe if exe.lower().endswith(".exe") else "",
        launch_options=str(vdf.get(entry, "LaunchOptions", default="") or ""),
    )


def _tail_lines(path: Path, max_bytes: int) -> list[str]:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


def _unique_existing(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            if not path.exists():
                continue
            key = str(path.resolve())
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False
