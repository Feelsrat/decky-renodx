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
        renodx_supported = context.get("renodx_supported", False)
        luma_supported = context.get("luma_supported", False)
        special_k_verified = context.get("special_k_verified", False)
        special_k_wrapper = context.get("special_k_wrapper", False)
        injection_dll = context.get("injection_dll", "auto")
        engine = context.get("engine", "unknown")
        
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
        renodx_flow_enabled = context.get("renodx_flow_enabled", False)
        if renodx_flow_enabled and (renodx_supported or luma_supported or self._is_renodx_supported(title, appid)):
            match = context.get("renodx_match", {}) or {}
            match_type = match.get("match_type", "specific")
            is_experimental = bool(context.get("renodx_experimental")) or match_type == "generic_engine"
            needs_manual_download = bool(match.get("manual_url")) and not bool(match.get("addon_url"))
            recommendations.append({
                "method": "renodx",
                "score": 90,
                "reason": (
                    "Experimental generic RenoDX engine addon is available for this engine."
                    if is_experimental
                    else "RenoDX/Luma mod found for this game."
                ),
                "confidence": "medium" if is_experimental else "high",
                "notes": [
                    f"RenoDX match: {match.get('name', title)}.",
                    f"Source: {match.get('source_type', 'unknown')}.",
                    f"Type: {match_type}.",
                ]
            })
            if not needs_manual_download:
                recommendations.append({
                    "method": "sdr",
                    "score": 0,
                    "reason": "Standard Dynamic Range fallback.",
                    "confidence": "high"
                })
                recommendations.sort(key=lambda x: x["score"], reverse=True)
                return recommendations
        elif renodx_supported or luma_supported:
            recommendations.append({
                "method": "renodx_disabled",
                "score": -1,
                "reason": "RenoDX/Luma support was detected, but the RenoDX install flow is temporarily disabled.",
                "confidence": "high",
                "state": "blocked",
                "notes": ["Use Special K or ReShade fallback for now."]
            })

        # 4. Special K (Score 75)
        sk_notes = []
        sk_eligible = False
        sk_requires_verification = False
        sk_attemptable_apis = {"dx10", "dx11", "dx12", "d3d10", "d3d11", "d3d12", "dx11_dx12", "dxgi"}
        
        if special_k_wiki or special_k_verified or special_k_wrapper:
            if special_k_wiki:
                sk_notes.append("PCGamingWiki confirms exact-game Special K HDR compatibility.")
            if special_k_verified:
                sk_notes.append("Special K HDR was verified for this game.")
            if special_k_wrapper:
                sk_notes.append("Known wrapper path exists for Special K HDR.")
            sk_eligible = True
        elif graphics_api in sk_attemptable_apis:
            sk_notes.append(f"{graphics_api} is a Special K-compatible API family.")
            sk_notes.append("Special K HDR still must be verified in-game before it is treated as working.")
            sk_eligible = True
            sk_requires_verification = True
        elif graphics_api in {"dx9", "d3d9"}:
            sk_notes.append("DX9 detected. Special K HDR requires exact-game support or a known wrapper path.")
            sk_eligible = False
            
        if sk_eligible:
            recommendations.append({
                "method": "special_k",
                "score": 75,
                "reason": (
                    "Special K can be attempted for this API family, but HDR support must be verified."
                    if sk_requires_verification
                    else "Special K provides advanced HDR retrofitting."
                ),
                "confidence": "medium" if not sk_requires_verification else "low",
                "state": "available",
                "requires_verification": sk_requires_verification,
                "notes": sk_notes
            })

        # 5. ReShade AutoHDR (Score 50)
        recommendations.append({
            "method": "reshade",
            "score": 50,
            "reason": "ReShade AutoHDR is the safe fallback when exact RenoDX/Luma or verified Special K HDR is unavailable.",
            "confidence": "medium" if graphics_api != "unknown" else "low",
            "notes": (
                [f"Detected API family: {graphics_api}.", f"Injection DLL: {injection_dll}.", f"Engine: {engine}."]
                if graphics_api != "unknown"
                else ["Graphics API is unknown; install will still attempt automatic DLL detection."]
            )
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
