"""Game executable, graphics API, engine and anti-cheat detection.

One scorer decides the executable; every installer receives the folder that
executable lives in, so ReShade, the RenoDX addon and the status check can
never disagree about where the game is.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .pe import read_pe

MAX_WALK_DEPTH = 6
MAX_WALK_ENTRIES = 40000

SKIP_DIRS = {
    "_commonredist", "commonredist", "redist", "redistributables", "directx", "vcredist", "dotnet",
    "__installer", "installer", "support", "easyanticheat", "battleye", "thirdparty", "prereqs",
    "prerequisites", "crashreporter", "shadercache", ".decky-renodx",
}
# Executable name tokens that are never the game itself.
SKIP_TOKENS = {
    "unins", "uninstall", "uninstaller", "setup", "install", "installer", "redist", "vcredist", "dxsetup", "dxwebsetup",
    "crashreporter", "crashreportclient", "crashhandler", "crashpad", "bugreporter", "reporter", "easyanticheat",
    "beservice", "eac", "updater", "update", "touchup", "cefprocess", "webhelper", "unitycrashhandler64",
    "unitycrashhandler32", "ue4prereqsetup", "prereqsetup", "dotnetfx", "vc", "oalinst", "physx", "quicksfv",
}
PENALTY_TOKENS = {"launcher", "config", "configtool", "settings", "editor", "server", "benchmark", "tool", "tools"}

ANTI_CHEAT_FILES = {
    "EasyAntiCheat": {"easyanticheat.exe", "easyanticheat_eos.exe", "easyanticheat.sys", "easyanticheat_eos_setup.exe", "easyanticheat_setup.exe"},
    "BattlEye": {"beservice.exe", "beservice_x64.exe", "bedaisy.sys", "beclient.dll", "beclient_x64.dll"},
    "Vanguard": {"vgk.sys", "vgc.exe"},
    "GameGuard": {"gameguard.des", "npggsvc.exe"},
    "XIGNCODE3": {"x3.xem", "xhunter1.sys"},
    "Denuvo Anti-Cheat": {"denuvo-anti-cheat.sys"},
    "ACE": {"ace-base.sys", "ace-game.sys"},
    "nProtect": {"npgamemon.des"},
}
ANTI_CHEAT_DIRS = {"easyanticheat": "EasyAntiCheat", "battleye": "BattlEye"}

API_PRIORITY = ["d3d12", "d3d11", "d3d10_1", "d3d10", "dxgi", "d3d9", "d3d8", "ddraw", "opengl32", "vulkan-1"]
API_FROM_IMPORT = {
    "d3d12": "d3d12", "d3d11": "d3d11", "d3d10_1": "d3d10", "d3d10": "d3d10", "dxgi": "dxgi",
    "d3d9": "d3d9", "d3d8": "d3d8", "ddraw": "ddraw", "opengl32": "opengl", "vulkan-1": "vulkan",
}
# Which proxy DLL ReShade / Special K should be installed as for each API.
HOOK_FOR_API = {
    "d3d12": "dxgi", "d3d11": "dxgi", "d3d10": "dxgi", "dxgi": "dxgi", "dx11_dx12": "dxgi",
    "d3d9": "d3d9", "d3d8": "d3d8", "ddraw": "ddraw", "opengl": "opengl32", "vulkan": "",
}
PROXY_DLLS = {"dxgi.dll", "d3d9.dll", "d3d8.dll", "d3d11.dll", "d3d12.dll", "ddraw.dll", "opengl32.dll", "dinput8.dll"}


@dataclass
class ExeCandidate:
    path: str
    score: float
    arch: str


@dataclass
class GameScan:
    install_path: str
    exe_path: str = ""
    candidates: list[ExeCandidate] = field(default_factory=list)
    architecture: str = "unknown"
    api: str = "unknown"
    hook: str = ""
    engine: str = "unknown"
    api_confidence: str = "none"
    api_source: str = ""
    notes: list[str] = field(default_factory=list)
    anti_cheat: list[str] = field(default_factory=list)
    linux_build: bool = False

    @property
    def target_dir(self) -> str:
        return str(Path(self.exe_path).parent) if self.exe_path else ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["target_dir"] = self.target_dir
        return data


def hook_for_api(api: str) -> str:
    return HOOK_FOR_API.get(api, "dxgi")


def _name_tokens(name: str) -> set[str]:
    stem = Path(name).stem.lower()
    tokens = set(re.split(r"[^a-z0-9]+", stem))
    tokens.add(re.sub(r"[^a-z0-9]+", "", stem))
    return {token for token in tokens if token}


def _title_words(text: str) -> set[str]:
    return {word for word in re.split(r"[^a-z0-9]+", text.lower()) if len(word) > 2}


def walk(root: Path, max_depth: int = MAX_WALK_DEPTH):
    """Bounded os.walk that yields (dirpath, depth, filenames) and skips redist folders."""
    root = Path(root)
    base_depth = len(root.parts)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        dirnames[:] = [name for name in dirnames if name.lower() not in SKIP_DIRS] if depth < max_depth else []
        count += len(filenames) + len(dirnames)
        yield Path(dirpath), depth, filenames
        if count > MAX_WALK_ENTRIES:
            return


def find_executables(install_path: Path, title: str, exclude: set[str] | None = None) -> list[ExeCandidate]:
    install_path = Path(install_path)
    exclude = exclude or set()
    title_words = _title_words(title) | _title_words(install_path.name)
    candidates: list[ExeCandidate] = []
    for dirpath, depth, filenames in walk(install_path):
        for filename in filenames:
            if not filename.lower().endswith(".exe"):
                continue
            path = dirpath / filename
            if str(path) in exclude:
                continue
            tokens = _name_tokens(filename)
            if tokens & SKIP_TOKENS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < 64 * 1024:
                continue
            info = read_pe(path)
            arch = info.arch if info else "unknown"
            score = math.log2(max(size, 1)) * 2.0  # bigger binaries are usually the game
            score += 12.0 * len(tokens & title_words)
            score -= 3.0 * depth
            lower_path = str(path).lower()
            if "shipping" in tokens or "-shipping" in filename.lower():
                score += 25
            if "binaries/win64" in lower_path.replace("\\", "/"):
                score += 10
            if tokens & PENALTY_TOKENS:
                score -= 20
            if arch == "64":
                score += 4
            if info and info.imports & {f"{name}.dll" for name in API_PRIORITY}:
                score += 15
            candidates.append(ExeCandidate(path=str(path), score=round(score, 2), arch=arch))
    candidates.sort(key=lambda item: (-item.score, item.path))
    return candidates


def prefer_shipping_exe(exe: Path, install_path: Path) -> Path:
    """Unreal games ship a small launcher stub in the root; hooks must go next to *-Shipping.exe."""
    exe = Path(exe)
    if "shipping" in exe.name.lower():
        return exe
    shipping: list[Path] = []
    for pattern in ("*/Binaries/Win64/*-Shipping.exe", "*/Binaries/WinGDK/*-Shipping.exe", "Binaries/Win64/*-Shipping.exe"):
        shipping.extend(path for path in exe.parent.glob(pattern) if path.is_file())
    if not shipping and exe.parent != install_path:
        for pattern in ("*/Binaries/Win64/*-Shipping.exe", "Binaries/Win64/*-Shipping.exe"):
            shipping.extend(path for path in install_path.glob(pattern) if path.is_file())
    if not shipping:
        return exe
    stem = exe.stem.lower()
    shipping.sort(key=lambda path: (not path.name.lower().startswith(stem), -path.stat().st_size))
    return shipping[0]


def detect_engine(exe_dir: Path, install_path: Path) -> str:
    current = Path(exe_dir)
    for _ in range(5):
        if (current / "UnityPlayer.dll").exists() or (current / "GameAssembly.dll").exists() or any(current.glob("*_Data/globalgamemanagers")):
            return "unity"
        if any(current.glob("*.uproject")) or (current / "Engine" / "Binaries").is_dir() or (
            current.name.lower() in {"win64", "wingdk"} and current.parent.name.lower() == "binaries"
        ):
            return "unreal"
        if current == install_path or current.parent == current:
            break
        current = current.parent
    return "unknown"


def quick_engine(install_path: Path) -> str:
    """Unreal/Unity from a shallow look at the install folder, without picking an executable."""
    for dirpath, depth, filenames in walk(install_path, max_depth=4):
        names = {name.lower() for name in filenames}
        if "unityplayer.dll" in names or "gameassembly.dll" in names or "globalgamemanagers" in names:
            return "unity"
        if any(name.endswith("-shipping.exe") or name.endswith(".uproject") for name in names):
            return "unreal"
        if depth and dirpath.name.lower() == "binaries" and (dirpath / "Win64").is_dir():
            return "unreal"
    return "unknown"


def detect_api(exe: Path, exclude: set[str] | None = None) -> tuple[str, str, str]:
    """Return (api, confidence, source) from the exe's imports, then sibling DLLs."""
    exclude = exclude or set()
    info = read_pe(exe)
    api = _api_from_imports(info.imports if info else set())
    if api != "unknown":
        return api, "high", "exe_imports"
    siblings = []
    try:
        siblings = sorted(exe.parent.glob("*.dll"), key=lambda path: (path.name.lower() not in {"unityplayer.dll", "gameassembly.dll"}, path.name.lower()))
    except OSError:
        pass
    for dll in siblings[:40]:
        if str(dll) in exclude or dll.name.lower() in PROXY_DLLS:
            continue
        sibling = read_pe(dll)
        api = _api_from_imports(sibling.imports if sibling else set())
        if api != "unknown":
            return api, "medium", f"dll_imports:{dll.name}"
    return "unknown", "none", ""


