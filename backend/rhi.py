"""Per-game RenoDX fixes from RHI's manifest.

RHI (ReShade HDR Installer, github.com/RankFTW/RHI) is the Windows RenoDX installer.
Its maintainers keep a manifest of wiki name fixes, addon URL fixes and per-game
warnings that it downloads on every launch. We read a few fields from it the same
way, so those fixes reach the Deck without a plugin release. It's optional: when
it can't be fetched or its format changes, matching just falls back to the wiki.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .remote import CachedJson
from .renodx import normalize_title

MANIFEST_URL = "https://raw.githubusercontent.com/RankFTW/RHI/main/manifest.json"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _for_deck(text: Any, url: Any = "") -> str:
    """Drop sentences that only make sense inside RHI's own UI, and leading emoji."""
    sentences = re.split(r"(?<=[.!?:])\s+|\n+", str(text or ""))
    kept = [s.strip() for s in sentences if s.strip() and not re.search(r"\bRHI\b|\bcog\b|Overrides|UE-Extended", s)]
    result = re.sub(r"^[\sℹ\u2600-\u27bf\U0001f000-\U0001faff\ufe0f]+", "", " ".join(kept)).strip()
    if result.endswith(":") and isinstance(url, str) and url.startswith("https://"):
        result = f"{result} {url}"
    return result


class RhiManifest:
    def __init__(self, cache_file: Path, fetch_text: Callable[[str], str]):
        self.source = CachedJson(cache_file, MANIFEST_URL, fetch_text,
                                 lambda data: isinstance(data, dict) and isinstance(data.get("wikiNameOverrides"), dict), "RHI manifest")
        self._index: dict[str, dict[str, Any]] = {}
        self._version = -1

    def _build(self, manifest: dict[str, Any]) -> None:
        index: dict[str, dict[str, Any]] = {}

        def entry(name: str) -> dict[str, Any]:
            return index.setdefault(normalize_title(name), {"aliases": [], "addon_url": "", "warnings": [], "manual_url": "", "manual_label": ""})

        for name, wiki_name in _dict(manifest.get("wikiNameOverrides")).items():
            if isinstance(wiki_name, str) and wiki_name.strip():
                entry(name)["aliases"].append(wiki_name)
        for name, url in _dict(manifest.get("snapshotOverrides")).items():
            if isinstance(url, str) and re.search(r"^https://.+\.addon(32|64)$", url):
                entry(name)["addon_url"] = url
        for name, warnings in _dict(manifest.get("installWarnings")).items():
            text = _for_deck(_dict(warnings).get("renodx", ""))
            if text:
                entry(name)["warnings"].append(text)
        for name, note in _dict(manifest.get("gameNotes")).items():
            text = _for_deck(_dict(note).get("notes", ""), _dict(note).get("notesUrl", ""))
            if text:
                entry(name)["warnings"].append(text)
        for name, values in _dict(manifest.get("renodxIniOverrides")).items():
            clean = {str(k): str(v) for k, v in _dict(values).items() if re.fullmatch(r"[A-Za-z0-9_]+", str(k)) and re.fullmatch(r"[0-9.]+", str(v))}
            if clean:
                entry(name)["ini"] = clean
        for name, external in _dict(manifest.get("forceExternalOnly")).items():
            url = str(_dict(external).get("url") or "")
            if url.startswith("https://"):
                entry(name).update(manual_url=url, manual_label=str(_dict(external).get("label") or ""))
        index.pop("", None)
        self._index = index

    def game(self, title: str) -> dict[str, Any]:
        """RHI's fixes for a game, looked up by its Steam name."""
        manifest = self.source.get()
        if self.source.version != self._version:
            self._version = self.source.version
            self._index = {}
            if manifest:
                self._build(manifest)
        return self._index.get(normalize_title(title), {})

    def clear(self) -> None:
        self.source.clear()


def apply(match: dict[str, Any] | None, fixes: dict[str, Any], title: str) -> dict[str, Any] | None:
    """Apply RHI's addon URL / download-page fixes to a wiki match."""
    if not fixes:
        return match
    if fixes.get("manual_url"):
        base = match or {"name": title, "status": "listed", "match_type": "specific", "bitness": "unknown", "notes": []}
        return {**base, "addon_url": "", "manual_url": fixes["manual_url"], "source": "rhi"}
    if fixes.get("addon_url"):
        url = fixes["addon_url"]
        base = match if match and match.get("match_type") == "specific" else {"name": title, "status": "listed", "match_type": "specific", "notes": []}
        return {**base, "addon_url": url, "manual_url": "", "bitness": "32" if url.endswith("32") else "64", "source": "rhi"}
    return match
