# compatibility.json — formal schema and workflow

`compatibility.json` is the per-game knowledge base: which HDR tools work, what
settings they need, and what the user must be told. It ships with the plugin and
is refreshed daily from GitHub `main` by the running plugin (payloads that fail a
sanity check are rejected, so a bad deploy can't wipe the local copy).

**Never hand-edit the raw file.** Use `scripts/compat_db.py`:

```
python scripts/compat_db.py validate      # schema check (also runs in npm test)
python scripts/compat_db.py format       # canonical order → clean diffs
python scripts/compat_db.py report       # coverage stats + unresolved entries
python scripts/compat_db.py add ...      # add/update one game from the CLI
python scripts/compat_db.py sync-renodx  # stage new games from the RenoDX wiki
```

`sync-renodx` fetches the wiki mod list, finds titles missing from the DB,
resolves Steam AppIDs via the Steam store search API (exact normalized-name
matches only — anything fuzzy is printed for manual review), and stages entries.
Dry-run by default; `--write` applies, re-formats, and re-validates.

## Top-level shape

```jsonc
{
  "schema_version": 2,
  "metadata": { /* provenance notes, appid resolution history */ },
  "tool_defaults": { /* per-tool global defaults and warnings */ },
  "games": { "<steam appid>": { /* game entry */ } },
  "unresolved_appids": { "<tool>": [ /* entries awaiting a confident appid */ ] }
}
```

## Game entry

```jsonc
"1016800": {
  "name": "Chernobylite",           // required, display name
  "tools": {                        // required, at least one of: renodx, special_k
    "renodx": { /* tool entry */ },
    "special_k": { /* tool entry */ }
  }
}
```

## Tool entry fields

Fields the plugin **applies automatically** at install time:

| field | type | effect |
| --- | --- | --- |
| `launch_options` | `string[]` | appended to the generated Steam launch options (`_hdr_launch_options`) |
| `special_k_delay_seconds` | `number` | written as `GlobalInjectDelay` in SpecialK.ini; also enables the delayed/global method gate |
| `special_k_ini_tweaks` | `{ "Section": { "Key": "value" } }` | merged into SpecialK.ini on install (`_write_specialk_hdr_ini`) |
| `automation.preferred_injection` | `"local" \| "global" \| "global_delayed"` | steers the Special K install method |
| `automation.local_dll.target` | dll name | forces the Special K hook DLL (dxgi/d3d11/d3d9/…) |
| `automation.local_dll.relative_path` | path | installs Special K into a subfolder relative to the exe dir |
| `automation.force_render_api` | api name | overrides detected graphics API |
| `automation.hdr.avoid` | `bool` | blocks Special K HDR for this game |

Fields the plugin **surfaces to the user** (shown in the panel, never auto-applied):

| field | type | shown as |
| --- | --- | --- |
| `warnings` / `automation.warnings` | `string[]` | orange warning list on the game status card |
| `manual_steps` / `automation.manual_steps` | `string[]` | numbered "Manual steps" list on the game status card |
| `notes` | `string` | maintainer context; used for DLL inference as a last resort |

Informational / provenance fields (not consumed by the plugin):

- `automation.appid_resolution` — how the AppID was determined (`method`, `source`)
- `automation.source` — where the entry came from (e.g. "RenoDX Mods wiki")
- `automation.engine`, `automation.renodx_profile`, `automation.renodx_settings`
  — engine and RenoDX config hints, kept for future automation
- `requires_reshade_addon_support`, `reshade_min_version` — RenoDX prerequisites
- `tags` — `["renodx"]` / `["special_k"]`, matches the tool key

## Conventions

- Only confident Steam AppID mappings go under `games`. Anything actionable but
  unmapped goes under `unresolved_appids.<tool>` with a `reason`, and graduates
  via `compat_db.py add` once the AppID is confirmed.
- A warning is something the user must *know* ("expect washed-out UI"); a manual
  step is something the user must *do* ("set HDR to scRGB in-game"). Keep them
  in their own arrays — the UI renders them differently.
- `tool_defaults.<profile>.global_warnings` apply to every game using that tool
  and don't need repeating per game.
