"""One installer per HDR method. Each writes only through a Transaction."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsutil, launch
from .compat import CompatDB
from .config import DISPLAY_COMMANDER_NAME
from .runtime import Runtime
from .transaction import Transaction

AUTOHDR_HOOKS = {"dxgi", "d3d11", "d3d12"}
RESHADE_ARTIFACTS = ["ReShade.log", "ReShade.log1", "ReShade.log2"]
SPECIALK_ARTIFACTS = ["SpecialK.log", "SpecialK.permissions"]


class InstallError(RuntimeError):
    pass


@dataclass
class Target:
    appid: str
    title: str
    exe_path: Path
    install_path: Path
    compatdata: Path
    arch: str
    api: str
    hook: str

    @property
    def dir(self) -> Path:
        return self.exe_path.parent

    @property
    def bits(self) -> str:
        return "32" if self.arch == "32" else "64"


def upsert_ini(text: str, section: str, values: dict[str, str]) -> str:
    """Set keys in an INI section, keeping everything else as is."""
    pattern = re.compile(rf"(?ims)^\[{re.escape(section)}\][^\n]*\n?(.*?)(?=^\[[^\]\n]+\]|\Z)")
    match = pattern.search(text)
    body = match.group(1) if match else ""
    for key, value in values.items():
        line = f"{key}={value}"
        key_re = re.compile(rf"(?im)^{re.escape(key)}\s*=.*$")
        if key_re.search(body):
            body = key_re.sub(lambda _m, line=line: line, body)
        else:
            body = body.rstrip("\n") + ("\n" if body.strip() else "") + line + "\n"
    block = f"[{section}]\n{body.strip()}\n\n"
    if match:
        return text[: match.start()] + block + text[match.end():]
    return text.rstrip() + ("\n\n" if text.strip() else "") + block


# ---------------------------------------------------------------- ReShade host

def _reshade_host(tx: Transaction, target: Target, runtime: Runtime, hook: str, *, effects: bool) -> dict[str, Any]:
    reshade = runtime.reshade()
    dll_source = reshade["dll"][target.bits]
    if not dll_source.is_file():
        raise InstallError(f"ReShade{target.bits}.dll is missing from the ReShade download")
    tx.track_artifacts(target.dir, [*RESHADE_ARTIFACTS, f"{hook}.log"])
    tx.copy_file(dll_source, target.dir / f"{hook}.dll")
    ini = ""
    if effects:
        ini = upsert_ini(ini, "GENERAL", {
            "EffectSearchPaths": ".\\ReShade_shaders\\Merged\\Shaders\\**",
            "TextureSearchPaths": ".\\ReShade_shaders\\Merged\\Textures\\**",
            "PresetPath": ".\\ReShadePreset.ini",
            "SkipLoadingDisabledEffects": "1",
        })
    else:
        ini = upsert_ini(ini, "GENERAL", {
            "EffectSearchPaths": "",
            "TextureSearchPaths": "",
            "PresetPath": ".\\ReShadePreset.ini",
            "SkipLoadingDisabledEffects": "1",
        })
    ini = upsert_ini(ini, "OVERLAY", {"TutorialProgress": "4"})
    tx.write_text(target.dir / "ReShade.ini", ini)
    return reshade


def install_reshade(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB) -> dict[str, Any]:
    if target.api == "vulkan":
        raise InstallError("ReShade proxy DLLs cannot hook Vulkan games.")
    hook = target.hook or "dxgi"
    pack = runtime.autohdr_pack()
    reshade = _reshade_host(tx, target, runtime, hook, effects=True)
    modern = hook in AUTOHDR_HOOKS
    ignore = None if modern else shutil.ignore_patterns("lilium*", "AdvancedAutoHDR.fx", "ConvertColorSpace.fx")
    tx.copy_tree(pack["shaders"], target.dir / "ReShade_shaders", ignore=ignore)
    addon = pack["addons"].get(target.bits)
    if modern and addon:
        tx.copy_file(addon, target.dir / f"AutoHDR.addon{target.bits}")
    tx.write_text(target.dir / "ReShadePreset.ini", "Techniques=AutoHDR\nPreprocessorDefinitions=\n")
    notes = [] if modern else [f"AutoHDR needs DX10-12; {target.api} games only get the basic shaders."]
    return {
        "method": "reshade",
        "dll": hook,
        "launch": launch.spec(hook, args=compat.game_args(target.appid, "reshade")),
        "extra": {"reshade_version": reshade["version"], "autohdr_addon": bool(modern and addon)},
        "message": f"ReShade {reshade['version']} with AutoHDR installed as {hook}.dll." + (f" {notes[0]}" if notes else ""),
    }


# ---------------------------------------------------------------- RenoDX

def install_renodx(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB, addon_file: Path, mod: dict[str, Any]) -> dict[str, Any]:
    if target.api == "vulkan":
        raise InstallError("This game uses Vulkan; RenoDX through a ReShade proxy DLL cannot hook it.")
    addon_bits = "32" if addon_file.name.lower().endswith(".addon32") else "64"
    if target.arch in {"32", "64"} and addon_bits != target.arch:
        raise InstallError(f"{addon_file.name} is a {addon_bits}-bit addon but the game is {target.arch}-bit.")
    hook = target.hook or "dxgi"
    reshade = _reshade_host(tx, target, runtime, hook, effects=False)
    tx.write_text(target.dir / "ReShadePreset.ini", "Techniques=\nTechniqueSorting=\n")
    addon_target = tx.copy_file(addon_file, target.dir / addon_file.name)
    companion = ""
    if addon_bits == "64":
        cached = runtime.display_commander()
        if cached is not None:
            companion = str(tx.copy_file(cached, target.dir / DISPLAY_COMMANDER_NAME))
    return {
        "method": "renodx",
        "dll": hook,
        "launch": launch.spec(hook, args=compat.game_args(target.appid, "renodx")),
        "extra": {"addon": str(addon_target), "display_commander": companion, "mod": _mod_summary(mod), "reshade_version": reshade["version"]},
        "message": f"RenoDX installed ({mod.get('name') or addon_file.name}) with ReShade {reshade['version']} as {hook}.dll."
        + (" Display Commander added." if companion else ""),
    }


def _mod_summary(mod: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "status", "match_type", "addon_url", "manual_url", "source_type", "bitness", "imported_from")
    return {key: mod[key] for key in keys if key in mod}


# ---------------------------------------------------------------- Special K

def specialk_ini(text: str, tweaks: dict[str, dict[str, str]]) -> str:
    sections: dict[str, dict[str, str]] = {
        "SpecialK.System": {"UsingWINE": "true"},
        "Render.OSD": {"HDRLuminance": "9.375"},
        "SpecialK.HDR": {
            "HDR.Enable": "true", "Use16BitSwapChain": "true", "AllowFullLuminance": "true",
            "scRGBLuminance_[0]": "18.75", "scRGBGamma_[0]": "1.0", "ToneMapper_[0]": "1",
            "Saturation_[0]": "1.0", "MiddleGray_[0]": "1.25", "Preset": "0",
        },
    }
    for section, values in tweaks.items():
        sections.setdefault(section, {}).update(values)
    for section, values in sections.items():
        text = upsert_ini(text, section, values)
    return text


def install_specialk(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB) -> dict[str, Any]:
    gate = compat.specialk_local_gate(target.appid)
    if not gate["available"]:
        raise InstallError(gate["reason"])
    if target.api == "vulkan":
        raise InstallError("Special K HDR does not support Vulkan games.")
    hook = compat.specialk_hook(target.appid) or target.hook or "dxgi"
    directory = target.dir
    subdir = compat.specialk_subdir(target.appid)
    if subdir:
        directory = target.dir / subdir
        if not fsutil.is_within(directory, target.install_path):
            directory = target.dir
    tx.mkdir(directory)
    tx.track_artifacts(directory, [*SPECIALK_ARTIFACTS, f"{hook}.log"])
    tx.copy_file(runtime.specialk_dll(target.bits), directory / f"{hook}.dll")
    ini = specialk_ini("", compat.specialk_ini_tweaks(target.appid))
    tx.write_text(directory / f"{hook}.ini", ini)
    tx.write_text(directory / "SpecialK.ini", ini)
    return {
        "method": "special_k",
        "dll": hook,
        "launch": launch.spec(hook, args=compat.game_args(target.appid, "special_k")),
        "extra": {"specialk_dir": str(directory)},
        "message": f"Special K installed as {hook}.dll. Open its menu in game (Ctrl+Shift+Backspace) to confirm HDR.",
    }


def install_specialk_delayed(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB, wrapper: Path, delay: int | None = None) -> dict[str, Any]:
    gate = compat.specialk_delayed_gate(target.appid)
    if not gate["available"]:
        raise InstallError(gate["reason"])
    prefix = target.compatdata / "pfx"
    if not (prefix / "drive_c").is_dir():
        raise InstallError("This game's Proton prefix does not exist yet. Launch the game once, quit, then try again.")
    sk_dir = prefix / "drive_c" / "users" / "steamuser" / "Documents" / "My Mods" / "SpecialK"
    tx.copy_tree(runtime.specialk(), sk_dir)
    injector = next((path for name in ("SKIF.exe", "SpecialK.exe", "SpecialK64.exe") for path in sorted(sk_dir.rglob(name))), None)
    if injector is None:
        raise InstallError("The Special K download has no global injector (SKIF.exe).")
    delay = delay or compat.specialk_delay(target.appid)
    tx.write_text(sk_dir / "SpecialK.ini", specialk_ini("", compat.specialk_ini_tweaks(target.appid)))
    profile = upsert_ini("", f"Profile.{target.exe_path.stem}", {
        "Title": target.title, "Executable": target.exe_path.name, "Enabled": "true", "GlobalInjectDelay": f"{float(delay)}",
    })
    tx.write_text(sk_dir / "Profiles.ini", profile)
    wrapper_args = ["bash", str(wrapper), target.appid, str(delay), str(injector), "--"]
    return {
        "method": "special_k_delayed",
        "dll": "",
        "launch": launch.spec("", args=compat.game_args(target.appid, "special_k"), wrapper=wrapper_args),
        "extra": {"specialk_dir": str(sk_dir), "delay": delay, "injector": str(injector)},
        "message": f"Special K will be injected {delay}s after launch (experimental). Check its HDR menu in game.",
    }
