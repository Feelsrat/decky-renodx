import urllib.request
import urllib.parse
import json
import os
import re

class PCGamingWikiScraper:
    API_URL = "https://www.pcgamingwiki.com/w/api.php"

    def get_game_data(self, appid: str):
        """Fetch PCGamingWiki data via MediaWiki/Cargo API only.

        This intentionally never parses rendered HTML pages. Structured fields
        come from Cargo (`action=cargoquery`) and notes come from the MediaWiki
        revisions API (`action=query&prop=revisions`).
        """
        try:
            # Table Video for HDR info
            # Table Middleware for Special K info
            # Table Infobox_game for Steam AppID matching
            
            # Query 1: HDR Support
            hdr_query = {
                "action": "cargoquery",
                "format": "json",
                "tables": "Infobox_game,Video",
                "fields": "Infobox_game._pageName=Page,Video.HDR",
                "join_on": "Infobox_game._pageName=Video._pageName",
                "where": f'Infobox_game.Steam_AppID HOLDS "{appid}"'
            }
            
            # Query 2: Special K / Middleware info
            sk_query = {
                "action": "cargoquery",
                "format": "json",
                "tables": "Infobox_game,Middleware",
                "fields": "Infobox_game._pageName=Page,Middleware.Middleware",
                "join_on": "Infobox_game._pageName=Middleware._pageName",
                "where": f'Infobox_game.Steam_AppID HOLDS "{appid}" AND Middleware.Middleware HOLDS "Special K"'
            }
            
            hdr_data = self._fetch(hdr_query)
            sk_data = self._fetch(sk_query)
            api_data = self._fetch_api_data(appid)
            
            result = {
                "appid": appid,
                "native_hdr": "unknown",
                "graphics_api": "unknown",
                "api_source": "",
                "api_page": "",
                "special_k_compatible": False,
                "special_k_notes": [],
                "special_k_delay_seconds": "0",
                "notes": []
            }
            
            if hdr_data and "cargoquery" in hdr_data and hdr_data["cargoquery"]:
                hdr_val = hdr_data["cargoquery"][0]["title"]["HDR"]
                result["native_hdr"] = hdr_val.lower() if hdr_val else "unknown"
                
            if sk_data and "cargoquery" in sk_data and sk_data["cargoquery"]:
                result["special_k_compatible"] = True

            api_info = self._parse_api_data(api_data)
            if api_info.get("api") and api_info["api"] != "unknown":
                result.update(api_info)

            page_name = ""
            if hdr_data and "cargoquery" in hdr_data and hdr_data["cargoquery"]:
                page_name = hdr_data["cargoquery"][0]["title"].get("Page", "")
            elif sk_data and "cargoquery" in sk_data and sk_data["cargoquery"]:
                page_name = sk_data["cargoquery"][0]["title"].get("Page", "")
            if page_name:
                notes = self._fetch_page_notes(page_name)
                result["special_k_notes"] = notes[:8]
                delay = self._extract_special_k_delay(notes)
                if delay:
                    result["special_k_delay_seconds"] = delay
                
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _fetch(self, params):
        url = f"{self.API_URL}?{urllib.parse.urlencode(params)}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "DeckyRenoDX/0.0.65"})
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode())
        except Exception:
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
            text = page.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("*", "")
        except Exception:
            return []
        lines = []
        for line in text.splitlines():
            clean = re.sub(r"<[^>]+>", "", line).strip()
            if re.search(r"special\s*k|skif|injection|delay|steamapi|hdr", clean, re.I):
                clean = re.sub(r"\{\{|\}\}|\[\[|\]\]", "", clean)
                clean = re.sub(r"\s+", " ", clean)
                if clean and clean not in lines:
                    lines.append(clean[:260])
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
