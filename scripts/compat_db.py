#!/usr/bin/env python3
"""Check and edit compatibility.json, the plugin's per-game Special K knowledge.

The running plugin re-downloads compatibility.json from `main` daily, so a fix
merged here reaches players without a release. RenoDX data is not kept here: the
plugin reads the RenoDX wiki and RHI's manifest live (backend/renodx.py, rhi.py).

Commands:
  python scripts/compat_db.py validate     # also runs in `pnpm test`
  python scripts/compat_db.py format       # canonical order, for clean diffs
  python scripts/compat_db.py add --appid 12345 --name "Some Game" \\
      --warning "Crashes with the overlay" --ini "Steam.Log.Silent=true"

File format:
  {
    "schema_version": 3,
    "games": {
      "<steam appid>": {
        "name": "Game name",
        "tools": {"special_k": {
          "notes": "context for maintainers",
          "launch_options": ["-dx11"],                 # game arguments, after %command%
          "special_k_ini_tweaks": {"Section": {"Key": "value"}},   # written to SpecialK.ini
          "automation": {
            "preferred_injection": "local",            # one of PREFERRED_INJECTION in backend/compat.py;
                                                       # anything but the local modes blocks the install
            "local_dll": {"target": "dinput8.dll", "relative_path": "bin"},  # hook DLL / subfolder
            "force_render_api": "Direct3D 11",         # overrides the detected graphics API
            "hdr": {"avoid": true},                    # blocks Special K HDR
            "avoid_injection_at_launch": true,         # blocks the install unless local_dll is set
            "avoid_injection_modes": ["local"],        # blocks the install
            "hardware_requirement": "...",             # blocks unless "Steam Deck"
            "required_wrapper": [...], "required_files": [...], "anti_cheat": {...},  # block the install
            "warnings": ["shown in orange: what the player must know"],
            "manual_steps": ["shown as numbered steps: what the player must do"],
            ...                                        # other keys are notes for maintainers
          }
        }}
      }
    }
  }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.compat import PREFERRED_INJECTION  # noqa: E402

DB_PATH = ROOT / "compatibility.json"
TOOLS = {"special_k"}


def load_db(path: Path = DB_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_db(db: dict, path: Path = DB_PATH) -> None:
    """Write in canonical form: sorted keys, games ordered by numeric appid."""
    games = db.get("games", {})
    db["games"] = {k: games[k] for k in sorted(games, key=lambda a: (not a.isdigit(), int(a) if a.isdigit() else 0, a))}
    path.write_text(json.dumps(db, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_db(db: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(db.get("schema_version"), int):
        errors.append("schema_version must be an integer")
    games = db.get("games")
    if not isinstance(games, dict) or len(games) < 50:
        # The plugin rejects downloads with fewer than 50 games.
        return errors + ["games must be an object with at least 50 entries"]
    for key in db:
        if key not in {"schema_version", "games"}:
            errors.append(f"unexpected top-level key '{key}'")
    for appid, game in games.items():
        where = f"games[{appid}]"
        if not re.fullmatch(r"\d+", str(appid)):
            errors.append(f"{where}: appid must be numeric")
        if not isinstance(game, dict) or not isinstance(game.get("name"), str) or not game["name"].strip():
            errors.append(f"{where}: needs a non-empty name")
            continue
        tools = game.get("tools")
        if not isinstance(tools, dict) or not tools or set(tools) - TOOLS:
            errors.append(f"{where}.tools: must contain only {sorted(TOOLS)}")
            continue
        tool = tools["special_k"]
        twhere = f"{where}.tools.special_k"
        if not isinstance(tool, dict):
            errors.append(f"{twhere}: must be an object")
            continue
        for key in tool:
            if key not in {"notes", "launch_options", "special_k_ini_tweaks", "automation"}:
                errors.append(f"{twhere}: unexpected key '{key}'")
        if "notes" in tool and not isinstance(tool["notes"], str):
            errors.append(f"{twhere}.notes: must be a string")
        if "launch_options" in tool and not _strings(tool["launch_options"]):
            errors.append(f"{twhere}.launch_options: must be a list of strings")
        tweaks = tool.get("special_k_ini_tweaks", {})
        if not isinstance(tweaks, dict) or not all(isinstance(v, dict) and all(isinstance(x, str) for x in v.values()) for v in tweaks.values()):
            errors.append(f"{twhere}.special_k_ini_tweaks: must map sections to {{key: string}}")
        automation = tool.get("automation", {})
        if not isinstance(automation, dict):
            errors.append(f"{twhere}.automation: must be an object")
            continue
        for field in ("warnings", "manual_steps"):
            if field in automation and not _strings(automation[field]):
                errors.append(f"{twhere}.automation.{field}: must be a list of strings")
        preferred = automation.get("preferred_injection", "")
        if preferred not in PREFERRED_INJECTION:
            errors.append(f"{twhere}.automation.preferred_injection: '{preferred}' is not one of {sorted(k for k in PREFERRED_INJECTION if k)}")
        if "local_dll" in automation and not isinstance(automation["local_dll"], dict):
            errors.append(f"{twhere}.automation.local_dll: must be an object")
    return errors


def cmd_validate(_args) -> int:
    db = load_db()
    errors = validate_db(db)
    for error in errors:
        print(f"ERROR: {error}")
    print(f"{len(db.get('games', {}))} games checked: {len(errors)} error(s)")
    return 1 if errors else 0


def cmd_format(_args) -> int:
    db = load_db()
    save_db(db)
    print(f"Formatted {DB_PATH} ({len(db.get('games', {}))} games)")
    return 0


def cmd_add(args) -> int:
    db = load_db()
    game = db.setdefault("games", {}).setdefault(str(args.appid), {"name": args.name, "tools": {}})
    game["name"] = args.name
    tool = game["tools"].setdefault("special_k", {})
    automation = tool.setdefault("automation", {})
    if args.notes:
        tool["notes"] = args.notes
    if args.injection is not None:
        automation["preferred_injection"] = args.injection
    for field, values in (("warnings", args.warning), ("manual_steps", args.manual_step)):
        for value in values or []:
            if value not in automation.setdefault(field, []):
                automation[field].append(value)
    for option in args.launch_option or []:
        if option not in tool.setdefault("launch_options", []):
            tool["launch_options"].append(option)
    for tweak in args.ini or []:
        if "=" not in tweak or "." not in tweak.split("=", 1)[0]:
            print(f"ERROR: --ini expects Section.Key=value, got: {tweak}")
            return 1
        key_path, value = tweak.split("=", 1)
        section, key = key_path.rsplit(".", 1)
        tool.setdefault("special_k_ini_tweaks", {}).setdefault(section, {})[key] = value
    if not automation:
        del tool["automation"]
    errors = validate_db(db)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    save_db(db)
    print(f"Saved Special K entry for {args.name} ({args.appid})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="check the file").set_defaults(func=cmd_validate)
    sub.add_parser("format", help="rewrite in canonical order").set_defaults(func=cmd_format)
    add = sub.add_parser("add", help="add or update a game's Special K entry")
    add.add_argument("--appid", required=True, type=int)
    add.add_argument("--name", required=True)
    add.add_argument("--notes", default="")
    add.add_argument("--injection", choices=sorted(k for k in PREFERRED_INJECTION if k))
    add.add_argument("--warning", action="append", help="repeatable")
    add.add_argument("--manual-step", action="append", help="repeatable")
    add.add_argument("--launch-option", action="append", help="repeatable")
    add.add_argument("--ini", action="append", help="Special K ini tweak: Section.Key=value (repeatable)")
    add.set_defaults(func=cmd_add)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
