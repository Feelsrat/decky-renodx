"""Pure scoring of HDR methods for a game. No IO, so it is easy to test."""
from __future__ import annotations

from typing import Any

DX_MODERN = {"d3d10", "d3d11", "d3d12", "dxgi", "dx11_dx12"}
INSTALL_ORDER = ["renodx", "special_k", "special_k_delayed", "reshade"]


def _rec(method: str, score: int, reason: str, confidence: str, notes: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {"method": method, "score": score, "reason": reason, "confidence": confidence, "notes": notes or [], **extra}


def evaluate(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (recommendations sorted best-first, method options for the picker)."""
    anti_cheat = list(ctx.get("anti_cheat") or [])
    arch = str(ctx.get("architecture") or "unknown")
    api = str(ctx.get("api") or "unknown")
    engine = str(ctx.get("engine") or "unknown")
    native_hdr = str(ctx.get("native_hdr") or "unknown").lower()
    match = ctx.get("renodx_match") or {}
    exe_found = bool(ctx.get("exe_found"))
    sk_local = ctx.get("specialk_local_gate") or {"available": True, "reason": ""}
    sk_delayed = ctx.get("specialk_delayed_gate") or {"available": False, "reason": ""}

    blocked: dict[str, str] = {}
    if anti_cheat:
        reason = f"Anti-cheat detected ({', '.join(anti_cheat)}). Injection could get you banned."
        blocked.update({method: reason for method in INSTALL_ORDER})
    elif not exe_found:
        reason = "The game's Windows executable was not found."
        if ctx.get("linux_build"):
            reason = "Native Linux build detected. Force Proton in the game's compatibility settings, launch once, then refresh."
        blocked.update({method: reason for method in INSTALL_ORDER})

    recs: list[dict[str, Any]] = []
    notes_32 = ["32-bit game: HDR output through 32-bit DXVK is less tested; check the result in game."] if arch == "32" else []

    # RenoDX: a real per-game mod beats the game's own HDR; generic engine addons do not.
    renodx_reason = "No RenoDX mod matched this game."
    if match:
        generic = match.get("match_type") == "generic_engine"
        bitness = str(match.get("bitness") or "unknown")
        if bitness in {"32", "64"} and arch in {"32", "64"} and bitness != arch:
            blocked.setdefault("renodx", f"The matched RenoDX mod is {bitness}-bit but the game is {arch}-bit.")
        manual = not match.get("addon_url")
        renodx_reason = f"{'Experimental generic ' + engine.title() + ' addon' if generic else 'RenoDX mod'}: {match.get('name', '')}"
        if manual:
            renodx_reason += " (manual download)"
        recs.append(_rec(
            "renodx", 70 if generic else 95,
            "Experimental generic RenoDX addon for this engine." if generic else "A RenoDX mod exists for this game.",
            "medium" if generic else "high",
            [f"Match: {match.get('name', '')} ({match.get('status', 'listed')}).", *notes_32],
            renodx_status=match.get("status", "listed"), renodx_match_type=match.get("match_type", ""), manual_download=manual,
        ))
    else:
        blocked.setdefault("renodx", renodx_reason)

    if native_hdr in {"true", "limited", "good", "yes"}:
        recs.append(_rec("native_hdr", 85, "The game has native HDR. Turn it on in the game's settings.", "high", [f"PCGamingWiki HDR: {native_hdr}."]))

    # Special K
    sk_notes: list[str] = []
    sk_score = 0
    if ctx.get("specialk_avoid_hdr"):
        blocked.setdefault("special_k", "Compatibility database says to avoid Special K HDR for this game.")
    elif not sk_local.get("available", True):
        blocked.setdefault("special_k", str(sk_local.get("reason")))
    elif api == "vulkan":
        blocked.setdefault("special_k", "Special K HDR does not support Vulkan games.")
    elif ctx.get("specialk_verified") or ctx.get("specialk_wiki") or ctx.get("specialk_compat"):
        sk_score = 78
        if ctx.get("specialk_verified"):
            sk_notes.append("You marked Special K HDR as working for this game.")
        if ctx.get("specialk_wiki"):
            sk_notes.append("PCGamingWiki lists Special K for this game.")
        if ctx.get("specialk_compat"):
            sk_notes.append("The compatibility database has Special K settings for this game.")
    elif api in DX_MODERN:
        sk_score = 65
        sk_notes.append("DX10-12 games usually work with Special K HDR, but check it in game.")
    else:
        blocked.setdefault("special_k", f"Special K HDR needs a DX10-12 game or a verified entry (detected API: {api}).")
    if sk_score:
        recs.append(_rec("special_k", sk_score, "Special K can retrofit HDR into this game.", "medium" if sk_score > 70 else "low", sk_notes + notes_32))

    if sk_delayed.get("available"):
        recs.append(_rec("special_k_delayed", 60, str(sk_delayed.get("reason")), "low", ["Experimental."]))
    else:
        blocked.setdefault("special_k_delayed", str(sk_delayed.get("reason") or "Not needed for this game."))

    # ReShade AutoHDR
    if api == "vulkan":
        blocked.setdefault("reshade", "ReShade proxy DLLs cannot hook Vulkan games.")
    else:
        modern = api in DX_MODERN or api == "unknown"
        recs.append(_rec(
            "reshade", 50 if modern else 30,
            "ReShade AutoHDR shaders: a fallback when no RenoDX mod or Special K path exists." if modern
            else f"ReShade can load, but AutoHDR only works on DX10-12 ({api} detected).",
            "medium" if api != "unknown" else "low",
            [f"Detected API: {api}.", *notes_32],
        ))

    recs.append(_rec("sdr", 0, "Leave the game in SDR.", "high"))
    recs = [rec for rec in recs if rec["method"] not in blocked]
    recs.sort(key=lambda rec: rec["score"], reverse=True)
    if anti_cheat:
        recs[0] = {**recs[0], "reason": blocked["renodx"]} if recs[0]["method"] == "sdr" else recs[0]
    return recs, method_options(recs, blocked, ctx)


def method_options(recs: list[dict[str, Any]], blocked: dict[str, str], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    by_method = {rec["method"]: rec for rec in recs}
    match = ctx.get("renodx_match") or {}
    top = recs[0] if recs else None

    def option(method: str, label: str, reason: str, badge: str = "") -> dict[str, Any]:
        rec = by_method.get(method, {})
        return {
            "method": method,
            "label": label,
            "available": method not in blocked,
            "reason": blocked.get(method, reason),
            "badge": badge if method not in blocked else "",
            "score": rec.get("score"),
            "confidence": rec.get("confidence", ""),
        }

    renodx_badge = "Experimental" if match.get("match_type") == "generic_engine" else "Best"
    return [
        option("recommended", "Recommended", top["reason"] if top else "No recommendation.", ""),
        option("renodx", "RenoDX", f"Mod: {match.get('name', '')}" if match else "", renodx_badge),
        option("special_k", "Special K", str((ctx.get("specialk_local_gate") or {}).get("reason") or "Special K HDR retrofit."), "Verified" if ctx.get("specialk_compat") else ""),
        option("special_k_delayed", "Special K Delayed", str((ctx.get("specialk_delayed_gate") or {}).get("reason") or ""), "Experimental"),
        option("reshade", "ReShade AutoHDR", "AutoHDR shader fallback.", "Fallback"),
        {"method": "native_hdr", "label": "Native HDR (no injection)", "available": True, "reason": f"PCGamingWiki HDR: {ctx.get('native_hdr', 'unknown')}.", "badge": "", "score": by_method.get("native_hdr", {}).get("score"), "confidence": ""},
        {"method": "sdr", "label": "SDR (remove injection)", "available": True, "reason": "Remove injected HDR files and launch options.", "badge": "", "score": 0, "confidence": ""},
    ]


def install_plan(recs: list[dict[str, Any]]) -> list[str]:
    """Methods the 'Recommended' install tries, in order, before giving up.

    If native HDR ranks above every remaining injection method, nothing is installed.
    """
    plan: list[str] = []
    for rec in recs:
        if rec["method"] in {"native_hdr", "sdr"}:
            break
        if rec["method"] in INSTALL_ORDER:
            plan.append(rec["method"])
    return plan
