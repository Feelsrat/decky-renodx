"""Whether the screen supports HDR and whether it is turned on, read from gamescope.

gamescope publishes GAMESCOPE_DISPLAY_SUPPORTS_HDR on its Xwayland root window,
and Steam sets GAMESCOPE_DISPLAY_HDR_ENABLED when the user enables HDR. Outside
Game Mode (or without xprop) both are unknown.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from typing import Any

from . import net

ATOMS = {"supported": "GAMESCOPE_DISPLAY_SUPPORTS_HDR", "enabled": "GAMESCOPE_DISPLAY_HDR_ENABLED"}


def parse_xprop(output: str) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {key: None for key in ATOMS}
    for key, atom in ATOMS.items():
        match = re.search(rf"^{atom}\(CARDINAL\) = (\d+)", output, re.M)
        if match:
            result[key] = match.group(1) != "0"
    return result


def _auth_files(home: str) -> list[str]:
    """Candidate X authority files: none (gamescope's Xwayland usually needs none), then the user's."""
    files = [""]
    files += [path for path in [os.path.join(home, ".Xauthority"), *glob.glob("/run/user/*/xauth_*"), *glob.glob("/run/user/*/gamescope*auth*")] if os.path.isfile(path)]
    return files


def hdr_status(user: str, home: str = "") -> dict[str, Any]:
    xprop = shutil.which("xprop")
    if not xprop:
        return {"supported": None, "enabled": None, "game_mode": False}
    home = home or (f"/home/{user}" if user and user != "root" else "")
    for display in dict.fromkeys([os.environ.get("DISPLAY", ""), ":0", ":1"]):
        if not display:
            continue
        for auth in _auth_files(home):
            env = {**net.clean_env(), "DISPLAY": display}
            if auth:
                env["XAUTHORITY"] = auth
            else:
                env.pop("XAUTHORITY", None)
            try:
                result = subprocess.run([xprop, "-root", *ATOMS.values()], capture_output=True, text=True, timeout=3, env=env)
            except (OSError, subprocess.TimeoutExpired):
                continue
            status = parse_xprop(result.stdout)
            if status["supported"] is not None:
                return {**status, "game_mode": True}
    return {"supported": None, "enabled": None, "game_mode": False}


# ---------------------------------------------------------------- screen brightness (EDID)

DRM_ROOT = "/sys/class/drm"
DECK_OLED_PEAK = 1000.0  # Valve's spec for the Steam Deck OLED panel's HDR peak

# gamescope ignores some built-in panels' EDID luminance and uses its own profile
# (scripts/00-gamescope/displays/*.lua in ValveSoftware/gamescope). Games see these
# values, so RenoDX should too. The Deck OLED's EDID, for one, says ~604 nits.
GAMESCOPE_PANELS = [
    {"vendor": "VLV", "product": 0x3003, "name": "Steam Deck OLED (SDC)", "peak_nits": 1000.0, "avg_nits": 800.0},
    {"vendor": "VLV", "product": 0x3004, "name": "Steam Deck OLED (BOE)", "peak_nits": 1000.0, "avg_nits": 800.0},
    {"vendor": "ZDZ", "model": "ZDZ0501", "name": "Zotac Zone AMOLED", "peak_nits": 993.0, "avg_nits": 400.0},
    {"vendor": "DXQ", "model": "DXQ7D0023", "name": "Zotac Zone AMOLED", "peak_nits": 993.0, "avg_nits": 400.0},
    {"vendor": "YHB", "model": "YHB02P25", "name": "OneXPlayer F1 OLED", "peak_nits": 687.4, "avg_nits": 400.0},
]


def _luminance(code: int) -> float:
    """CTA-861 HDR static metadata: max / max-frame-average luminance code value -> nits."""
    return round(50.0 * 2 ** (code / 32.0), 1)


def parse_edid(edid: bytes) -> dict[str, Any]:
    """Monitor name and HDR static metadata (peak, full-frame average, min nits) from an EDID."""
    info: dict[str, Any] = {"name": "", "vendor": "", "product": 0, "peak_nits": None, "avg_nits": None, "min_nits": None}
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return info
    packed = (edid[8] << 8) | edid[9]
    info["vendor"] = "".join(chr(64 + ((packed >> shift) & 0x1F)) for shift in (10, 5, 0))
    info["product"] = edid[10] | (edid[11] << 8)
    for offset in (54, 72, 90, 108):
        descriptor = edid[offset:offset + 18]
        if descriptor[:3] == b"\x00\x00\x00" and descriptor[3] == 0xFC:
            info["name"] = descriptor[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
    for start in range(128, len(edid) - 127, 128):
        block = edid[start:start + 128]
        if block[0] != 0x02:  # CTA-861 extension
            continue
        end, pos = min(block[2], 127), 4
        while pos < end:
            tag, length = block[pos] >> 5, block[pos] & 0x1F
            payload = block[pos + 1:pos + 1 + length]
            if tag == 7 and length >= 3 and payload[0] == 6:  # extended tag 6: HDR static metadata
                values = payload[3:]
                if len(values) >= 1 and values[0]:
                    info["peak_nits"] = _luminance(values[0])
                if len(values) >= 2 and values[1]:
                    info["avg_nits"] = _luminance(values[1])
                if len(values) >= 3 and info["peak_nits"]:
                    info["min_nits"] = round(info["peak_nits"] * (values[2] / 255.0) ** 2 / 100.0, 4)
            pos += 1 + length
    return info


def gamescope_profile(info: dict[str, Any]) -> dict[str, Any] | None:
    for panel in GAMESCOPE_PANELS:
        if panel["vendor"] != info.get("vendor"):
            continue
        if "product" in panel and panel["product"] != info.get("product"):
            continue
        if "model" in panel and panel["model"] != info.get("name"):
            continue
        return panel
    return None


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _is_deck_oled() -> bool:
    return _read("/sys/class/dmi/id/product_name") == "Galileo"


def screen_info(drm_root: str = DRM_ROOT) -> dict[str, Any]:
    """The screen games are shown on and its HDR brightness, from the kernel's EDID copy.

    Docked, the connected external screen wins over the built-in one (gamescope shows
    games on one output). Returns peak_nits None when nothing usable is reported.
    """
    outputs = []
    for path in sorted(glob.glob(os.path.join(drm_root, "card*-*"))):
        if _read(os.path.join(path, "status")) != "connected":
            continue
        try:
            with open(os.path.join(path, "edid"), "rb") as handle:
                edid = handle.read()
        except OSError:
            edid = b""
        connector = os.path.basename(path).split("-", 1)[1]
        internal = connector.startswith(("eDP", "LVDS", "DSI"))
        outputs.append((internal, connector, parse_edid(edid)))
    if not outputs:
        return {"connector": "", "name": "", "peak_nits": None, "source": ""}
    internal, connector, info = sorted(outputs, key=lambda item: item[0])[0]  # external first
    result = {"connector": connector, "internal": internal, **info, "source": "EDID" if info["peak_nits"] else ""}
    result["edid_peak_nits"] = info["peak_nits"]
    profile = gamescope_profile(info)
    if profile:
        result.update(name=profile["name"], peak_nits=profile["peak_nits"], avg_nits=profile["avg_nits"], source="gamescope panel profile")
        return result
    if internal and not info["peak_nits"] and _is_deck_oled():
        result.update(name=info["name"] or "Steam Deck OLED", peak_nits=DECK_OLED_PEAK, source="Steam Deck OLED spec")
    if internal and _is_deck_oled() and not info["name"]:
        result["name"] = "Steam Deck OLED"
    return result
