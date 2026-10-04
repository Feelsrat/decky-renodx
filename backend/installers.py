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


def renodx_screen_settings(peak_nits: float | None, sdr_nits: float | None = None) -> dict[str, dict[str, str]]:
    """Starting RenoDX values for this screen. Mods spell keys differently and ReShade's
    config lookup is case-sensitive, so both spellings are written; unused keys are ignored."""
    values: dict[str, str] = {}
    if peak_nits:
        values.update(ToneMapPeakNits=f"{float(peak_nits):g}", toneMapPeakNits=f"{float(peak_nits):g}")
    if sdr_nits:
        nits = f"{float(sdr_nits):g}"
        values.update(ToneMapGameNits=nits, toneMapGameNits=nits, ToneMapUINits=nits, toneMapUINits=nits)
    return {"renodx-preset1": values} if values else {}


def addon_knows_game(addon: Path, names: list[str]) -> bool:
    """Whether a (generic) RenoDX addon names this game itself: its built-in per-game defaults
    are keyed by exe file or product name, stored as plain strings in the DLL."""
    wanted = [name.encode() for name in names if name and len(name) >= 6]
    if not wanted:
        return False
    try:
        with open(addon, "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    return any(name in data for name in wanted)


def merge_sections(*layers: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for layer in layers:
        for section, values in (layer or {}).items():
            merged.setdefault(section, {}).update({str(k): str(v) for k, v in values.items()})
    return {section: values for section, values in merged.items() if values}


def _same_value(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.05
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def carried_settings(previous_ini: str, previous_auto: dict[str, dict[str, str]] | None) -> dict[str, dict[str, str]]:
    """RenoDX settings from the old ReShade.ini that the user chose: values equal to what
    this plugin wrote automatically last time are dropped, so new automatic values apply."""
    auto = previous_auto or {}
    kept: dict[str, dict[str, str]] = {}
    for section, values in ini_sections(previous_ini, "renodx").items():
        mine = auto.get(section, {})
        values = {k: v for k, v in values.items() if not (k in mine and _same_value(v, mine[k]))}
        if values:
            kept[section] = values
    return kept


def _reshade_host(tx: Transaction, target: Target, runtime: Runtime, hook: str, *, effects: bool,
                  sections: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """ReShade as ``hook``.dll plus ReShade.ini; ``sections`` are written case-sensitively."""
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
    tx.write_text(target.dir / "ReShade.ini", ini)
    return reshade


# The RenoDX wiki's Engine.ini block for the generic Unreal mod ("Engine.ini" in a game's notes).
ENGINE_INI_HDR = {
    "SystemSettings": {
        "r.AllowHDR": "1", "r.HDR.EnableHDROutput": "1", "r.HDR.Display.OutputDevice": "3",
        "r.HDR.Display.ColorGamut": "2", "r.HDR.UI.CompositeMode": "1",
    },
    "/Script/Engine.RendererSettings": {"r.LUT.UpdateEveryFrame": "1"},
}
ENGINE_INI_OUTPUT_ONLY = {"SystemSettings": {"r.HDR.EnableHDROutput": "1"}}


def engine_ini_path(target: Target) -> Path | None:
    """<prefix>/AppData/Local/<Project>/Saved/Config/<Windows|WindowsNoEditor|WinGDK>/Engine.ini,
    where <Project> is the folder holding Binaries/ (the *-Shipping.exe's grandparent's parent)."""
    parts = target.exe_path.parts
    lowered = [part.lower() for part in parts]
    if "binaries" not in lowered:
        return None
    project = parts[lowered.index("binaries") - 1]
    local = target.compatdata / "pfx" / "drive_c" / "users" / "steamuser" / "AppData" / "Local"
    if not local.is_dir():
        return None  # never launched: the prefix doesn't exist yet
    config = local / project / "Saved" / "Config"
    platforms = ["Windows", "WindowsNoEditor", "WinGDK"]
    for name in platforms:
        if (config / name / "Engine.ini").is_file():
            return config / name / "Engine.ini"
    for name in platforms:
        if (config / name).is_dir():
            return config / name / "Engine.ini"
    return config / "Windows" / "Engine.ini"


def apply_engine_ini(tx: Transaction, target: Target, mode: str) -> str:
    """Add the wiki's HDR lines to the game's Engine.ini (backed up; undone on removal) and make it
    read-only, as the wiki says, so the game can't drop them. Returns a note for the user."""
    path = engine_ini_path(target)
    if path is None:
        return "Launch the game once, then Repair, to add the Engine.ini HDR settings this game needs."
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    for section, values in (ENGINE_INI_OUTPUT_ONLY if mode == "output" else ENGINE_INI_HDR).items():
        text = upsert_ini(text, section, values)
    tx.write_text(path, text, mode=0o444)
    return "Added the Engine.ini HDR settings this game needs."


def install_reshade(tx: Transaction, target: Target, runtime: Runtime, compat: CompatDB, *, keep: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    if target.api == "vulkan":
        raise InstallError("ReShade proxy DLLs cannot hook Vulkan games.")
    hook = target.hook or "dxgi"
    pack = runtime.autohdr_pack()
    reshade = _reshade_host(tx, target, runtime, hook, effects=True, sections=keep)
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
                   *, auto: dict[str, dict[str, str]] | None = None, keep: dict[str, dict[str, str]] | None = None,
                   notes: list[str] | None = None, engine_ini: str = "") -> dict[str, Any]:
    """``auto``: values chosen for this game and screen; ``keep``: the user's own settings (win);
    ``engine_ini``: 'full'/'output' to add the wiki's Unreal Engine.ini lines."""
    if target.api == "vulkan":
        raise InstallError("This game uses Vulkan; RenoDX through a ReShade proxy DLL cannot hook it.")
    addon_bits = "32" if addon_file.name.lower().endswith(".addon32") else "64"
    if target.arch in {"32", "64"} and addon_bits != target.arch:
        raise InstallError(f"{addon_file.name} is a {addon_bits}-bit addon but the game is {target.arch}-bit.")
    hook = target.hook or "dxgi"
    reshade = _reshade_host(tx, target, runtime, hook, effects=False, sections=merge_sections(auto or {}, keep or {}))
    tx.write_text(target.dir / "ReShadePreset.ini", "Techniques=\nTechniqueSorting=\n")
    addon_target = tx.copy_file(addon_file, target.dir / addon_file.name)
    notes = list(notes or [])
    pending_engine_ini = ""
    if engine_ini:
        notes.append(apply_engine_ini(tx, target, engine_ini))
        pending_engine_ini = engine_ini if engine_ini_path(target) is None else ""
    return {
        "method": "renodx",
        "dll": hook,
        "launch": launch.spec(hook, args=compat.game_args(target.appid, "renodx")),
        "extra": {"addon": str(addon_target), "mod": _mod_summary(mod), "reshade_version": reshade["version"], "auto_ini": auto or {},
                  "engine_ini_pending": pending_engine_ini},
        "message": f"RenoDX installed ({mod.get('name') or addon_file.name}) with ReShade {reshade['version']} as {hook}.dll."
                   + (" " + " ".join(notes) if notes else ""),
    }


def _mod_summary(mod: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "status", "match_type", "addon_url", "manual_url", "source_type", "bitness", "imported_from", "notes", "engine_bucket")
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

