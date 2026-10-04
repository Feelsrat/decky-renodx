"""RenoDX mod catalog: wiki parsing, caching and title matching."""
from __future__ import annotations

import re
import time
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import fsutil, log
from .config import RENODX_MODS_URL

CACHE_TTL = 24 * 3600
RETRY_AFTER = 15 * 60
CACHE_SCHEMA = 2

GENERIC_FALLBACK_URLS = {
    "unreal": "https://clshortfuse.github.io/renodx/renodx-unrealengine.addon64",
    "unity": "https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon64",
}
# Known mirrors per addon filename, tried when the wiki's link fails.
ADDON_MIRRORS: dict[str, list[str]] = {
    "renodx-ue-extended.addon64": ["https://marat569.github.io/renodx/renodx-ue-extended.addon64"],
    "renodx-unityengine.addon64": [
        "https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon64",
        "https://clshortfuse.github.io/renodx/renodx-unityengine.addon64",
    ],
    "renodx-unityengine.addon32": [
        "https://notvoosh.github.io/renodx-unity/renodx-unityengine.addon32",
        "https://clshortfuse.github.io/renodx/renodx-unityengine.addon32",
    ],
}

# Words that may trail a title without making it a different game.
EDITION_WORDS = {
    "goty", "game", "of", "the", "year", "edition", "complete", "definitive", "enhanced", "ultimate", "deluxe",
    "gold", "anniversary", "remastered", "remaster", "hd", "directors", "director", "cut", "special", "standard",
    "premium", "redux", "dx11", "dx12", "steam", "windows", "pc", "version", "classic",
}
STOP_WORDS = {"the", "a", "an"}


# ---------------------------------------------------------------- normalization

