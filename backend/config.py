"""Constants and filesystem layout."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import pwd
except ImportError:  # pragma: no cover - non-POSIX
    pwd = None  # type: ignore[assignment]

PLUGIN_NAME = "Decky RenoDX"
PLUGIN_PACKAGE = "decky-renodx"
GITHUB_REPO = "Feelsrat/decky-renodx"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
COMPAT_DB_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/compatibility.json"

RENODX_MODS_URL = "https://raw.githubusercontent.com/wiki/clshortfuse/renodx/Mods.md"
RESHADE_HOME_URL = "https://reshade.me/"
RESHADE_FALLBACK_SETUP_URL = "https://reshade.me/downloads/ReShade_Setup_6.7.3_Addon.exe"
RESHADE_MIN_VERSION = (6, 7, 3)
RESHADE_FXH_URL = "https://raw.githubusercontent.com/crosire/reshade-shaders/slim/Shaders/ReShade.fxh"
SPECIALK_RELEASES_URL = "https://api.github.com/repos/SpecialKO/SpecialK/releases/latest"
LILIUM_RELEASES_URL = "https://api.github.com/repos/EndlesslyFlowering/ReShade_HDR_shaders/releases/latest"
PUMBO_AUTOHDR_ZIP_URL = "https://github.com/Filoppi/PumboAutoHDR/archive/refs/heads/master.zip"
AUTOHDR_ADDON_ZIP_URL = "https://github.com/MajorPainTheCactus/AutoHDR-ReShade/archive/refs/heads/main.zip"
SEVENZIP_VERSION = "2501"

# Environment variables every HDR launch gets. Deliberately absent:
# - PROTON_LOG: writes a multi-MB log on every launch.
# - ENABLE_HDR_WSI: only for the desktop VK_hdr_layer; under gamescope it can wash colours out.
# - ENABLE_GAMESCOPE_WSI: Game Mode already enables it; forcing it can crash 32-bit games on
#   some gamescope builds (ValveSoftware/gamescope#1718).
HDR_ENV = {
    "PROTON_ENABLE_HDR": "1",
    "DXVK_HDR": "1",
}

STATE_DIR_NAME = ".decky-renodx"  # per-directory backups/stash next to game files
LEGACY_MARKER = ".decky-renodx-hdr.json"


@dataclass(frozen=True)
class Paths:
    """Where the plugin keeps its own data. Everything lives in the deck user's home."""

    home: Path
    user: str
    plugin_dir: Path

    @property
    def data(self) -> Path:
        return self.home / ".local" / "share" / PLUGIN_PACKAGE

    @property
    def runtime(self) -> Path:
        return self.data / "runtime"

    @property
    def bin(self) -> Path:
        return self.data / "bin"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def installs(self) -> Path:
        return self.data / "installs"

    @property
    def imports(self) -> Path:
        return self.data / "imports"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"

    @property
    def compat_cache(self) -> Path:
        return self.cache / "compatibility.json"

    # Locations used by plugin versions <= 0.0.x, read only for migration.
    @property
    def legacy_manifests(self) -> Path:
        return self.data / "manifests"

    @property
    def legacy_runtime(self) -> Path:
        return self.data / "reshade"

    def assets_dir(self) -> Path:
        for candidate in (self.plugin_dir / "defaults" / "assets", self.plugin_dir / "assets"):
            if candidate.exists():
                return candidate
        return self.plugin_dir / "defaults" / "assets"


def resolve_user(decky_user: str = "", decky_user_home: str = "") -> tuple[str, Path]:
    """Work out the real (non-root) user and home the plugin should act for."""
    user = decky_user or os.environ.get("SUDO_USER") or ""
    if user == "root":
        user = ""
    home = Path(decky_user_home) if decky_user_home and decky_user_home != "/root" else None
    if home is None and user and pwd is not None:
        try:
            home = Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            home = None
    if home is None:
        home = Path("/home") / (user or "deck")
    if not user:
        user = home.name if home.name and home.name != "root" else "deck"
    return user, home
