"""Whether the screen supports HDR and whether it is turned on, read from gamescope.

gamescope publishes GAMESCOPE_DISPLAY_SUPPORTS_HDR on its Xwayland root window,
and Steam sets GAMESCOPE_DISPLAY_HDR_ENABLED when the user enables HDR. Outside
Game Mode (or without xprop) both are unknown.
"""
from __future__ import annotations

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


def hdr_status(user: str) -> dict[str, Any]:
    xprop = shutil.which("xprop")
    if not xprop:
        return {"supported": None, "enabled": None, "game_mode": False}
    for display in (os.environ.get("DISPLAY", ""), ":0", ":1"):
        if not display:
            continue
        env = {**net.clean_env(), "DISPLAY": display}
        if user and user != "root":
            env.setdefault("XAUTHORITY", f"/home/{user}/.Xauthority")
        try:
            result = subprocess.run([xprop, "-root", *ATOMS.values()], capture_output=True, text=True, timeout=3, env=env)
        except (OSError, subprocess.TimeoutExpired):
            continue
        status = parse_xprop(result.stdout)
        if status["supported"] is not None:
            return {**status, "game_mode": True}
    return {"supported": None, "enabled": None, "game_mode": False}
