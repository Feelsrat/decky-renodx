#!/usr/bin/env python3
"""Manage compatibility.json without hand-editing 400+ entries.

Commands:
  validate      Check the DB against the formal schema (non-zero exit on errors).
  format        Rewrite the DB in canonical form (sorted appids/keys, stable diffs).
  report        Coverage stats and entries that need attention.
  add           Add or update one game entry from the command line.
  sync-renodx   Fetch the RenoDX wiki list, find titles missing from the DB,
                resolve Steam AppIDs via the Steam store search API, and stage
                new entries. Dry-run by default; pass --write to apply.

Examples:
  python scripts/compat_db.py validate
  python scripts/compat_db.py report
  python scripts/compat_db.py add --appid 12345 --name "Some Game" --tool special_k \
      --notes "Needs Silent=true" --warning "Crashes with overlay" \
      --ini "Steam.Log.Silent=true" --delay 5
  python scripts/compat_db.py sync-renodx            # preview
  python scripts/compat_db.py sync-renodx --write    # apply + format + validate

The schema is documented in docs/COMPATIBILITY.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "compatibility.json"
RENODX_MODS_URL = "https://raw.githubusercontent.com/wiki/clshortfuse/renodx/Mods.md"
STORESEARCH_URL = "https://store.steampowered.com/api/storesearch/?cc=us&l=en&term="
KNOWN_TOOLS = {"renodx", "special_k"}
LIST_FIELDS = {"launch_options", "tags", "warnings", "manual_steps"}


# ---------------------------------------------------------------- utilities

def load_db(path: Path = DB_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_db(db: dict, path: Path = DB_PATH) -> None:
    """Write in canonical form: sorted keys, games ordered by numeric appid."""
    games = db.get("games", {})
    db["games"] = {k: games[k] for k in sorted(games, key=lambda a: (not a.isdigit(), int(a) if a.isdigit() else 0, a))}
    path.write_text(json.dumps(db, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def normalize_title(title: str) -> str:
    """Match main.py's _normalize_game_title so lookups behave identically."""
    title = unicodedata.normalize("NFKD", title)
    title = "".join(ch for ch in title if not unicodedata.combining(ch))
    title = title.lower().replace("™", "").replace("®", "")
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(r"\b(the|definitive edition|directors cut|director's cut|remastered|remake|dx10|dx11|dx12|steam only)\b", " ", title)
    return re.sub(r"[^a-z0-9]+", "", title)


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "decky-renodx-compat-db"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "ignore")


# ---------------------------------------------------------------- validate

