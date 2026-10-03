"""The bundled/remote per-game compatibility database (compatibility.json)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import fsutil, log

SK_HOOKS = {"dxgi", "d3d11", "d3d9", "d3d8", "opengl32", "dinput8", "ddraw"}
FORCED_API = [("12", "d3d12"), ("11", "d3d11"), ("10", "d3d10"), ("9", "d3d9"), ("vulkan", "vulkan"), ("opengl", "opengl")]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, str) and value.strip() else []


REFRESH_AGE = 24 * 3600
RETRY_MIN, RETRY_MAX = 600, 6 * 3600


class CompatDB:
    def __init__(self, bundled: Path, cached: Path):
        self.bundled_path = Path(bundled)
        self.cached_path = Path(cached)
        self.data: dict[str, Any] = {"games": {}}
        self._next_try = 0.0
        self._retry_delay = RETRY_MIN
        self.reload()

    # Refresh timing uses the wall clock (time.time), not a long asyncio.sleep:
    # the monotonic clock stops while the Deck is suspended.
    def refresh_due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        try:
            fresh = now - self.cached_path.stat().st_mtime < REFRESH_AGE
        except OSError:
            fresh = False
        return not fresh and now >= self._next_try

    def refresh_failed(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._next_try = now + self._retry_delay
        self._retry_delay = min(self._retry_delay * 2, RETRY_MAX)

    def refresh_soon(self) -> None:
        self._next_try, self._retry_delay = 0.0, RETRY_MIN

    def reload(self) -> None:
        bundled = fsutil.read_json(self.bundled_path, {}) or {}
        cached = fsutil.read_json(self.cached_path, {}) or {}
        self.data = merge(bundled, cached)

    def accept_remote(self, text: str) -> int:
        """Validate and store a downloaded DB. Returns the number of games."""
        parsed = json.loads(text)
        if not (isinstance(parsed, dict) and isinstance(parsed.get("games"), dict) and len(parsed["games"]) >= 50):
            raise ValueError("remote compatibility.json failed the sanity check")
        fsutil.atomic_write_text(self.cached_path, text)
        self.reload()
        self.refresh_soon()
        return len(parsed["games"])

    # ------------------------------------------------------------ lookups
    def game(self, appid: str) -> dict[str, Any]:
        return _dict(_dict(self.data.get("games")).get(str(appid)))

    def tool(self, appid: str, name: str) -> dict[str, Any]:
        return _dict(_dict(self.game(appid).get("tools")).get(name))

    def automation(self, appid: str, name: str) -> dict[str, Any]:
        return _dict(self.tool(appid, name).get("automation"))

    def renodx_aliases(self, appid: str) -> list[str]:
        name = self.tool(appid, "renodx").get("name") or self.game(appid).get("name")
        return [str(name)] if name else []

    def game_args(self, appid: str, method: str) -> list[str]:
        """Extra arguments the game needs (placed after %command%)."""
        tool = "special_k" if method.startswith("special_k") else "renodx" if method == "renodx" else method
        return [str(item) for item in _list(self.tool(appid, tool).get("launch_options")) if str(item).strip()]

    def metadata(self, appid: str, method: str) -> dict[str, list[str]]:
        tool_name = "special_k" if method.startswith("special_k") else method
        tool = self.tool(appid, tool_name)
        automation = _dict(tool.get("automation"))
        warnings = [str(item) for item in _list(automation.get("warnings")) + _list(tool.get("warnings")) if str(item).strip()]
        steps = [str(item) for item in _list(automation.get("manual_steps")) + _list(tool.get("manual_steps")) if str(item).strip()]
        if tool_name == "renodx":
            for upgrade in _list(_dict(automation.get("renodx_settings")).get("upgrades")):
                upgrade = _dict(upgrade)
                if upgrade.get("format") and upgrade.get("mode"):
                    steps.append(f"In the RenoDX tab, set the {upgrade['format']} upgrade to \"{upgrade['mode']}\".")
        if tool_name == "special_k" and _dict(automation.get("hdr")).get("avoid"):
            warnings.append("Compatibility database marks Special K HDR as avoid for this game.")
        return {"warnings": list(dict.fromkeys(warnings)), "manual_steps": list(dict.fromkeys(steps))}

    def forced_api(self, appid: str) -> str:
        tool = self.tool(appid, "special_k")
        forced = str(_dict(tool.get("automation")).get("force_render_api") or tool.get("force_render_api") or "").lower()
        for needle, api in FORCED_API:
            if needle in forced:
                return api
        return ""

    def specialk_hook(self, appid: str) -> str:
        tool = self.tool(appid, "special_k")
        if not tool:
            return ""
        automation = _dict(tool.get("automation"))
        for candidate in (
            _dict(automation.get("local_dll")).get("target"),
            _dict(automation.get("addon_loader")).get("special_k_target"),
            automation.get("special_k_target"), automation.get("target_dll"), automation.get("injection_dll"),
            tool.get("special_k_target"), tool.get("target_dll"), tool.get("injection_dll"),
        ):
            if candidate:
                name = Path(str(candidate)).stem.lower()
                if name in SK_HOOKS:
                    return name
        for section in _dict(tool.get("special_k_ini_tweaks")):
            lower = str(section).lower()
            for hook in ("d3d9", "d3d11", "dxgi"):
                if hook in lower:
                    return hook
        return ""

    def specialk_subdir(self, appid: str) -> str:
        relative = str(_dict(self.automation(appid, "special_k").get("local_dll")).get("relative_path") or "").strip()
        if not relative or relative.lower() in {"<path-to-game>", ".", "./"}:
            return ""
        relative = relative.replace("\\", "/").strip("/")
        return "" if ".." in relative.split("/") else relative

    def specialk_ini_tweaks(self, appid: str) -> dict[str, dict[str, str]]:
        tweaks = _dict(self.tool(appid, "special_k").get("special_k_ini_tweaks"))
        return {str(section): {str(k): str(v) for k, v in _dict(values).items()} for section, values in tweaks.items()}

    def specialk_avoid_hdr(self, appid: str) -> bool:
        return bool(_dict(self.automation(appid, "special_k").get("hdr")).get("avoid"))

    def specialk_local_gate(self, appid: str) -> dict[str, Any]:
        if not self.tool(appid, "special_k"):
            return {"available": True, "reason": "No compatibility notes; Special K HDR must be checked in game."}
        automation = self.automation(appid, "special_k")
        preferred = str(automation.get("preferred_injection") or "").lower()
        local_dll = _dict(automation.get("local_dll"))
        avoid_modes = [str(item).lower() for item in _list(automation.get("avoid_injection_modes"))]
        hardware = str(automation.get("hardware_requirement") or "")
        if _dict(automation.get("hdr")).get("avoid"):
            return {"available": False, "reason": "Compatibility database says to avoid Special K HDR for this game."}
        if hardware and hardware.lower() != "steam deck":
            return {"available": False, "reason": f"Special K needs {hardware} for this game."}
        if automation.get("required_wrapper"):
            return {"available": False, "reason": f"Needs an unsupported wrapper: {', '.join(map(str, _list(automation.get('required_wrapper'))))}."}
        if automation.get("required_files"):
            files = ", ".join(str(_dict(item).get("file", item)) for item in _list(automation.get("required_files")))
            return {"available": False, "reason": f"Needs extra Special K files this plugin does not install: {files}."}
        if "local" in avoid_modes:
            return {"available": False, "reason": "Compatibility database says local Special K injection should be avoided."}
        if automation.get("avoid_injection_at_launch") and not local_dll:
            return {"available": False, "reason": "Needs Special K injected after launch (global injector), which this plugin doesn't set up. See the game's notes."}
        if preferred.startswith("global") and "local" not in preferred and not local_dll:
            return {"available": False, "reason": f"Needs {preferred.replace('_', ' ')} Special K injection, which this plugin doesn't set up. See the game's notes."}
        if automation.get("anti_cheat"):
            return {"available": False, "reason": "Needs anti-cheat changes before Special K is safe."}
        return {"available": True, "reason": "Local Special K install is allowed by the compatibility database."}


def merge(bundled: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    """Per-game merge: remote entries override bundled ones, bundled-only games are kept."""
    result = {key: value for key, value in bundled.items() if key != "games"}
    games = dict(_dict(bundled.get("games")))
    remote_games = _dict(cached.get("games"))
    games.update({appid: entry for appid, entry in remote_games.items() if isinstance(entry, dict)})
    for key, value in cached.items():
        if key != "games":
            result[key] = value
    result["games"] = games
    if remote_games:
        log.plugin().debug("Compat DB: %d bundled + %d remote entries", len(_dict(bundled.get("games"))), len(remote_games))
    return result
