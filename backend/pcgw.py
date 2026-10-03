"""PCGamingWiki lookups, cached on disk so a recommendation costs at most one round of requests."""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import fsutil, log

API_URL = "https://www.pcgamingwiki.com/w/api.php"
CACHE_TTL = 7 * 86400
ERROR_TTL = 3600
REQUEST_TIMEOUT = 8


class PCGamingWiki:
    def __init__(self, cache_file: Path, fetch_json: Callable[..., Any]):
        self.cache_file = Path(cache_file)
        self._fetch_json = fetch_json
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    # ------------------------------------------------------------ cache
    def _load(self) -> dict[str, Any]:
        if self._cache is None:
            data = fsutil.read_json(self.cache_file, {}) or {}
            self._cache = data if isinstance(data, dict) else {}
        return self._cache

    def _get(self, key: str) -> Any:
        entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        ttl = ERROR_TTL if entry.get("error") else CACHE_TTL
        if time.time() - float(entry.get("at", 0)) > ttl:
            return None
        return entry

    def _put(self, key: str, value: dict[str, Any]) -> None:
        cache = self._load()
        cache[key] = {**value, "at": time.time()}
        try:
            fsutil.write_json(self.cache_file, cache)
        except OSError as error:
            log.plugin().warning("Could not write PCGamingWiki cache: %s", error)

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
            if self.cache_file.exists():
                self.cache_file.unlink()

    # ------------------------------------------------------------ public
    def game_data(self, appid: str) -> dict[str, Any]:
        """HDR/API/engine/Special K hints for a Steam app. Never raises."""
        key = f"game:{appid}"
        with self._lock:
            cached = self._get(key)
            if cached is not None:
                return cached
            try:
                data = self._game_data(str(appid))
            except Exception as error:
                log.plugin().info("PCGamingWiki lookup failed for %s: %s", appid, error)
                data = {"error": str(error)}
            self._put(key, data)
            return {**data}

    def improvements(self, appid: str) -> dict[str, Any]:
        data = self.game_data(appid)
        if data.get("error"):
            return {"status": "error", "message": f"PCGamingWiki unavailable: {data['error']}"}
        if not data.get("page_name"):
            return {"status": "error", "message": f"No PCGamingWiki page is linked to Steam AppID {appid}."}
        return {
            "status": "success",
            "page_name": data["page_name"],
            "essential_improvements": data.get("essential_improvements", []),
            "issues_fixed": data.get("issues_fixed", []),
        }

    # ------------------------------------------------------------ fetching
    def _query(self, params: dict[str, str]) -> Any:
        url = f"{API_URL}?{urllib.parse.urlencode({'format': 'json', **params})}"
        return self._fetch_json(url, timeout=REQUEST_TIMEOUT)

    def _cargo(self, table: str, fields: str, page: str) -> dict[str, Any]:
        safe_page = page.replace('"', '\\"')
        data = self._query({"action": "cargoquery", "tables": table, "fields": fields, "where": f'_pageName="{safe_page}"', "limit": "1"})
        rows = (data or {}).get("cargoquery") or []
        return rows[0].get("title", {}) if rows else {}

    def _game_data(self, appid: str) -> dict[str, Any]:
        data = self._query({
            "action": "cargoquery", "tables": "Infobox_game", "fields": "Infobox_game._pageName=Page",
            "where": f'Infobox_game.Steam_AppID HOLDS "{appid}"', "limit": "1",
        })
        rows = (data or {}).get("cargoquery") or []
        page = rows[0].get("title", {}).get("Page", "") if rows else ""
        result: dict[str, Any] = {"page_name": page, "native_hdr": "unknown", "graphics_api": "unknown", "engine": "", "special_k": False}
        if not page:
            return result
        hdr = self._cargo("Video", "HDR", page).get("HDR")
        result["native_hdr"] = str(hdr).lower() if hdr else "unknown"
        engine = self._cargo("Infobox_game", "Engines", page).get("Engines")
        result["engine"] = str(engine or "").replace("Engine:", "").split(",")[0].strip()
        api = self._cargo("API", "Direct3D_versions,OpenGL_versions,Vulkan_versions", page)
        result["graphics_api"] = api_from_fields(str(api.get("Direct3D versions") or ""), str(api.get("OpenGL versions") or ""), str(api.get("Vulkan versions") or ""))
        middleware = self._query({
            "action": "cargoquery", "tables": "Middleware", "fields": "Middleware",
            "where": f'_pageName="{page.replace(chr(34), chr(92) + chr(34))}" AND Middleware HOLDS "Special K"', "limit": "1",
        })
        result["special_k"] = bool((middleware or {}).get("cargoquery"))
        text = self._wikitext(page)
        result["essential_improvements"] = extract_section(text, "Essential improvements")
        result["issues_fixed"] = extract_section(text, "Issues fixed")
        return result

    def _wikitext(self, page: str) -> str:
        data = self._query({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": page})
        try:
            pages = data.get("query", {}).get("pages", {})
            return next(iter(pages.values())).get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("*", "")
        except (AttributeError, StopIteration, IndexError):
            return ""


def api_from_fields(direct3d: str, opengl: str, vulkan: str) -> str:
    versions = [int(match) for match in re.findall(r"\b([0-9]{1,2})\b", direct3d)]
    if versions:
        version = max(versions)
        return {12: "d3d12", 11: "d3d11", 10: "d3d10", 9: "d3d9", 8: "d3d8"}.get(min(version, 12), "unknown")
    if opengl.strip():
        return "opengl"
    if vulkan.strip():
        return "vulkan"
    return "unknown"


def extract_section(text: str, name: str) -> list[str]:
    if not text:
        return []
    heading = re.search(rf"(?im)^(=+)\s*{re.escape(name)}\s*\1\s*$", text)
    if not heading:
        return []
    level = len(heading.group(1))
    following = re.search(rf"(?im)^={{1,{level}}}\s*[^=\n].*={{1,{level}}}\s*$", text[heading.end():])
    body = text[heading.end(): heading.end() + following.start()] if following else text[heading.end():]
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("{{ii"):
            continue
        if line.startswith("==="):
            clean = line.strip("= ")
        elif line.startswith(("*", "#", ";", ":")):
            clean = line.lstrip("*#;: ")
        elif "{{Fixbox" in line:
            clean = line
        else:
            continue
        clean = re.sub(r"\{\{([^|{}]+)\|([^{}]+)\}\}", r"\2", clean)
        clean = re.sub(r"\{\{|\}\}|\[\[|\]\]|<[^>]+>", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean and clean not in lines:
            lines.append(clean[:320])
        if len(lines) >= 80:
            break
    return lines
