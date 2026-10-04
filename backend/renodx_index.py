"""RenoDX's own build index: every snapshot addon, keyed by Steam AppID.

Each RenoDX build repo publishes ``games-index.json`` next to its snapshot release,
generated from the ``metadata.json`` files in its source tree. It lists mods the wiki
doesn't (yet), and maps them to Steam AppIDs, so a Steam game can be matched exactly
instead of by name. The wiki stays the first choice when it lists the game, because it
carries status and notes; this index fills the gaps.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .remote import CachedJson
from .renodx import normalize_title

# Upstream first, then the forks that build their own mods (Unity games, OopyDoopy's mods).
REPOS = ["clshortfuse/renodx", "NotVoosh/renodx-unity", "OopyDoopy/renodx"]
ARCH = {"64": "x64", "32": "x86"}
STATUS = {"working": "working", "stable": "working", "in-progress": "in_progress", "beta": "in_progress", "experimental": "in_progress"}


def release_url(repo: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/snapshot/{name}"


def _valid(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("games"), list)


def _superseded(mod: dict[str, Any]) -> bool:
    return any("superseded" in str(note).lower() for note in mod.get("notes") or [])


class RenoDXIndex:
    def __init__(self, cache_dir: Path, fetch_text: Callable[[str], str]):
        self.sources = [
            (repo, CachedJson(Path(cache_dir) / f"renodx_index_{repo.replace('/', '_')}.json", release_url(repo, "games-index.json"), fetch_text, _valid, f"RenoDX index ({repo})"))
            for repo in REPOS
        ]
        self._versions: tuple[int, ...] = ()
        self._by_appid: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._by_title: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    def _indexes(self) -> None:
        documents = [(repo, source.get()) for repo, source in self.sources]
        versions = tuple(source.version for _repo, source in self.sources)
        if versions == self._versions:
            return
        self._versions = versions
        by_appid: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        by_title: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for repo, data in documents:
            for game in (data or {}).get("games", []):
                if not isinstance(game, dict) or not isinstance(game.get("mods"), list):
                    continue
                appid = game.get("steam_appid") or (game.get("deploy") or {}).get("steam_appid")
                if appid:
                    by_appid.setdefault(str(appid), []).append((repo, game))
                for title in [game.get("title"), *(game.get("aliases") or [])]:
                    if isinstance(title, str) and normalize_title(title):
                        by_title.setdefault(normalize_title(title), []).append((repo, game))
        self._by_appid, self._by_title = by_appid, by_title

    def has(self, appid: str = "", titles: list[str] | None = None) -> bool:
        """Cheap check for the game list: is any mod indexed for this game?"""
        self._indexes()
        return bool((appid and appid in self._by_appid) or any(normalize_title(t) in self._by_title for t in titles or []))

    def find(self, appid: str = "", titles: list[str] | None = None, arch: str = "") -> dict[str, Any] | None:
        """The best indexed mod for a game, shaped like a wiki entry; None if nothing fits."""
        self._indexes()
        games = list(self._by_appid.get(str(appid), [])) if appid else []
        for title in titles or []:
            games += [entry for entry in self._by_title.get(normalize_title(title), []) if entry not in games]
        wanted = ARCH.get(arch)
        candidates = []
        for order, (repo, game) in enumerate(games):
            for mod in game["mods"]:
                if not isinstance(mod, dict):
                    continue
                artifacts = [a for a in mod.get("artifacts") or [] if isinstance(a, dict) and str(a.get("name", "")).endswith((".addon64", ".addon32"))]
                if wanted:
                    artifacts = [a for a in artifacts if a.get("arch") == wanted]
                if not artifacts:
                    continue
                artifacts.sort(key=lambda a: a.get("arch") != "x64")
                engine = mod.get("category") == "engine" or mod.get("support") == "generic"
                rank = (mod.get("category") == "related", _superseded(mod), engine, mod.get("variant") is not None, order)
                candidates.append((rank, repo, game, mod, artifacts[0], engine))
        if not candidates:
            return None
        _rank, repo, game, mod, artifact, engine = min(candidates, key=lambda item: item[0])
        title = str(game.get("title") or mod.get("title") or "")
        name = f"{title} ({mod['variant']})" if mod.get("variant") else title
        url = release_url(repo, str(artifact["name"]))
        status = STATUS.get(str(mod.get("status") or ""), STATUS.get(str(mod.get("compatibility") or ""), "listed"))
        notes = [str(note) for note in [mod.get("summary"), *(mod.get("notes") or [])] if note]
        return {
            "name": name, "normalized": normalize_title(title), "maintainer": ", ".join(map(str, mod.get("maintainers") or [])) or repo.split("/")[0],
            "status": status, "notes": notes, "links": [url], "addon_url": url, "page_links": [], "manual_url": "",
            "source_type": "github_release", "bitness": "32" if artifact.get("arch") == "x86" else "64",
            "engine_bucket": "", "match_type": "generic_listed" if engine else "specific", "source": f"renodx_index:{repo}",
        }

    def clear(self) -> None:
        for _repo, source in self.sources:
            source.clear()
