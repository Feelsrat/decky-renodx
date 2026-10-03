# Decky RenoDX

A [Decky Loader](https://decky.xyz) plugin that adds HDR to Proton games on the Steam Deck OLED (and other HDR SteamOS setups). Pick a game and the plugin finds the best way to get HDR:

1. **RenoDX**: a per-game HDR mod from the [RenoDX wiki](https://github.com/clshortfuse/renodx/wiki/Mods), loaded through ReShade. Generic Unreal/Unity addons are offered as an experimental option.
2. **Native HDR**: if the game has its own HDR, the plugin only sets Proton's HDR switches.
3. **Special K**: HDR retrofit for DX10-12 games (or games listed in the compatibility database), including an experimental delayed-injection mode.
4. **ReShade AutoHDR**: AutoHDR, Lilium and Pumbo shaders as a fallback.

It also sets the matching Steam launch options for you.

## What it changes, and how it undoes it

- Every install is recorded in `~/.local/share/decky-renodx/installs/<appid>.json`: the files it added, and backups of any game files it replaced. Backups sit in a hidden `.decky-renodx` folder next to the original.
- **Remove HDR** deletes exactly those files and restores the backups, so the game folder is back to how it was.
- If switching to another method fails, the previous install is put back.
- Launch options are changed through Steam's API. HDR variables go at the front, so wrappers like `gamemoderun`, `mangohud` or decky-lsfg-vk's `~/lsfg` keep working, and your own options are left alone.
- Games with anti-cheat (EasyAntiCheat, BattlEye, …) are blocked from injection.

Shared downloads (ReShade, Special K, shader packs, 7-Zip) are fetched over verified HTTPS the first time a method needs them. They are stored in `~/.local/share/decky-renodx/runtime` and `bin`.

## Using it

1. Open the Quick Access menu → Decky → **Decky RenoDX** and pick a game.
2. Check the recommendation, then press **Install** (or pick another method first).
3. Launch the game. For RenoDX/ReShade press **Home** for the ReShade overlay; for Special K press **Ctrl+Shift+Backspace**.

Some RenoDX mods are only on Nexus Mods or Discord. For those, download the `.addon64`/`.addon32` (or an archive containing it) to `~/Downloads` from Desktop Mode, then use **Import downloaded RenoDX mod**.

**Advanced** has: choosing the game executable by hand, checking the installed files, viewing plugin/Proton logs, PCGamingWiki fixes, resetting caches, and resetting the Proton prefix as a last resort.

## Limitations

- Native Linux builds must be switched to Proton first.
- Vulkan games can't use proxy-DLL injection (ReShade/Special K/RenoDX). Use their native HDR if they have it.
- Special K HDR, and the delayed mode especially, always needs a check in game.
- Updating a game through Steam can replace installed files. The panel shows when files are missing; reinstalling repairs it.

## Development

```bash
pnpm i
pnpm test            # Python tests, compat DB validation, launch-option tests, types, build
pnpm run build
pnpm run package     # writes decky-renodx.zip
```

Layout:

| Path | What |
| --- | --- |
| `main.py` | Decky RPC entry point; per-game locks, runs everything in threads |
| `backend/service.py` | The operations (state, install, remove, import, …) |
| `backend/detect.py` | Executable / graphics API / engine / anti-cheat detection |
| `backend/recommend.py` | Pure scoring of methods |
| `backend/installers.py` | One installer per method, writing only through `transaction.py` |
| `backend/transaction.py` | Records and reverses file changes |
| `backend/runtime.py` | Shared downloads |
| `backend/renodx.py`, `pcgw.py`, `compat.py` | RenoDX wiki, PCGamingWiki, compatibility database |
| `src/utils/launchOptions.ts` | Merging and removing launch options |
| `compatibility.json` | Per-game knowledge base; see [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) |

## Releasing

```bash
pnpm run release -- patch     # or minor / major / 1.2.3
```

This bumps `package.json`, runs the tests, commits, tags `vX.Y.Z` and pushes. The **Release** workflow then builds `decky-renodx.zip` and publishes the GitHub release that the in-plugin updater installs from.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
