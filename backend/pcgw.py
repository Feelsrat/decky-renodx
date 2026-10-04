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
BATCH = 50  # games per bulk request; keeps the URL well under server limits


class PCGamingWiki:
    def __init__(self, cache_file: Path, fetch_json: Callable[..., Any]):
        self.cache_file = Path(cache_file)
        self._fetch_json = fetch_json
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._inflight: dict[str, threading.Event] = {}

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

    def _put(self, key: str, value: dict[str, Any], save: bool = True) -> None:
        self._load()[key] = {**value, "at": time.time()}
        if save:
            self._save()

    def _save(self) -> None:
        try:
            fsutil.write_json(self.cache_file, self._load())
        except OSError as error:
            log.plugin().warning("Could not write PCGamingWiki cache: %s", error)

    def cached(self, appid: str) -> dict[str, Any] | None:
        """What's already known about a game without a network request: the full lookup,
        else the bulk one (``prefetch``), else None."""
        with self._lock:
            entry = self._get(f"game:{appid}") or self._get(f"lite:{appid}")
            return {**entry} if entry is not None else None

    def prefetch(self, appids: list[str]) -> int:
        """Bulk-fetch HDR and engine for games without a cached answer, BATCH per request.
        For the library grid; the game page still does the full lookup. Returns how many
        games were looked up. Failures are cached briefly so they aren't retried at once."""
        with self._lock:
            wanted = [a for a in dict.fromkeys(map(str, appids)) if a.isdigit() and self._get(f"game:{a}") is None and self._get(f"lite:{a}") is None]
        for start in range(0, len(wanted), BATCH):
            chunk = wanted[start:start + BATCH]
            try:
                found = self._bulk(chunk)
            except Exception as error:
                log.plugin().info("PCGamingWiki bulk lookup failed: %s", error)
                found = {appid: {"error": str(error)} for appid in chunk}
            with self._lock:
                for appid in chunk:
                    self._put(f"lite:{appid}", found.get(appid) or {"page_name": "", "native_hdr": "unknown", "engine": ""}, save=False)
                self._save()
        return len(wanted)

    def _bulk(self, appids: list[str]) -> dict[str, dict[str, Any]]:
        where = " OR ".join(f'Infobox_game.Steam_AppID HOLDS "{appid}"' for appid in appids)
        data = self._query({
            "action": "cargoquery", "tables": "Infobox_game,Video", "join_on": "Infobox_game._pageName=Video._pageName",
            "fields": "Infobox_game._pageName=Page,Infobox_game.Steam_AppID=AppIDs,Infobox_game.Engines=Engines,Video.HDR=HDR",
            "where": where, "limit": "500",
        })
        if isinstance(data, dict) and data.get("error"):
            raise ValueError(str(data["error"].get("info") if isinstance(data["error"], dict) else data["error"]))
        wanted, found = set(appids), {}
        for row in (data or {}).get("cargoquery") or []:
            title = row.get("title") or {}
            hdr = str(title.get("HDR") or "").lower() or "unknown"
            engine = str(title.get("Engines") or "").replace("Engine:", "").split(",")[0].strip()
            for appid in re.split(r"[,\s]+", str(title.get("AppIDs") or "")):
                if appid in wanted:
                    found[appid] = {"page_name": title.get("Page", ""), "native_hdr": hdr, "engine": engine}
        return found

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
            if self.cache_file.exists():
                self.cache_file.unlink()

    # ------------------------------------------------------------ public
    def game_data(self, appid: str) -> dict[str, Any]:
        """HDR/API/engine/Special K hints for a Steam app. Never raises.

        Network requests run outside the shared lock, so a slow lookup for one
        game never holds up another; concurrent calls for the same game share one fetch.
        """
        key = f"game:{appid}"
        with self._lock:
            cached = self._get(key)
            if cached is not None:
                return {**cached}
            pending = self._inflight.get(key)
            if pending is None:
                pending = self._inflight[key] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            pending.wait(timeout=REQUEST_TIMEOUT * 6)
            with self._lock:
                return {**(self._get(key) or {"error": "PCGamingWiki lookup still running"})}
        try:
            data = self._game_data(str(appid))
        except Exception as error:
            log.plugin().info("PCGamingWiki lookup failed for %s: %s", appid, error)
            data = {"error": str(error)}  # cached for ERROR_TTL so it isn't retried on every refresh
        with self._lock:
            self._put(key, data)
            self._inflight.pop(key, None)
        pending.set()
        return {**data}

    @staticmethod
    def page_url(page_name: str) -> str:
        return f"https://www.pcgamingwiki.com/wiki/{urllib.parse.quote(page_name.replace(' ', '_'))}" if page_name else ""

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
        return result


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

