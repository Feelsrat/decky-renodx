"""Shared downloads (ReShade, Special K, shader packs, 7-Zip), fetched only when a method needs them."""
from __future__ import annotations

import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from . import fsutil, log, net
from .config import (
    AUTOHDR_ADDON_ZIP_URL, LILIUM_RELEASES_URL, PUMBO_AUTOHDR_ZIP_URL,
    DGVOODOO_SHA256, DGVOODOO_URL, RESHADE_FXH_URL, RESHADE_SETUP_URL, RESHADE_VERSION, SEVENZIP_VERSION, SPECIALK_RELEASES_URL,
    Paths,
)


class ComponentError(RuntimeError):
    pass


class Runtime:
    def __init__(self, paths: Paths, *, download: Callable[..., Path] = net.download, fetch_text: Callable[..., str] = net.fetch_text, fetch_json: Callable[..., Any] = net.fetch_json):
        self.paths = paths
        self._download = download
        self._fetch_text = fetch_text
        self._fetch_json = fetch_json
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self.paths.runtime

    # ------------------------------------------------------------ 7-Zip
    def sevenzip(self) -> Path:
        with self._lock:
            target = self.paths.bin / "7zz"
            if target.is_file():
                return target
            machine = platform.machine().lower()
            name = f"7z{SEVENZIP_VERSION}-linux-{'arm64' if machine in {'aarch64', 'arm64'} else 'x64'}.tar.xz"
            with tempfile.TemporaryDirectory(prefix="decky-renodx-7z-") as temp:
                archive = self._download(f"https://www.7-zip.org/a/{name}", Path(temp) / name, min_size=100_000)
                with tarfile.open(archive, "r:xz") as tar:
                    members = [member for member in tar.getmembers() if member.isfile() and Path(member.name).name == "7zz"]
                    if not members:
                        raise ComponentError("7zz was not found in the 7-Zip archive")
                    fsutil.safe_extract_tar(tar, Path(temp) / "x", members[:1])
                    extracted = Path(temp) / "x" / members[0].name
                # Write under a temporary name and rename, so an interrupted copy
                # never leaves a truncated 7zz that every later extraction trips over.
                fsutil.atomic_write_bytes(target, extracted.read_bytes(), mode=0o755)
            return target

    def extract(self, archive: Path, target: Path, *, only: list[str] | None = None, flat: bool = False) -> None:
        if archive.suffix.lower() == ".zip" and not flat and not only:
            with zipfile.ZipFile(archive) as handle:
                fsutil.safe_extract_zip(handle, target)
            return
        fsutil.makedirs(target)
        command = [str(self.sevenzip()), "e" if flat else "x", "-y", f"-o{target}", str(archive), *(only or [])]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, env=net.clean_env())
        if result.returncode != 0:
            raise ComponentError(f"Could not extract {archive.name}: {(result.stderr or result.stdout).strip()[-300:]}")
        for path in list(target.rglob("*")):
            if path.is_symlink():
                path.unlink()
            elif not fsutil.is_within(path.resolve(), target.resolve()):
                raise ComponentError(f"{archive.name} contained a path outside the extraction folder")

    # ------------------------------------------------------------ ReShade
    def reshade(self) -> dict[str, Any]:
        """ReShade (full add-on build) DLLs, at the pinned RESHADE_VERSION."""
        with self._lock:
            meta_file = self.root / "reshade" / "current.json"
            meta = fsutil.read_json(meta_file, {}) or {}
            current_dir = Path(meta.get("dir", "")) if meta.get("dir") else None
            if current_dir and (current_dir / "ReShade64.dll").is_file() and tuple(meta.get("version", [])) == RESHADE_VERSION:
                return self._reshade_info(meta)
            target_dir = self.root / "reshade" / ".".join(map(str, RESHADE_VERSION))
            with tempfile.TemporaryDirectory(prefix="decky-renodx-reshade-") as temp:
                setup = self._download(RESHADE_SETUP_URL, Path(temp) / Path(RESHADE_SETUP_URL).name, min_size=1_000_000)
                unpacked, staging = Path(temp) / "x", Path(temp) / "dlls"
                # Keep the installer's folder layout and take the top-level DLLs, so a same-named
                # file in a subfolder (another architecture, say) can never replace them.
                self.extract(setup, unpacked)
                fsutil.makedirs(staging)
                for name in ("ReShade64.dll", "ReShade32.dll"):
                    found = sorted(unpacked.rglob(name), key=lambda path: len(path.relative_to(unpacked).parts))
                    if found:
                        shutil.copyfile(found[0], staging / name)
                if not (staging / "ReShade64.dll").is_file():
                    raise ComponentError("ReShade64.dll was not found in the ReShade installer")
                _replace_dir(staging, target_dir)
            meta = {"version": list(RESHADE_VERSION), "dir": str(target_dir), "url": RESHADE_SETUP_URL}
            fsutil.write_json(meta_file, meta)
            return self._reshade_info(meta)

    def _reshade_info(self, meta: dict[str, Any]) -> dict[str, Any]:
        directory = Path(meta["dir"])
        return {"version": ".".join(map(str, meta["version"])), "dir": directory, "dll": {"64": directory / "ReShade64.dll", "32": directory / "ReShade32.dll"}}

    # ------------------------------------------------------------ AutoHDR shader pack
    def autohdr_pack(self) -> dict[str, Any]:
        """AutoHDR addons plus HDR shaders (Lilium, Pumbo AdvancedAutoHDR) in ReShade's layout."""
        with self._lock:
            root = self.root / "autohdr"
            marker = root / "ready.json"
            if not marker.exists():
                staging_parent = Path(tempfile.mkdtemp(prefix="decky-renodx-shaders-"))
                try:
                    self._build_autohdr_pack(staging_parent)
                    if root.exists():
                        shutil.rmtree(root)
                    fsutil.makedirs(root.parent)
                    shutil.move(str(staging_parent / "pack"), str(root))
                    fsutil.write_json(marker, {"built_at": time.time()})
                    fsutil.chown_tree(root)
                finally:
                    shutil.rmtree(staging_parent, ignore_errors=True)
            addons = {arch: root / "addons" / f"AutoHDR{arch}.addon" for arch in ("32", "64")}
            return {"shaders": root / "ReShade_shaders", "addons": {arch: path for arch, path in addons.items() if path.is_file()}}

    def _build_autohdr_pack(self, temp: Path) -> None:
        pack = temp / "pack"
        shaders = pack / "ReShade_shaders" / "Merged" / "Shaders"
        textures = pack / "ReShade_shaders" / "Merged" / "Textures"
        addons = pack / "addons"
        for directory in (shaders, textures, addons):
            directory.mkdir(parents=True)

        autohdr_zip = self._download(AUTOHDR_ADDON_ZIP_URL, temp / "autohdr.zip", min_size=1024)
        self.extract(autohdr_zip, temp / "autohdr")
        found_addon = False
        for path in (temp / "autohdr").rglob("*"):
            lower = path.name.lower()
            if not path.is_file():
                continue
            if ".addon" in lower:
                arch = "32" if "32" in lower else "64"
                shutil.copyfile(path, addons / f"AutoHDR{arch}.addon")
                found_addon = True
            elif path.suffix.lower() in {".fx", ".fxh"}:
                shutil.copyfile(path, shaders / path.name)
        if not found_addon:
            raise ComponentError("AutoHDR add-on files were not found in the download")

        self._download(RESHADE_FXH_URL, shaders / "ReShade.fxh", min_size=1024)
        pumbo = self._download(PUMBO_AUTOHDR_ZIP_URL, temp / "pumbo.zip", min_size=1024)
        self._copy_shader_archive(pumbo, temp / "pumbo", shaders, textures, keep_layout=False)
        try:
            lilium = self._latest_asset(LILIUM_RELEASES_URL, (".7z", ".zip"), temp / "lilium")
            self._copy_shader_archive(lilium, temp / "lilium-x", shaders, textures, keep_layout=True)
        except Exception as error:
            log.plugin().warning("Lilium HDR shaders unavailable, continuing without them: %s", error)

    def _copy_shader_archive(self, archive: Path, scratch: Path, shaders: Path, textures: Path, *, keep_layout: bool) -> None:
        self.extract(archive, scratch)
        for path in scratch.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in {".fx", ".fxh"}:
                target = shaders / (_relative_to_shaders(path, scratch) if keep_layout else Path(path.name))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            elif suffix in {".png", ".jpg", ".jpeg", ".dds"} or "texture" in str(path.parent).lower():
                shutil.copyfile(path, textures / path.name)

    def _latest_asset(self, api_url: str, extensions: tuple[str, ...], target_stem: Path) -> Path:
        release = self._fetch_json(api_url, timeout=15)
        for asset in (release or {}).get("assets", []) if isinstance(release, dict) else []:
            name = str(asset.get("name", ""))
            if name.lower().endswith(extensions) and asset.get("browser_download_url"):
                digest = str(asset.get("digest") or "")
                return self._download(
                    str(asset["browser_download_url"]), target_stem.with_name(name), min_size=1024,
                    sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
                )
        raise ComponentError(f"No downloadable asset in {api_url}")

    # ------------------------------------------------------------ Special K
    def specialk(self) -> Path:
        with self._lock:
            target = self.root / "SpecialK"
            if (target / READY).is_file():
                return target
            with tempfile.TemporaryDirectory(prefix="decky-renodx-sk-") as temp:
                archive = self._latest_asset(SPECIALK_RELEASES_URL, (".7z", ".zip"), Path(temp) / "sk")
                staging = Path(temp) / "x"
                self.extract(archive, staging)
                if not any(staging.rglob("SpecialK64.dll")):
                    raise ComponentError("SpecialK64.dll was not found in the Special K release")
                (staging / READY).write_text("ok", encoding="utf-8")
                _replace_dir(staging, target)
            return target

    def specialk_dll(self, arch: str) -> Path:
        name = "SpecialK32.dll" if arch == "32" else "SpecialK64.dll"
        matches = sorted(self.specialk().rglob(name))
        if not matches:
            raise ComponentError(f"{name} is missing from the Special K runtime")
        return matches[0]

    def specialk_injector(self) -> Path | None:
        root = self.specialk()
        for name in ("SKIF.exe", "SpecialK.exe", "SpecialK64.exe"):
            matches = sorted(root.rglob(name))
            if matches:
                return matches[0]
        return None

    # ------------------------------------------------------------ dgVoodoo2
    def dgvoodoo(self) -> Path:
        """The unpacked dgVoodoo2 release (MS/x86, MS/x64 DLLs and its default dgVoodoo.conf)."""
        with self._lock:
            target = self.root / "dgvoodoo" / Path(DGVOODOO_URL).stem
            if (target / READY).is_file():
                return target
            with tempfile.TemporaryDirectory(prefix="decky-renodx-dgvoodoo-") as temp:
                archive = self._download(DGVOODOO_URL, Path(temp) / "dgvoodoo.zip", min_size=1_000_000, sha256=DGVOODOO_SHA256)
                staging = Path(temp) / "x"
                with zipfile.ZipFile(archive) as handle:
                    fsutil.safe_extract_zip(handle, staging)
                if not (staging / "MS" / "x86" / "D3D9.dll").is_file() or not (staging / "dgVoodoo.conf").is_file():
                    raise ComponentError("dgVoodoo2 download is missing D3D9.dll or dgVoodoo.conf")
                (staging / READY).write_text("ok", encoding="utf-8")
                _replace_dir(staging, target)
            return target

    # ------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        meta = fsutil.read_json(self.root / "reshade" / "current.json", {}) or {}
        components = {
            "reshade": ".".join(map(str, meta["version"])) if meta.get("version") else "",
            "autohdr": (self.root / "autohdr" / "ready.json").exists(),
            "specialk": (self.root / "SpecialK").exists(),
            "sevenzip": (self.paths.bin / "7zz").exists(),
        }
        return {"installed": bool(components["reshade"] or components["autohdr"] or components["specialk"]), "components": components}

    def remove(self) -> None:
        with self._lock:
            for path in (self.root, self.paths.legacy_runtime, self.paths.bin):
                if path.exists():
                    shutil.rmtree(path)


READY = ".decky-renodx-ready"


def _replace_dir(source: Path, target: Path) -> None:
    """Copy ``source`` next to ``target`` first, then swap it in with renames.

    An interruption leaves either the old folder or a stray temporary one, never a half-copied target.
    """
    fsutil.makedirs(target.parent)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=str(target.parent)))
    try:
        shutil.copytree(source, temp, dirs_exist_ok=True)
        fsutil.chown_tree(temp)
        old = target.with_name(f".{target.name}.old")
        if old.exists():
            shutil.rmtree(old)
        if target.exists():
            target.rename(old)
        temp.rename(target)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _relative_to_shaders(path: Path, root: Path) -> Path:
    parts = list(path.relative_to(root).parts)
    lower = [part.lower() for part in parts]
    for marker in ("shaders", "reshade-shaders", "reshade_shaders"):
        if marker in lower[:-1]:
            return Path(*parts[lower.index(marker) + 1:])
    return Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