def _api_from_imports(imports: set[str]) -> str:
    for name in API_PRIORITY:
        if f"{name}.dll" in imports:
            return API_FROM_IMPORT[name]
    return "unknown"


def scan_anti_cheat(install_path: Path) -> list[str]:
    """Scan the whole install (not just the exe folder) for anti-cheat, case-insensitively."""
    found: set[str] = set()
    base_depth = len(Path(install_path).parts)
    count = 0
    for dirpath, dirnames, filenames in os.walk(install_path):
        depth = len(Path(dirpath).parts) - base_depth
        for name in dirnames:
            label = ANTI_CHEAT_DIRS.get(name.lower())
            if label:
                found.add(label)
        if depth >= 5:
            dirnames[:] = []
        names = {name.lower() for name in filenames}
        for label, signatures in ANTI_CHEAT_FILES.items():
            if names & signatures:
                found.add(label)
        count += len(filenames) + len(dirnames)
        if count > MAX_WALK_ENTRIES:
            break
    return sorted(found)


def looks_like_linux_build(install_path: Path) -> bool:
    for dirpath, depth, filenames in walk(install_path, max_depth=2):
        for filename in filenames:
            lower = filename.lower()
            if lower.endswith((".x86_64", ".x86")) or lower in {"start.sh", "run.sh"}:
                return True
            path = dirpath / filename
            if "." not in filename and depth <= 1:
                try:
                    with open(path, "rb") as handle:
                        if handle.read(4) == b"\x7fELF":
                            return True
                except OSError:
                    continue
    return False


