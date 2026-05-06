import urllib.request
import urllib.parse
import json
import os
import re

class PCGamingWikiScraper:
    API_URL = "https://www.pcgamingwiki.com/w/api.php"

    def get_game_data(self, appid: str):
        """Fetch HDR and Special K data from PCGamingWiki via Cargo API."""
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
            
            result = {
                "appid": appid,
                "native_hdr": "unknown",
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
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode())
        except Exception:
            return None

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
            if re.search(r"special\s*k|skif|injection|delay|steamapi|dgvoodoo|hdr", clean, re.I):
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
