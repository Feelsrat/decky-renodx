# Decky RenoDX

A [Decky Loader](https://decky.xyz) plugin that adds HDR to Proton games on the Steam Deck OLED (and other HDR SteamOS setups). Pick a game and the plugin finds the best way to get HDR:

1. **RenoDX**: a per-game HDR mod from the [RenoDX wiki](https://github.com/clshortfuse/renodx/wiki/Mods), loaded through ReShade, like the Windows installers (RenoDX Commander / [RHI](https://github.com/RankFTW/RHI)) do. The wiki's notes for the mod (upgrade settings, in-game options) are shown in the panel. Generic Unreal/Unity addons are offered as an experimental option.
2. **Native HDR**: if the game has its own HDR, the plugin only sets Proton's HDR switches.
3. **Special K**: HDR retrofit for DX10-12 games, with per-game settings from `compatibility.json`.
4. **ReShade AutoHDR**: AutoHDR, Lilium and Pumbo shaders as a fallback.

It also sets the matching Steam launch options for you, and marks games in the library grid and on each game's page: ★ a RenoDX mod exists, ◆ the game has native HDR, ◇ it's an Unreal/Unity game where the generic RenoDX addon may work, ● HDR is set up. The badges can be turned off under Plugin. Steam games and non-Steam games you've added to Steam are both supported.

## What it changes, and how it undoes it

- Every install is recorded in `~/.local/share/decky-renodx/installs/<appid>.json`: the files it added, and backups of any game files it replaced. Backups sit in a hidden `.decky-renodx` folder next to the original.
- **Remove HDR** deletes exactly those files and restores the backups, so the game folder is back to how it was.
- If switching to another method fails, the previous install is put back.
- Launch options are changed through Steam's API. HDR variables go at the front, so wrappers like `gamemoderun`, `mangohud` or decky-lsfg-vk's `~/lsfg` keep working, and your own options are left alone.
- Games with anti-cheat (EasyAntiCheat, BattlEye, …) are blocked from injection.

Shared downloads (ReShade, Special K, shader packs, 7-Zip) are fetched over verified HTTPS the first time a method needs them. They are stored in `~/.local/share/decky-renodx/runtime` and `bin`.

## Using it

1. Turn on HDR in SteamOS (Settings → Display). The plugin warns you if it's off.
2. Open the Quick Access menu → Decky → **Decky RenoDX**. The game you're playing is picked automatically.
3. Press **Enable HDR** to install the recommended method, or open **Other methods**.
4. Launch the game and answer **Did HDR work?**. If it didn't, try another method or report it on GitHub straight from the panel.

The ReShade overlay opens with **Home** and Special K with **Ctrl+Shift+Backspace**; bind them to back buttons in Steam Input.

Some RenoDX mods are only on Nexus Mods or Discord. For those, download the `.addon64`/`.addon32` (or an archive containing it) to `~/Downloads` from Desktop Mode, then use **Import downloaded RenoDX mod**.

**Advanced** has: choosing the game executable by hand, checking the installed files, reinstalling (repair), viewing plugin/Proton logs, a link to the game's PCGamingWiki page, refreshing the mod list, and resetting the Proton prefix as a last resort.

## Limitations

- Native Linux builds must be switched to Proton first.
- Vulkan games can't use proxy-DLL injection (ReShade/Special K/RenoDX). Use their native HDR if they have it.
- Special K HDR always needs a check in game. Games whose compatibility entry needs Special K injected after launch (its global injector) aren't set up automatically.
- Updating a game through Steam can replace installed files. The panel shows when files are missing; reinstalling repairs it.

## Development

```bash
pnpm i
pnpm test            # Python tests, compat DB validation, launch-option tests, types, build
pnpm run build
pnpm run package     # writes decky-renodx.zip
```

### Working on the UI without a Deck

```bash
pnpm dev             # http://127.0.0.1:8787
pnpm dev:screens     # (with pnpm dev running) screenshots of every scenario into dev/screens/
```

`pnpm dev` bundles the real plugin UI with browser stand-ins for `@decky/ui`, `@decky/api` and `SteamClient`, laid out like the Quick Access Menu. It also starts `dev/server.py`, which runs the real Python backend against a fake Steam library in `dev/.sandbox`. That library has an Unreal game, a 32-bit DX9 game, a Vulkan game, an anti-cheat game, a native Linux build, a native-HDR game, a game with compatibility notes and a non-Steam shortcut. Downloads are faked, so installs, repairs and removals run for real against the fake game folders. The page shows the resulting Steam launch options next to the panel. Useful flags:

- `python3 dev/server.py --hdr off`: pretend HDR is turned off in SteamOS.
- `--reset`: start over with a fresh fake library.
- `?running=1000` in the URL: pretend that game is running.

The stand-ins only approximate Steam's look and gamepad focus, so check new controls on a Deck before releasing.

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
| `backend/renodx.py`, `renodx_index.py`, `rhi.py` | RenoDX mods from the wiki and RenoDX's build indexes, plus RHI's per-game fixes (see below) |
| `backend/pcgw.py`, `compat.py` | PCGamingWiki (native HDR, API, engine); Special K settings from `compatibility.json` |
| `src/components/HdrPanel.tsx` | The per-game panel |
| `src/library.tsx`, `components/LibraryBadge.tsx` | The badge on library game pages (patches Steam's `/library/app/:appid` route, like ProtonDB Badges) |
| `src/gridBadges.ts` | Corner icons on library tiles (watches Big Picture's page for cover art and reads the AppID from the image URL) |
| `src/utils/launchOptions.ts` | Merging and removing launch options |
| `dev/` | Local UI harness (see above) |
| `compatibility.json` | Per-game Special K settings; format in `scripts/compat_db.py` |

## Releasing

1. Add a `## x.y.z` section to `CHANGELOG.md` and commit it. The release notes come from it, and releasing refuses without it.
2. Run:

```bash
pnpm run release -- patch     # or minor / major / 1.2.3
```

This bumps `package.json`, runs the tests, commits, tags `vX.Y.Z` and pushes. The **Release** workflow then builds `decky-renodx.zip` and publishes the GitHub release that the in-plugin updater installs from.

You can also run the **Release** workflow by hand (Actions → Release → Run workflow): it releases the version in `package.json` from the chosen branch and creates the tag itself.

## Maintainer notes

- `pnpm dev` to work on the UI, `pnpm test` before pushing (CI runs the same).
- Where per-game data comes from, all fetched live and cached for a day, so fixes reach players without a release:
  - **RenoDX**: the [wiki's mod list](https://github.com/clshortfuse/renodx/wiki/Mods) (download links, status, notes). Fix wrong data on the wiki itself.
  - **RenoDX build indexes**: `games-index.json` from each RenoDX repo's snapshot release (generated from the `metadata.json` next to each mod's source). Used by Steam AppID when the wiki has no entry for a game.
  - **RHI's [manifest.json](https://github.com/RankFTW/RHI/blob/main/manifest.json)**, maintained for the Windows installer: wiki name fixes, addon URL fixes, Nexus/Discord-only mods and per-game warnings. It's optional; without it the wiki is used as is.
  - **Special K**: `compatibility.json` in this repo. Edit it with `python scripts/compat_db.py add …` (the format is documented at the top of that script); `pnpm test` validates it. The plugin picks up changes from `main` within a day.
- `backend/cache.py` and `defaults/assets/specialk-delayed-launch.sh` are only shipped because older versions' self-updaters require them in release zips. Remove them after a release or two.
- Cleanup for installs made by 0.0.x (`state.find_legacy`/`remove_legacy`) can go after 0.3.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