def title_words(title: str) -> list[str]:
    # Strip trademark signs first: NFKD would turn "™" into the letters "TM".
    text = re.sub(r"[™®©]", " ", title or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    text = re.sub(r"\([^)]*\)", " ", text)
    return [word for word in re.split(r"[^a-z0-9]+", text) if word and word not in STOP_WORDS]


def normalize_title(title: str) -> str:
    return "".join(title_words(title))


def core_title(title: str) -> str:
    """The title without trailing edition words ("... GOTY Edition", "... Remastered")."""
    words = title_words(title)
    while len(words) > 1 and words[-1] in EDITION_WORDS:
        words.pop()
    return "".join(words)


def _edition_suffix(words: list[str]) -> bool:
    return all(word in EDITION_WORDS for word in words)


def match_score(query: str, candidate: str) -> int:
    """100 exact, 85 edition-suffix variant of the same game, else 0.

    Prefix matches only count when the extra words are edition words, so
    "Doom" never matches "DOOM Eternal" and "Final Fantasy X" never matches
    "Final Fantasy XIII".
    """
    query_words, candidate_words = title_words(query), title_words(candidate)
    if not query_words or not candidate_words:
        return 0
    if "".join(query_words) == "".join(candidate_words):
        return 100
    shorter, longer = sorted((query_words, candidate_words), key=len)
    if longer[: len(shorter)] == shorter and _edition_suffix(longer[len(shorter):]):
        return 85
    return 0


# ---------------------------------------------------------------- wiki parsing

def strip_markdown(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r":(?:white_check_mark|construction):", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _valid_name(name: str) -> bool:
    return bool(name and name.lower() not in {"name", "game"} and not set(name) <= {":", "-"})


def _links(value: str) -> dict[str, Any]:
    links = [url.rstrip(".,") for url in re.findall(r"https?://[^\s)]+", value or "")]
    addon_re = re.compile(r"\.addon(?:32|64)?(?:$|[?#])")
    addon_links = [url for url in links if addon_re.search(url.lower())]
    snapshot_links = [
        url.rstrip(".,")
        for badge, url in re.findall(r"\[!\[([^\]]+)\]\([^)]+\)\]\((https?://[^)]+)\)", value or "", re.I)
        if "snapshot" in badge.lower() and addon_re.search(url.lower())
    ]
    if snapshot_links:
        addon_links = snapshot_links + [url for url in addon_links if url not in snapshot_links]
    page_links = [url for url in links if url not in addon_links]
    return {
        "links": links,
        "addon_url": addon_links[0] if addon_links else "",
        "page_links": page_links,
        "manual_url": page_links[0] if page_links and not addon_links else "",
    }


def _bitness(links: list[str]) -> str:
    text = " ".join(links).lower()
    has64 = ".addon64" in text or "64.addon" in text
    has32 = ".addon32" in text or "32.addon" in text
    return "both" if has64 and has32 else "64" if has64 else "32" if has32 else "unknown"


def _source_type(links: list[str]) -> str:
    lower = " ".join(links).lower()
    if not lower:
        return "unknown"
    if "github.com" in lower and "releases/download" in lower:
        return "github_release"
    if "github.io" in lower or ".addon" in lower:
        return "snapshot"
    if "nexusmods.com" in lower:
        return "nexus"
    if "discord." in lower:
        return "discord"
    return "page"


def _status(cell: str) -> str:
    lower = cell.lower()
    if "white_check_mark" in lower:
        return "working"
    if "construction" in lower:
        return "in_progress"
    return strip_markdown(cell) or "listed"


def _notes(cell: str) -> list[str]:
    return [strip_markdown(note) for note in re.findall(r'\]\(#\s*"([^"]+)"\)', cell or "") if strip_markdown(note)]


def engine_bucket(text: str) -> str:
    lower = (text or "").lower()
    if "unreal" in lower:
        return "unreal"
    if "unity" in lower:
        return "unity"
    return normalize_title(lower.replace("engine", ""))


def _table_rows(rows: list[str]) -> list[list[str]]:
    parsed = []
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if not cells or all(set(cell) <= {":", "-"} for cell in cells if cell):
            continue
        if cells[0].lower() in {"name", "game"}:
            continue
        parsed.append(cells)
    return parsed


def _mod(name: str, **fields: Any) -> dict[str, Any]:
    return {"name": name, "normalized": normalize_title(name), **fields}


def parse_mods(markdown: str) -> list[dict[str, Any]]:
    mods: list[dict[str, Any]] = []
    section, table, engine, engine_links = "", [], "", []

    def flush() -> None:
        nonlocal table
        if section == "specific":
            for cells in _table_rows(table):
                if len(cells) < 4 or not _valid_name(strip_markdown(cells[0])):
                    continue
                links = _links(cells[2])
                mods.append(_mod(
                    strip_markdown(cells[0]), maintainer=strip_markdown(cells[1]), status=_status(cells[3]), notes=_notes(cells[3]),
                    **links, source_type=_source_type(links["links"]), bitness=_bitness(links["links"]), engine_bucket="", match_type="specific",
                ))
        elif section == "multi" and engine:
            shared = _links(" ".join(engine_links))
            for cells in _table_rows(table):
                if len(cells) < 2 or not _valid_name(strip_markdown(cells[0])):
                    continue
                note = strip_markdown(cells[2]) if len(cells) > 2 else ""
                mods.append(_mod(
                    strip_markdown(cells[0]), maintainer="RenoDX", status=_status(cells[1]), notes=[note] if note else [],
                    **shared, source_type="generic", bitness=_bitness(shared["links"]), engine_bucket=engine, match_type="generic_listed",
                ))
            if shared["addon_url"] and not any(m["match_type"] == "generic_engine" and m["engine_bucket"] == engine for m in mods):
                mods.append(_mod(
                    f"Generic {engine.title()} Engine", maintainer="RenoDX", status="experimental",
                    notes=["Experimental generic engine install. Use only when no exact game entry exists."],
                    **shared, source_type="generic", bitness=_bitness(shared["links"]), engine_bucket=engine, match_type="generic_engine",
                ))
        table = []

    for raw in markdown.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^\s*(#{1,4})\s+(.+?)\s*$", line)
        if heading:
            flush()
            title = strip_markdown(heading.group(2)).lower()
            if title == "list":
                section, engine, engine_links = "specific", "", []
            elif title == "multi-game mods":
                section, engine, engine_links = "multi", "", []
            elif section == "multi" and "engine" in title:
                engine, engine_links = engine_bucket(title), re.findall(r"https?://[^\s)]+", line)
            elif title in {"related mods", "deprecated mods"}:
                section, engine, engine_links = "", "", []
            continue
        if line.lstrip().startswith("|"):
            table.append(line)
        elif table:
            flush()
        elif section == "multi" and engine and "http" in line:
            engine_links.extend(re.findall(r"https?://[^\s)]+", line))
    flush()
    return mods


def generic_fallback(engine: str) -> dict[str, Any]:
    bucket = engine_bucket(engine)
    url = GENERIC_FALLBACK_URLS.get(bucket, "")
    if not url:
        return {}
    return _mod(
        f"Generic {bucket.title()} Engine", maintainer="RenoDX", status="experimental",
        notes=["Experimental generic engine install. Use only when no exact game entry exists."],
        links=[url], addon_url=url, page_links=[], manual_url="", source_type="generic_fallback", bitness="64",
        engine_bucket=bucket, match_type="generic_engine",
    )


def addon_url_candidates(addon_url: str) -> list[str]:
    """Where to try downloading an addon, best first.

    RenoDX repos publish each snapshot both as a GitHub release and on GitHub Pages
    (<owner>.github.io/<repo>/). The release is tried first: Pages can lag behind or
    refuse large files (the generic Unity addon is over 100 MB).
    """
    parsed = urllib.parse.urlparse(addon_url)
    name = Path(urllib.parse.unquote(parsed.path)).name
    candidates = [addon_url]
    pages = re.fullmatch(r"([a-z0-9-]+)\.github\.io", parsed.netloc.lower())
    parts = [part for part in parsed.path.split("/") if part]
    if pages and len(parts) == 2 and re.search(r"\.addon(?:32|64)$", name.lower()):
        candidates.insert(0, f"https://github.com/{pages.group(1)}/{parts[0]}/releases/download/snapshot/{name}")
    candidates += ADDON_MIRRORS.get(name.lower(), [])
    if pages and re.search(r"\.addon(?:32|64)$", name.lower()):
        candidates.append(f"https://github.com/clshortfuse/renodx/releases/download/snapshot/{name}")
    return list(dict.fromkeys(candidates))


def addon_filename(addon_url: str, title: str) -> str:
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(addon_url).path)).name
    if not re.search(r"\.addon(?:32|64)?$", name.lower()):
        name = f"renodx-{normalize_title(title)}.addon{'32' if '32' in addon_url else '64'}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


# ---------------------------------------------------------------- catalog

class RenoDXCatalog:
    def __init__(self, cache_file: Path, fetch_text: Callable[[str], str]):
        self.cache_file = Path(cache_file)
        self._fetch_text = fetch_text
        self._mods: list[dict[str, Any]] | None = None
        self._fetched_at = 0.0
        self._retry_after = 0.0
        self._by_name: tuple[list[dict[str, Any]], dict[str, dict[str, Any]]] | None = None

    def mods(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Cached mod list; a stale cache is used when the wiki cannot be reached."""
        if self._mods is None:
            cached = fsutil.read_json(self.cache_file, {}) or {}
            if cached.get("schema") == CACHE_SCHEMA and isinstance(cached.get("mods"), list):
                self._mods, self._fetched_at = cached["mods"], float(cached.get("fetched_at", 0))
        now = time.time()
        fresh = self._mods is not None and now - self._fetched_at < CACHE_TTL
        if (fresh or (self._mods is not None and now < self._retry_after)) and not refresh:
            return self._mods or []
        try:
            mods = parse_mods(self._fetch_text(RENODX_MODS_URL))
            if not mods:
                raise ValueError("RenoDX mod list was empty")
            self._mods, self._fetched_at = mods, time.time()
            fsutil.write_json(self.cache_file, {"schema": CACHE_SCHEMA, "fetched_at": self._fetched_at, "mods": mods})
        except Exception as error:
            # Don't retry on every panel refresh; a slow or captive network would stall each one.
            self._retry_after = time.time() + RETRY_AFTER
            if self._mods is None:
                raise
            log.plugin().warning("RenoDX wiki unavailable, using cached list: %s", error)
        return self._mods or []

    def listed(self, titles: list[str]) -> dict[str, Any] | None:
        """Name lookup of a game-specific or engine-listed mod, ignoring edition suffixes; fast enough for a whole library."""
        mods = self.mods()
        if self._by_name is None or self._by_name[0] is not mods:
            listed = [m for m in mods if m.get("match_type") in {"specific", "generic_listed"} and m.get("normalized")]
            index = {core_title(m["name"]): m for m in listed}
            index.update({m["normalized"]: m for m in listed})  # exact names win
            self._by_name = (mods, index)
        index = self._by_name[1]
        keys = [normalize_title(t) for t in titles] + [core_title(t) for t in titles]
        return next((index[key] for key in keys if key and key in index), None)

    def clear(self) -> None:
        self._mods, self._fetched_at, self._retry_after = None, 0.0, 0.0
        if self.cache_file.exists():
            self.cache_file.unlink()

    def match(self, title: str, *, aliases: list[str] | None = None, engine: str = "", architecture: str = "") -> dict[str, Any] | None:
        """Best specific match for ``title`` (or a compat-DB alias), else a 64-bit generic engine addon."""
        mods = self.mods()
        best: tuple[int, int, dict[str, Any]] | None = None
        for name in [title, *(aliases or [])]:
            for mod in mods:
                if mod.get("match_type") == "generic_engine":
                    continue
                score = match_score(name, str(mod.get("name", "")))
                if score and (best is None or (score, len(mod["normalized"])) > (best[0], best[1])):
                    best = (score, len(mod["normalized"]), mod)
        if best is not None:
            return {**best[2], "score": best[0]}
        if architecture != "64":
            return None
        bucket = engine_bucket(engine)
        if bucket not in {"unreal", "unity"}:
            return None
        generic = next((m for m in mods if m.get("match_type") == "generic_engine" and m.get("engine_bucket") == bucket), None)
        generic = generic or generic_fallback(bucket)
        return {**generic, "score": 62, "experimental": True} if generic else None


# ---------------------------------------------------------------- settings from wiki notes

UPGRADE_MODES = {"off": 0, "output size": 1, "output ratio": 2, "any size": 3}
_FORMAT = re.compile(r"^[A-Z][A-Z0-9]*_[A-Z0-9_]+$")
FORMAT_TYPOS = {"R8G8R8A8_TYPELESS": "R8G8B8A8_TYPELESS"}  # as written in a wiki note


def upgrade_settings(notes: list[str]) -> dict[str, str]:
    """RenoDX resource-upgrade settings named in wiki notes, as ReShade.ini keys.

    Notes look like "`B8G8R8A8_TYPELESS` `Output Size`" or "`Upgrade Copy Destinations` `On`".
    Pairs marked optional or conditional ("if black screen occurs ...") are left alone. When a
    note offers a wider alternative ("`Output Size` for 100% render resolution or `Output Ratio`
    for other percentages"), the wider one is used: it covers both cases.
    """
    result: dict[str, str] = {}
    for note in notes or []:
        tokens = [(m.group(1).strip(), m.end()) for m in re.finditer(r"`([^`]+)`", note)]
        last_key = ""
        i = 0
        while i < len(tokens):
            text, end = tokens[i]
            nxt = tokens[i + 1][0] if i + 1 < len(tokens) else ""
            tail = note[tokens[i + 1][1]:].split("`", 1)[0].lower() if i + 1 < len(tokens) else ""
            if (_FORMAT.match(text) or text.lower() == "upgrade copy destinations") and (nxt.lower() in UPGRADE_MODES or nxt.lower() in {"on", "off"}):
                key = "Upgrade_CopyDestinations" if text.lower() == "upgrade copy destinations" else f"Upgrade_{FORMAT_TYPOS.get(text, text)}"
                value = {"on": 1, "off": 0}.get(nxt.lower(), UPGRADE_MODES.get(nxt.lower()))
                if "optional" in tail or re.search(r"\bif\b", tail):
                    last_key = ""
                else:
                    result[key] = str(value)
                    last_key = key
                i += 2
                continue
            if text.lower() in UPGRADE_MODES and last_key and last_key != "Upgrade_CopyDestinations":
                # A bare mode after a pair is an alternative for the same format: keep the wider one.
                result[last_key] = str(max(int(result[last_key]), UPGRADE_MODES[text.lower()]))
            i += 1
    return result


def needs_engine_ini(notes: list[str]) -> str:
    """'full' when a note asks for the wiki's Engine.ini block, 'output' for "only add
    r.HDR.EnableHDROutput=1", '' otherwise (including notes that warn against it)."""
    for note in notes or []:
        lower = note.lower()
        if not lower.startswith("engine.ini"):
            continue
        return "output" if "only add" in lower and "enablehdroutput" in lower else "full"
    return ""