def scan_game(
    install_path: Path,
    title: str,
    *,
    exe_override: str = "",
    override_note: str = "Using the executable you selected.",
    launched_exe: Path | None = None,
    exclude: set[str] | None = None,
) -> GameScan:
    install_path = Path(install_path)
    scan = GameScan(install_path=str(install_path))
    if not install_path.is_dir():
        scan.notes.append("Install folder is missing.")
        return scan
    exclude = exclude or set()
    scan.candidates = find_executables(install_path, title, exclude)
    if launched_exe is not None:
        # What Steam actually launched is a strong hint, but it can be a launcher stub.
        for candidate in scan.candidates:
            if Path(candidate.path) == launched_exe:
                candidate.score += 20
        scan.candidates.sort(key=lambda item: (-item.score, item.path))
    chosen: Path | None = None
    if exe_override and Path(exe_override).is_file() and _within(Path(exe_override), install_path):
        chosen = Path(exe_override)
        scan.notes.append(override_note)
    elif scan.candidates:
        chosen = Path(scan.candidates[0].path)
    if chosen is None:
        scan.linux_build = looks_like_linux_build(install_path)
        scan.anti_cheat = scan_anti_cheat(install_path)
        return scan
    if not exe_override:
        chosen = prefer_shipping_exe(chosen, install_path)
    scan.exe_path = str(chosen)
    info = read_pe(chosen)
    scan.architecture = info.arch if info else "unknown"
    scan.engine = detect_engine(chosen.parent, install_path)
    scan.api, scan.api_confidence, scan.api_source = detect_api(chosen, exclude)
    if scan.api == "unknown" and scan.engine in {"unreal", "unity"} and scan.architecture == "64":
        scan.api, scan.api_confidence, scan.api_source = "dx11_dx12", "heuristic", f"{scan.engine}_engine"
        scan.notes.append(f"{scan.engine.title()} engine detected; treating the API as DX11/DX12.")
    scan.hook = hook_for_api(scan.api) if scan.api != "unknown" else ""
    scan.anti_cheat = scan_anti_cheat(install_path)
    return scan


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False
