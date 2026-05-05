class DecisionTree:
    def __init__(self, renodx_mods=None):
        # renodx_mods could be a list of supported games for RenoDX/Luma
        self.renodx_mods = renodx_mods or []

    def evaluate(self, context: dict):
        """
        Evaluate game context and return a scored list of recommendations.
        Context should include:
        - appid, title
        - graphics_api (dx9, dx11, dx12, etc.)
        - anti_cheat (list of detected ACs)
        - is_multiplayer (bool)
        - native_hdr (wiki status)
        - special_k_wiki (bool)
        """
        appid = context.get("appid")
        title = context.get("title", "")
        graphics_api = context.get("graphics_api", "unknown")
        anti_cheat = context.get("anti_cheat", [])
        is_multiplayer = context.get("is_multiplayer", False)
        native_hdr = context.get("native_hdr", "unknown")
        special_k_wiki = context.get("special_k_wiki", False)
        
        recommendations = []

        # 1. Hard Blocks for Anti-Cheat or Multiplayer
        if anti_cheat or is_multiplayer:
            return [{
                "method": "sdr",
                "score": 0,
                "reason": "Anti-cheat or online multiplayer detected. Injection is unsafe.",
                "confidence": "high",
                "blocked": ["renodx", "special_k", "reshade"],
                "notes": [f"Detected Anti-Cheat: {', '.join(anti_cheat)}" if anti_cheat else "Multiplayer detected."]
            }]

        # 2. Native HDR (Score 100)
        if native_hdr in ["true", "limited", "good"]:
            recommendations.append({
                "method": "native_hdr",
                "score": 100,
                "reason": "Game has native HDR support.",
                "confidence": "high",
                "notes": [f"PCGamingWiki status: {native_hdr}"]
            })

        # 3. RenoDX / Luma (Score 90)
        if self._is_renodx_supported(title, appid):
            recommendations.append({
                "method": "renodx",
                "score": 90,
                "reason": "Exact RenoDX/Luma mod found for this game.",
                "confidence": "high"
            })

        # 4. Special K (Score 75)
        sk_notes = []
        sk_eligible = False
        
        if special_k_wiki:
            sk_notes.append("PCGamingWiki confirms Special K compatibility.")
            sk_eligible = True
        elif graphics_api in ["dx11", "dx12"]:
            sk_notes.append("Modern DirectX API detected (DX11/DX12).")
            sk_eligible = True
        elif graphics_api == "dx9":
            sk_notes.append("DX9 detected. Special K HDR typically requires a wrapper (e.g., d3d9.dll wrapper).")
            sk_eligible = False
            
        if sk_eligible:
            recommendations.append({
                "method": "special_k",
                "score": 75,
                "reason": "Special K provides advanced HDR retrofitting.",
                "confidence": "medium",
                "notes": sk_notes
            })

        # 5. ReShade AutoHDR (Score 50)
        if graphics_api in ["dx10", "dx11", "dx12"]:
            recommendations.append({
                "method": "reshade",
                "score": 50,
                "reason": "ReShade AutoHDR is a safe general-purpose fallback.",
                "confidence": "medium"
            })

        # 6. Fallback SDR (Score 0)
        recommendations.append({
            "method": "sdr",
            "score": 0,
            "reason": "Standard Dynamic Range fallback.",
            "confidence": "high"
        })

        # Sort by score descending
        recommendations.sort(key=lambda x: x["score"], reverse=True)
        return recommendations

    def _is_renodx_supported(self, title, appid):
        # Implementation depends on how we fetch the RenoDX mod list
        # For now, placeholder
        return False
