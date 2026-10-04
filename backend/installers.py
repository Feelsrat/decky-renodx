"""One installer per HDR method. Each writes only through a Transaction."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsutil, launch
from .compat import CompatDB
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


def upsert_ini(text: str, section: str, values: dict[str, str], *, case_sensitive: bool = False) -> str:
    """Set keys in an INI section, keeping everything else as is. ReShade's own config lookup is
    case-sensitive, so its add-on sections (RenoDX) need ``case_sensitive``."""
    pattern = re.compile(rf"(?ims)^\[{re.escape(section)}\][^\n]*\n?(.*?)(?=^\[[^\]\n]+\]|\Z)")
    match = pattern.search(text)
    body = match.group(1) if match else ""
    for key, value in values.items():
        line = f"{key}={value}"
        key_re = re.compile(rf"(?m{'' if case_sensitive else 'i'})^{re.escape(key)}\s*=.*$")
        if key_re.search(body):
            body = key_re.sub(lambda _m, line=line: line, body)
        else:
            body = body.rstrip("\n") + ("\n" if body.strip() else "") + line + "\n"
    block = f"[{section}]\n{body.strip()}\n\n"
    if match:
        return text[: match.start()] + block + text[match.end():]
    return text.rstrip() + ("\n\n" if text.strip() else "") + block


# ---------------------------------------------------------------- ReShade host

def ini_sections(text: str, prefix: str) -> dict[str, dict[str, str]]:
    """Sections whose name starts with ``prefix`` (case-insensitive), as {section: {key: value}}."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in (text or "").splitlines():
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            name = header.group(1).strip()
            current = sections.setdefault(name, {}) if name.lower().startswith(prefix.lower()) else None
        elif current is not None and "=" in line and not line.lstrip().startswith((";", "#")):
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections


def renodx_screen_settings(peak_nits: float | None) -> dict[str, dict[str, str]]:
    """Starting RenoDX values for this screen. Mods spell the key differently and ReShade's
    config lookup is case-sensitive, so both spellings are written; unused keys are ignored."""
    if not peak_nits:
        return {}
    value = f"{float(peak_nits):g}"
    return {"renodx-preset1": {"ToneMapPeakNits": value, "toneMapPeakNits": value}}


PEAK_KEYS = {"ToneMapPeakNits", "toneMapPeakNits"}


def _same_number(a: str, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.05
    except (TypeError, ValueError):
        return False


def _reshade_host(tx: Transaction, target: Target, runtime: Runtime, hook: str, *, effects: bool,
                  sections: dict[str, dict[str, str]] | None = None, previous_ini: str = "",
                  previous_auto: list[float] | None = None) -> dict[str, Any]:
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
    for section, values in (sections or {}).items():
        ini = upsert_ini(ini, section, values, case_sensitive=True)
    # Settings the user changed in the RenoDX tab win over our defaults and survive a reinstall.
    for section, values in ini_sections(previous_ini, "renodx").items():
        # A peak this plugin wrote earlier isn't a user choice: let the new value replace it.
        values = {key: value for key, value in values.items()
                  if not (key in PEAK_KEYS and any(_same_number(value, auto) for auto in previous_auto or []))}
        if values:
            ini = upsert_ini(ini, section, values, case_sensitive=True)
    tx.write_text(target.dir / "ReShade.ini", ini)
    return reshade


def install_reshade(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB, *, previous_ini: str = "") -> dict[str, Any]:
    if target.api == "vulkan":
        raise InstallError("ReShade proxy DLLs cannot hook Vulkan games.")
    hook = target.hook or "dxgi"
    pack = runtime.autohdr_pack()
    reshade = _reshade_host(tx, target, runtime, hook, effects=True, previous_ini=previous_ini)
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

def install_renodx(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB, addon_file: Path, mod: dict[str, Any],
                   *, peak_nits: float | None = None, previous_ini: str = "", previous_auto: list[float] | None = None) -> dict[str, Any]:
    if target.api == "vulkan":
        raise InstallError("This game uses Vulkan; RenoDX through a ReShade proxy DLL cannot hook it.")
    addon_bits = "32" if addon_file.name.lower().endswith(".addon32") else "64"
    if target.arch in {"32", "64"} and addon_bits != target.arch:
        raise InstallError(f"{addon_file.name} is a {addon_bits}-bit addon but the game is {target.arch}-bit.")
    hook = target.hook or "dxgi"
    reshade = _reshade_host(tx, target, runtime, hook, effects=False, sections=renodx_screen_settings(peak_nits),
                            previous_ini=previous_ini, previous_auto=previous_auto)
    tx.write_text(target.dir / "ReShadePreset.ini", "Techniques=\nTechniqueSorting=\n")
    addon_target = tx.copy_file(addon_file, target.dir / addon_file.name)
    return {
        "method": "renodx",
        "dll": hook,
        "launch": launch.spec(hook, args=compat.game_args(target.appid, "renodx")),
        "extra": {"addon": str(addon_target), "mod": _mod_summary(mod), "reshade_version": reshade["version"], "peak_nits": peak_nits},
        "message": f"RenoDX installed ({mod.get('name') or addon_file.name}) with ReShade {reshade['version']} as {hook}.dll."
                   + (f" Peak brightness set to {float(peak_nits):g} nits for this screen." if peak_nits else ""),
    }


def _mod_summary(mod: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "status", "match_type", "addon_url", "manual_url", "source_type", "bitness", "imported_from")
    return {key: mod[key] for key in keys if key in mod}


# ---------------------------------------------------------------- Special K

def specialk_ini(text: str, tweaks: dict[str, dict[str, str]], peak_nits: float | None = None) -> str:
    sections: dict[str, dict[str, str]] = {
        "SpecialK.System": {"UsingWINE": "true"},
        "Render.OSD": {"HDRLuminance": "9.375"},
        "SpecialK.HDR": {
            "HDR.Enable": "true", "Use16BitSwapChain": "true", "AllowFullLuminance": "true",
            "scRGBLuminance_[0]": "18.75", "scRGBGamma_[0]": "1.0", "ToneMapper_[0]": "1",
            "Saturation_[0]": "1.0", "MiddleGray_[0]": "1.25", "Preset": "0",
        },
    }
    if peak_nits:
        # Special K's scRGB luminance is in units of 80 nits.
        sections["SpecialK.HDR"]["scRGBLuminance_[0]"] = f"{float(peak_nits) / 80:.3f}"
    for section, values in tweaks.items():
        sections.setdefault(section, {}).update(values)
    for section, values in sections.items():
        text = upsert_ini(text, section, values)
    return text


def install_specialk(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB, *, peak_nits: float | None = None) -> dict[str, Any]:
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
    ini = specialk_ini("", compat.specialk_ini_tweaks(target.appid), peak_nits)
    tx.write_text(directory / f"{hook}.ini", ini)
    tx.write_text(directory / "SpecialK.ini", ini)
    return {
        "method": "special_k",
        "dll": hook,
        "launch": launch.spec(hook, args=compat.game_args(target.appid, "special_k")),
        "extra": {"specialk_dir": str(directory), "peak_nits": peak_nits},
        "message": f"Special K installed as {hook}.dll. Open its menu in game (Ctrl+Shift+Backspace) to confirm HDR.",
    }

