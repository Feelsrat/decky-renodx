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
# ReShade is pinned rather than "latest from reshade.me": 6.7.3 is what worked on a Deck before,
# and a fresh install picking up 6.8.0 (Aug 2026) coincided with ReShade no longer loading.
# Bump only after testing a game on a device.
RESHADE_VERSION = (6, 7, 3)
RESHADE_SETUP_URL = "https://reshade.me/downloads/ReShade_Setup_6.7.3_Addon.exe"
RESHADE_FXH_URL = "https://raw.githubusercontent.com/crosire/reshade-shaders/slim/Shaders/ReShade.fxh"
SPECIALK_RELEASES_URL = "https://api.github.com/repos/SpecialKO/SpecialK/releases/latest"
LILIUM_RELEASES_URL = "https://api.github.com/repos/EndlesslyFlowering/ReShade_HDR_shaders/releases/latest"
PUMBO_AUTOHDR_ZIP_URL = "https://github.com/Filoppi/PumboAutoHDR/archive/refs/heads/master.zip"
AUTOHDR_ADDON_ZIP_URL = "https://github.com/MajorPainTheCactus/AutoHDR-ReShade/archive/refs/heads/main.zip"
SEVENZIP_VERSION = "2501"
# dgVoodoo2 translates DirectX 8/9 (and DirectDraw) to DirectX 11, so Special K's HDR and the
# AutoHDR addon, which need DX10+, work in older games. Pinned with its checksum.
DGVOODOO_URL = "https://github.com/dege-diosg/dgVoodoo2/releases/download/v2.87.5/dgVoodoo2_87_5.zip"
DGVOODOO_SHA256 = "5ffde6927f7355ca3fdd5d785b581256a8e6539fa13e395a891ade6ba1040850"

# Environment variables every HDR launch gets: the set the 0.0.x/0.1.0 plugin used and that
# is known to work on a Deck OLED (RenoDX in Wobbly Life, Against the Storm). 0.2.0-0.4.2 dropped
# the two WSI switches on untested theory and HDR stopped turning on; don't remove them again
# without testing on a device. PROTON_LOG stays out: it writes a multi-MB log on every launch.
HDR_ENV = {
    "PROTON_ENABLE_HDR": "1",
    "DXVK_HDR": "1",
    "ENABLE_HDR_WSI": "1",
    "ENABLE_GAMESCOPE_WSI": "1",
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