def validate_db(db: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(db.get("schema_version"), int):
        errors.append("schema_version must be an integer")
    games = db.get("games")
    if not isinstance(games, dict) or not games:
        errors.append("games must be a non-empty object")
        return errors, warnings
    if not isinstance(db.get("tool_defaults"), dict):
        warnings.append("tool_defaults missing or not an object")

    seen_names: dict[str, str] = {}
    for appid, game in games.items():
        where = f"games[{appid}]"
        if not re.fullmatch(r"\d+", str(appid)):
            errors.append(f"{where}: appid must be numeric")
        if not isinstance(game, dict):
            errors.append(f"{where}: entry must be an object")
            continue
        name = game.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: name must be a non-empty string")
        else:
            normalized = normalize_title(name)
            if normalized in seen_names:
                warnings.append(f"{where}: name '{name}' normalizes identically to appid {seen_names[normalized]}")
            else:
                seen_names[normalized] = str(appid)
        for key in game:
            if key not in {"name", "tools"}:
                warnings.append(f"{where}: unexpected field '{key}'")
        tools = game.get("tools")
        if not isinstance(tools, dict) or not tools:
            errors.append(f"{where}: tools must be a non-empty object")
            continue
        for tool_name, tool in tools.items():
            twhere = f"{where}.tools.{tool_name}"
            if tool_name not in KNOWN_TOOLS:
                warnings.append(f"{twhere}: unknown tool (known: {sorted(KNOWN_TOOLS)})")
            if not isinstance(tool, dict):
                errors.append(f"{twhere}: must be an object")
                continue
            for field in LIST_FIELDS:
                value = tool.get(field)
                if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
                    errors.append(f"{twhere}.{field}: must be a list of strings")
            automation = tool.get("automation")
            if automation is not None:
                if not isinstance(automation, dict):
                    errors.append(f"{twhere}.automation: must be an object")
                else:
                    for field in ("warnings", "manual_steps"):
                        value = automation.get(field)
                        if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
                            errors.append(f"{twhere}.automation.{field}: must be a list of strings")
            notes = tool.get("notes")
            if notes is not None and not isinstance(notes, str):
                errors.append(f"{twhere}.notes: must be a string")
            if tool_name == "special_k":
                delay = tool.get("special_k_delay_seconds")
                if delay is not None and (not isinstance(delay, (int, float)) or delay < 0):
                    errors.append(f"{twhere}.special_k_delay_seconds: must be a non-negative number")
                tweaks = tool.get("special_k_ini_tweaks")
                if tweaks is not None:
                    if not isinstance(tweaks, dict):
                        errors.append(f"{twhere}.special_k_ini_tweaks: must be an object")
                    else:
                        for section, values in tweaks.items():
                            if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
                                errors.append(f"{twhere}.special_k_ini_tweaks[{section}]: values must be an object of string values")
    return errors, warnings


def cmd_validate(_args) -> int:
    db = load_db()
    errors, warnings = validate_db(db)
    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"{len(db.get('games', {}))} games checked: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


# ---------------------------------------------------------------- format

def cmd_format(_args) -> int:
    db = load_db()
    save_db(db)
    print(f"Formatted {DB_PATH} ({len(db.get('games', {}))} games, canonical order)")
    return 0


# ---------------------------------------------------------------- report

def cmd_report(_args) -> int:
    db = load_db()
    games = db.get("games", {})
    tool_counts: dict[str, int] = {}
    with_warnings = with_manual = with_tweaks = with_delay = with_launch_opts = 0
    for game in games.values():
        for tool_name, tool in game.get("tools", {}).items():
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            automation = tool.get("automation", {}) if isinstance(tool.get("automation"), dict) else {}
            if tool.get("warnings") or automation.get("warnings"):
                with_warnings += 1
            if tool.get("manual_steps") or automation.get("manual_steps"):
                with_manual += 1
            if tool.get("special_k_ini_tweaks"):
                with_tweaks += 1
            if tool.get("special_k_delay_seconds"):
                with_delay += 1
            if tool.get("launch_options"):
                with_launch_opts += 1
    print(f"games: {len(games)}")
    for tool_name, count in sorted(tool_counts.items()):
        print(f"  {tool_name}: {count}")
    print(f"tools with warnings: {with_warnings}")
    print(f"tools with manual steps: {with_manual}")
    print(f"tools with SK ini tweaks: {with_tweaks}")
    print(f"tools with SK delay: {with_delay}")
    print(f"tools with launch options: {with_launch_opts}")
    unresolved = db.get("unresolved_appids", {})
    entries = []
    if isinstance(unresolved, dict):
        for tool_name, items in unresolved.items():
            for item in items or []:
                label = item.get("name", "?") if isinstance(item, dict) else str(item)
                entries.append(f"{label} ({tool_name})")
    print(f"unresolved entries: {len(entries)}")
    for entry in entries:
        print(f"  - {entry}")
    return 0


# ---------------------------------------------------------------- add

def cmd_add(args) -> int:
    db = load_db()
    games = db.setdefault("games", {})
    appid = str(args.appid)
    game = games.setdefault(appid, {"name": args.name, "tools": {}})
    game["name"] = args.name
    tool = game["tools"].setdefault(args.tool, {
        "automation": {},
        "launch_options": [],
        "name": args.name,
        "notes": "",
        "tags": [args.tool],
    })
    if args.tool == "renodx":
        tool.setdefault("requires_reshade_addon_support", True)
        tool.setdefault("reshade_min_version", "6.7.3")
    if args.notes:
        tool["notes"] = args.notes
    for warning in args.warning or []:
        tool.setdefault("warnings", [])
        if warning not in tool["warnings"]:
            tool["warnings"].append(warning)
    for step in args.manual_step or []:
        tool.setdefault("manual_steps", [])
        if step not in tool["manual_steps"]:
            tool["manual_steps"].append(step)
    for option in args.launch_option or []:
        if option not in tool["launch_options"]:
            tool["launch_options"].append(option)
    if args.delay is not None:
        tool["special_k_delay_seconds"] = args.delay
    for tweak in args.ini or []:
        if "=" not in tweak or tweak.count(".") < 1:
            print(f"ERROR: --ini expects Section.Key=value, got: {tweak}")
            return 1
        key_path, value = tweak.split("=", 1)
        section, key = key_path.rsplit(".", 1)
        tool.setdefault("special_k_ini_tweaks", {}).setdefault(section, {})[key] = value
    tool.setdefault("automation", {})["appid_resolution"] = {
        "appid": appid,
        "method": "manual_cli",
        "source": "scripts/compat_db.py add",
    }
    errors, _ = validate_db(db)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    save_db(db)
    print(f"Saved {args.tool} entry for {args.name} ({appid})")
    return 0


# ---------------------------------------------------------------- sync-renodx

def parse_wiki_mods(markdown: str) -> list[dict]:
    """Parse the wiki with the production parser from main.py (decky stubbed)."""
    import importlib.util
    import tempfile
    import types

    temp_home = tempfile.mkdtemp(prefix="compat-db-")
    decky = types.SimpleNamespace(
        HOME=temp_home, USER="deck", DECKY_USER="deck",
        DECKY_USER_HOME=temp_home, DECKY_HOME=str(Path(temp_home) / "homebrew"),
        DECKY_PLUGIN_DIR=str(ROOT),
        logger=types.SimpleNamespace(**{k: (lambda *a, **kw: None) for k in ["debug", "info", "warning", "error", "exception"]}),
    )
    sys.modules["decky"] = decky
    spec = importlib.util.spec_from_file_location("decky_renodx_main_for_compat_db", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["decky_renodx_main_for_compat_db"] = module
    spec.loader.exec_module(module)
    plugin = module.Plugin()
    return plugin._parse_renodx_mods(markdown)


def resolve_appid(title: str) -> tuple[str, str]:
    """Resolve a Steam AppID via the store search API.

    Returns (appid, resolution) where resolution is 'exact' when the
    normalized names match, 'fuzzy' for the top hit otherwise, or ('', 'none').
    """
    try:
        payload = json.loads(fetch(STORESEARCH_URL + urllib.parse.quote(title)))
    except Exception as error:
        print(f"  ! store search failed for '{title}': {error}")
        return "", "none"
    items = payload.get("items", []) if isinstance(payload, dict) else []
    wanted = normalize_title(title)
    for item in items:
        if normalize_title(str(item.get("name", ""))) == wanted:
            return str(item.get("id", "")), "exact"
    if items:
        return str(items[0].get("id", "")), "fuzzy"
    return "", "none"


def cmd_sync_renodx(args) -> int:
    db = load_db()
    games = db.setdefault("games", {})
    known_names = {normalize_title(game.get("name", "")) for game in games.values()}
    known_names.discard("")

    print(f"Fetching RenoDX wiki list from {RENODX_MODS_URL} ...")
    mods = parse_wiki_mods(fetch(RENODX_MODS_URL))
    specific = [mod for mod in mods if mod.get("match_type") in {"specific", "generic_listed"}]
    print(f"Wiki lists {len(specific)} games; DB has {len(games)}.")

    missing = [mod for mod in specific if mod.get("normalized") and mod["normalized"] not in known_names]
    if args.limit:
        missing = missing[: args.limit]
    print(f"{len(missing)} wiki titles are not in the DB yet.\n")

    staged: dict[str, dict] = {}
    review: list[str] = []
    for mod in missing:
        title = str(mod.get("name", ""))
        appid, resolution = resolve_appid(title)
        time.sleep(args.throttle)
        if resolution != "exact" or not appid:
            hint = f"top hit appid {appid}" if appid else "no store result"
            review.append(f"{title} ({hint})")
            print(f"  ? needs review: {title} ({hint})")
            continue
        if appid in games:
            existing = games[appid]
            if "renodx" not in existing.get("tools", {}):
                print(f"  + adding renodx tool to existing entry: {title} ({appid})")
            else:
                continue
        else:
            print(f"  + staged: {title} ({appid}) [{mod.get('status', 'listed')}]")
        entry = games.get(appid, {"name": title, "tools": {}})
        tool = {
            "automation": {
                "appid_resolution": {
                    "appid": appid,
                    "method": "storesearch_exact",
                    "source": "scripts/compat_db.py sync-renodx",
                },
                "source": "RenoDX Mods wiki",
            },
            "launch_options": [],
            "name": title,
            "notes": "; ".join(mod.get("notes", [])) or f"RenoDX status: {mod.get('status', 'listed')}.",
            "requires_reshade_addon_support": True,
            "reshade_min_version": "6.7.3",
            "tags": ["renodx"],
        }
        if mod.get("engine_bucket"):
            tool["automation"]["engine"] = mod["engine_bucket"]
        if mod.get("addon_url"):
            tool["automation"]["addon_url"] = mod["addon_url"]
        if mod.get("status") == "in_progress":
            tool.setdefault("warnings", []).append("RenoDX mod is marked in-progress on the wiki; expect issues.")
        entry["tools"]["renodx"] = tool
        staged[appid] = entry

    print(f"\nStaged {len(staged)} new/updated entries; {len(review)} need manual review.")
    if review:
        print("Manual review needed (add via 'compat_db.py add' once the AppID is confirmed):")
        for item in review:
            print(f"  - {item}")

    if not args.write:
        print("\nDry run only. Re-run with --write to apply.")
        return 0

    games.update(staged)
    errors, _ = validate_db(db)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("Not saved: staged entries failed validation.")
        return 1
    save_db(db)
    print(f"Wrote {DB_PATH} ({len(games)} games).")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check schema").set_defaults(func=cmd_validate)
    sub.add_parser("format", help="canonicalize the file").set_defaults(func=cmd_format)
    sub.add_parser("report", help="coverage stats").set_defaults(func=cmd_report)

    add = sub.add_parser("add", help="add or update a game entry")
    add.add_argument("--appid", required=True, type=int)
    add.add_argument("--name", required=True)
    add.add_argument("--tool", required=True, choices=sorted(KNOWN_TOOLS))
    add.add_argument("--notes", default="")
    add.add_argument("--warning", action="append", help="repeatable")
    add.add_argument("--manual-step", action="append", help="repeatable")
    add.add_argument("--launch-option", action="append", help="repeatable")
    add.add_argument("--delay", type=int, help="Special K injection delay seconds")
    add.add_argument("--ini", action="append", help="Special K ini tweak: Section.Key=value (repeatable)")
    add.set_defaults(func=cmd_add)

    sync = sub.add_parser("sync-renodx", help="stage new games from the RenoDX wiki")
    sync.add_argument("--write", action="store_true", help="apply changes (default: dry run)")
    sync.add_argument("--limit", type=int, default=0, help="only process the first N missing titles")
    sync.add_argument("--throttle", type=float, default=0.5, help="seconds between Steam store lookups")
    sync.set_defaults(func=cmd_sync_renodx)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
