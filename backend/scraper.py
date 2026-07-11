import urllib.request
import urllib.parse
import json
import os
import re

class PCGamingWikiScraper:
    API_URL = "https://www.pcgamingwiki.com/w/api.php"

    def __init__(self, logger=None):
        self.logger = logger
        self._page_name_cache = {}
        self._improvements_cache = {}
        self._last_fetch_error = ""
        # Verified TLS first; unverified only as a fallback for devices whose
        # sandbox lacks a usable CA store.
        self._ssl_contexts = []
        try:
            import ssl
            try:
                self._ssl_contexts.append(ssl.create_default_context())
            except Exception:
                pass
            insecure = ssl.create_default_context()
            insecure.check_hostname = False
            insecure.verify_mode = ssl.CERT_NONE
            self._ssl_contexts.append(insecure)
        except Exception:
            pass

    def get_game_data(self, appid: str):
        """Fetch PCGamingWiki data via MediaWiki/Cargo API.

        Prioritizes getting a valid page name from Infobox_game, then fetches
        specific attributes from Video, Middleware, and API tables individually
        to avoid join-related empty results.
        """
        try:
            page_name = self._page_name_for_appid(appid)
            result = {
                "appid": appid,
                "page_name": page_name,
                "native_hdr": "unknown",
                "graphics_api": "unknown",
                "api_source": "",
                "api_page": "",
                "special_k_compatible": False,
                "special_k_notes": [],
                "special_k_delay_seconds": "0",
                "notes": []
            }

            if not page_name:
                # If we can't find by AppID, try to see if we have some API data via AppID directly
                api_data = self._fetch_api_data(appid)
                api_info = self._parse_api_data(api_data)
                if api_info.get("api") and api_info["api"] != "unknown":
                    result.update(api_info)
                return result

            # Fetch HDR Support
            hdr_query = {
                "action": "cargoquery",
                "format": "json",
                "tables": "Video",
                "fields": "HDR",
                "where": f'_pageName="{page_name}"'
            }
            hdr_data = self._fetch(hdr_query)
            if hdr_data and "cargoquery" in hdr_data and hdr_data["cargoquery"]:
                hdr_val = hdr_data["cargoquery"][0]["title"].get("HDR")
                result["native_hdr"] = hdr_val.lower() if hdr_val else "unknown"

            # Fetch Special K / Middleware info
            sk_query = {
                "action": "cargoquery",
                "format": "json",
                "tables": "Middleware",
                "fields": "Middleware",
                "where": f'_pageName="{page_name}" AND Middleware HOLDS "Special K"'
            }
            sk_data = self._fetch(sk_query)
            if sk_data and "cargoquery" in sk_data and sk_data["cargoquery"]:
                result["special_k_compatible"] = True

            # Fetch Engine info
            engine_query = {
                "action": "cargoquery",
                "format": "json",
                "tables": "Engine",
                "fields": "Engine",
                "where": f'_pageName="{page_name}"'
            }
            engine_data = self._fetch(engine_query)
            if engine_data and "cargoquery" in engine_data and engine_data["cargoquery"]:
                engine_val = engine_data["cargoquery"][0]["title"].get("Engine")
                if engine_val:
                    result["engine"] = engine_val

            # Fetch API data
            api_data = self._fetch({
                "action": "cargoquery",
                "format": "json",
                "tables": "API",
                "fields": "Direct3D_versions,OpenGL_versions,Vulkan_versions",
                "where": f'_pageName="{page_name}"',
            })
            api_info = self._parse_api_data(api_data)
            if api_info.get("api") and api_info["api"] != "unknown":
                result.update(api_info)
                result["api_page"] = page_name

            # Fetch notes and delay
            notes = self._fetch_page_notes(page_name)
            result["special_k_notes"] = notes[:8]
            delay = self._extract_special_k_delay(notes)
            if delay:
                result["special_k_delay_seconds"] = delay
                
            return result
        except Exception as e:
            if self.logger: self.logger.error(f"PCGW get_game_data error: {str(e)}")
            return {"status": "error", "message": str(e), "appid": appid}


    def get_improvements_and_issues(self, appid: str):
        try:
            cached = self._improvements_cache.get(str(appid))
            if cached:
                return cached
            page_name = self._page_name_for_appid(appid)
            if not page_name:
                detail = self._last_fetch_error or "No page mapping was returned by PCGamingWiki."
                return {"status": "error", "message": f"Could not resolve PCGamingWiki page for Steam AppID {appid}. {detail}"}
            text = self._fetch_page_wikitext(page_name)
            if not text:
                detail = self._last_fetch_error or "The page content response was empty."
                return {"status": "error", "message": f"Could not fetch wiki content for '{page_name}'. {detail}", "page_name": page_name}
            result = {
                "status": "success",
                "appid": appid,
                "page_name": page_name,
                "essential_improvements": self._extract_named_section(text, "Essential improvements"),
                "issues_fixed": self._extract_named_section(text, "Issues fixed"),
            }
            self._improvements_cache[str(appid)] = result
            return result
        except Exception as error:
            if self.logger: self.logger.error(f"PCGW get_improvements_and_issues error: {str(error)}")
            return {"status": "error", "message": str(error)}

    def _fetch(self, params):
        query_string = urllib.parse.urlencode(params)
        url = f"{self.API_URL}?{query_string}"
        self._last_fetch_error = ""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeckyRenoDX/1.0"}
        request = urllib.request.Request(url, headers=headers)
        for context in self._ssl_contexts or [None]:
            try:
                kwargs = {"timeout": 15}
                if context is not None:
                    kwargs["context"] = context
                with urllib.request.urlopen(request, **kwargs) as response:
                    return json.loads(response.read().decode("utf-8", "ignore"))
            except Exception as e:
                self._last_fetch_error = str(e)
                if self.logger: self.logger.warning(f"PCGW fetch failed for {url}: {str(e)}")
        return None


    def _fetch_api_data(self, appid: str):
        return self._fetch({
            "action": "cargoquery",
            "format": "json",
            "tables": "Infobox_game,API",
            "fields": "Infobox_game._pageName=Page,API.Direct3D_versions,API.OpenGL_versions,API.Vulkan_versions",
            "join_on": "Infobox_game._pageName=API._pageName",
            "where": f'Infobox_game.Steam_AppID HOLDS "{appid}"',
        })

    def _page_name_for_appid(self, appid: str):
        appid = str(appid)
        if appid in self._page_name_cache:
            return self._page_name_cache[appid]
        data = self._fetch({
            "action": "cargoquery",
            "format": "json",
            "tables": "Infobox_game",
            "fields": "Infobox_game._pageName=Page",
            "where": f'Infobox_game.Steam_AppID HOLDS "{appid}"',
            "limit": "1",
        })
        rows = (data or {}).get("cargoquery") or []
        if not rows:
            return ""
        page_name = rows[0].get("title", {}).get("Page", "")
        if page_name:
            self._page_name_cache[appid] = page_name
        return page_name

    def _parse_api_data(self, api_data):
        rows = (api_data or {}).get("cargoquery") or []
        if not rows:
            return {"api": "unknown"}
        title = rows[0].get("title", {})
        direct3d = str(title.get("Direct3D versions") or "")
        opengl = str(title.get("OpenGL versions") or "")
        vulkan = str(title.get("Vulkan versions") or "")
        api = self._api_from_pcgw_fields(direct3d, opengl, vulkan)
        return {
            "graphics_api": api,
            "api": api,
            "api_source": "pcgamingwiki_api_table",
            "api_page": title.get("Page", ""),
            "pcgw_direct3d_versions": direct3d,
            "pcgw_opengl_versions": opengl,
            "pcgw_vulkan_versions": vulkan,
        }

    def _api_from_pcgw_fields(self, direct3d: str, opengl: str, vulkan: str) -> str:
        d3d_versions = [int(match) for match in re.findall(r"\b(?:direct3d\s*)?([0-9]{1,2})\b", direct3d.lower())]
        if d3d_versions:
            version = max(d3d_versions)
            if version >= 12:
                return "d3d12"
            if version == 11:
                return "d3d11"
            if version == 10:
                return "dx10"
            if version == 9:
                return "d3d9"
            if version == 8:
                return "d3d8"
        if opengl.strip():
            return "opengl32"
        if vulkan.strip():
            return "vulkan"
        return "unknown"

    def _fetch_page_notes(self, page_name):
        text = self._fetch_page_wikitext(page_name)
        lines = []
        for line in text.splitlines():
            clean = re.sub(r"<[^>]+>", "", line).strip()
            if re.search(r"special\s*k|skif|injection|delay|steamapi|hdr", clean, re.I):
                clean = re.sub(r"\{\{|\}\}|\[\[|\]\]", "", clean)
                clean = re.sub(r"\s+", " ", clean)
                if clean and clean not in lines:
                    lines.append(clean[:260])
        return lines

    def _fetch_page_wikitext(self, page_name):
        params = {
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": page_name,
        }
        data = self._fetch(params)
        try:
            pages = data.get("query", {}).get("pages", {})
            page = next(iter(pages.values()))
            return page.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("*", "")
        except Exception:
            return ""

    def _extract_named_section(self, text: str, section_name: str):
        if not text:
            return []
        heading = re.search(rf"(?im)^(=+)\s*{re.escape(section_name)}\s*\1\s*$", text)
        if not heading:
            return []
        level = len(heading.group(1))
        next_heading = re.search(rf"(?im)^={{1,{level}}}\s*[^=\n].*={{1,{level}}}\s*$", text[heading.end():])
        body = text[heading.end(): heading.end() + next_heading.start()] if next_heading else text[heading.end():]
        return self._summarize_wiki_section(body)

    def _summarize_wiki_section(self, body: str):
        lines = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("{{ii}}") or line.startswith("{{ii "):
                continue
            if line.startswith("==="):
                clean = line.strip("= ").strip()
            elif line.startswith(("*", "#", ";", ":")):
                clean = line.lstrip("*#;: ").strip()
            elif "{{Fixbox" in line or "{{ii" in line:
                clean = line
            else:
                continue
            clean = re.sub(r"\{\{([^|{}]+)\|([^{}]+)\}\}", r"\2", clean)
            clean = re.sub(r"\{\{|\}\}|\[\[|\]\]", "", clean)
            clean = re.sub(r"<[^>]+>", "", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and clean not in lines:
                lines.append(clean[:320])
            if len(lines) >= 80:
                break
        return lines

    def _extract_special_k_delay(self, notes):
        text = " ".join(notes)
        if not re.search(r"special\s*k|injection|skif", text, re.I):
            return ""
        match = re.search(r"(?:delay|delayed|wait)[^0-9]{0,40}(\d{1,2})\s*(?:s|sec|second)", text, re.I)
        if match:
            return str(min(30, max(0, int(match.group(1)))))
        if re.search(r"delayed\s+injection|injection\s+delay", text, re.I):
            return "10"
        return ""

class AntiCheatDetector:
    # Common anti-cheat file signatures
    SIGNATURES = {
        "EasyAntiCheat": ["EasyAntiCheat.exe", "EasyAntiCheat_EOS.exe", "EasyAntiCheat.sys"],
        "BattlEye": ["BEService.exe", "BEService_x64.exe", "BEDaisy.sys"],
        "Vanguard": ["vgk.sys", "vgc.exe"],
        "GameGuard": ["GameGuard.des", "npggsvc.exe"],
        "XignCode3": ["x3.xem", "xhunter1.sys"],
        "DenuvoAntiCheat": ["denuvo-anti-cheat.sys"],
        "TencentACE": ["ACE-BASE.sys", "ACE-GAME.sys"]
    }

    def detect(self, game_path: str):
        """Scan game directory for known anti-cheat files."""
        if not os.path.exists(game_path):
            return []
            
        detected = []
        for ac_name, files in self.SIGNATURES.items():
            for root, dirs, filenames in os.walk(game_path):
                # Optimization: don't go too deep if we already found it
                if any(f in filenames for f in files):
                    detected.append(ac_name)
                    break
                    
        return list(set(detected))

    def is_multiplayer(self, game_path: str):
        """Heuristic check for multiplayer indicators in the game path/files."""
        # This is a bit vague, but we can look for "multiplayer", "online", etc.
        # or rely more on PCGamingWiki data.
        return False # Placeholder
