# Changelog

## 0.4.0

### New
- **Badges on game pages.** Each game's library page shows a small badge with its HDR status. It works for games you haven't installed too. Selecting the badge opens the plugin on that game.
  - **● HDR on**: Decky RenoDX has set HDR up.
  - **★ RenoDX**: the RenoDX wiki lists a mod for the game ("RenoDX WIP" for one marked in progress).
  - **◆ Native HDR**: PCGamingWiki says the game has its own HDR.
  - **◇ RenoDX? (Unreal/Unity)**: no game-specific mod, but the game uses Unreal or Unity, so RenoDX's experimental generic addon may work. The engine comes from the game's files, or from PCGamingWiki for games you haven't installed.

  Turn the badges off under Plugin → "Badges on game pages".
- Games with a RenoDX mod are marked with ★ in the panel's game list.
- **RenoDX's own build index.** Every RenoDX build repo (clshortfuse/renodx, NotVoosh/renodx-unity, OopyDoopy/renodx) publishes a list of its mods by Steam AppID. The plugin now reads these lists too. That finds mods the wiki doesn't list yet, such as Wobbly Life's, which used to get only the experimental generic Unity addon. Steam games are matched by AppID instead of by name.

### Fixes
- RenoDX addons now download from the GitHub release first, then GitHub Pages. The generic Unity addon has grown past 100 MB, more than GitHub Pages is meant to serve.
- When "Enable HDR" can't install RenoDX and falls back to another method (for example Special K), the panel now says so and shows why. Before, the fallback was silent, so the game could look like it launched without HDR.

## 0.3.0

### RenoDX data straight from the source
- The panel now shows the RenoDX wiki's own notes for the matched mod, such as which upgrades to set and in-game options to change. They appear before installing and on the "Check it in game" card. These replace the copy of the wiki that was stored in `compatibility.json`, which covered about 300 of the wiki's 950+ mods and went out of date.
- Per-game fixes are read from the manifest of [RHI](https://github.com/RankFTW/RHI), the Windows RenoDX installer: wiki name fixes, corrected addon download links, mods that are only on Nexus or Discord, and per-game warnings. They're fetched daily like the wiki. If the manifest can't be fetched, the plugin uses the wiki alone.

### Fixes
- Special K is no longer offered for games the compatibility list says not to inject (Star Wars Battlefront II, Far Cry 3 Blood Dragon, Far Cry 4, No Man's Sky, Rainbow Six Siege, Dead by Daylight). It's also no longer offered for games that need a separate anti-cheat-free executable, or Special K's global injector alongside a local DLL.
- Steam names with ™, ® or curly apostrophes now match their RenoDX wiki entry (for example "Batman™: Arkham Knight").

### Project
- `compatibility.json` now only holds Special K settings: 95 games, down from 400, and about 11% of its old size. Unused fields are gone. Its format is documented in `scripts/compat_db.py`, which rejects unknown injection modes. `docs/COMPATIBILITY.md` and the `sync-renodx` command were removed.
- The local dev harness no longer downloads the real compatibility list.

## 0.2.1

### Fixes
- An install interrupted by a crash, reboot or plugin reload is now undone the next time the plugin starts. Previously, leftover files could later be mistaken for the game's own (so Remove HDR could put ReShade back), and an interrupted method switch could strand the previous install.
- The panel refreshes every time the Quick Access Menu opens. It switches to a game you just started and picks up game updates and launch options you edited yourself.
- The compatibility database now refreshes when it's more than a day old, retrying every few minutes when offline. Before, a failed first fetch (Decky often starts before Wi-Fi) meant no refresh for a day of uptime, and sleep paused that timer.
- Slow or offline networks no longer stall the panel. PCGamingWiki lookups for one game don't block another, failures aren't retried on every refresh, and non-Steam games skip PCGamingWiki.
- Self-update refuses to run, and leaves everything as it was, when it can't start its helper outside Decky's service. Previously Decky could be left stopped.
- Shared downloads (7-Zip, ReShade, Special K) are saved atomically, so an interrupted download can't leave a broken copy behind.

### Removed (simpler, fewer things to break)
- **Special K Delayed.** Existing installs keep working and can still be removed; Repair asks you to pick another method.
- **Display Commander** is no longer added to RenoDX installs.
- **The PCGamingWiki fixes window.** Advanced now links to the game's PCGamingWiki page instead. PCGamingWiki is still used for native-HDR and graphics-API hints.

### Project
- Issue forms (the in-app "Report on GitHub" fills one in), Dependabot for GitHub Actions, unused dev dependencies removed, and releases require a CHANGELOG entry.
- Cleanup for installs made by 0.0.x will be removed after 0.3.

## 0.2.0

### New
- **Non-Steam games.** Games you added to Steam yourself (any launcher or folder) show up in the list. HDR is installed next to the shortcut's .exe and launch options are set on the shortcut.
- **Redesigned panel.** The game you're playing is selected automatically and listed first, followed by recently played games. A status card says plainly whether HDR is set up. There is one main button, a guided "check it in game" step, and a "Did HDR work?" prompt that can open a pre-filled GitHub report. Other methods are listed with plain-language descriptions.
- **HDR display check.** Warns when HDR is turned off in SteamOS, or when the screen doesn't support HDR.
- **Repair.** Detects when a game update or Steam's file check removed HDR files, or when an install uses old launch options, and offers one-tap Repair. RenoDX repairs reuse a kept copy of the mod, so no download is needed.
- RenoDX settings from the compatibility database (e.g. which upgrades to set) are shown as steps after installing.
- `pnpm dev`: a local browser simulation of the Quick Access Menu that runs the real backend against a fake Steam library, plus `pnpm dev:screens` for screenshots.

### Changed
- HDR launch options are now just `PROTON_ENABLE_HDR=1 DXVK_HDR=1`. `ENABLE_HDR_WSI` (a desktop-only layer that can wash colours out under gamescope) and the forced `ENABLE_GAMESCOPE_WSI=1` (can crash 32-bit games on some gamescope builds) are gone. Existing installs show "Repair" to update.
- Launch options you already had (for example `-dx11` or `dxgi=n,b`) are remembered and left in place when HDR is removed.

## 0.1.0

A ground-up rewrite of the plugin backend and panel, aimed at "it works the same every time" and "removing it puts the game back exactly as it was".

### Safety
- Installs are now transactional. Every file the plugin adds is recorded, and any game file it overwrites is backed up first. Remove HDR restores the game folder byte-for-byte.
- Removed the broad cleanup that could delete a game's own `Shaders`/`Textures` folders, `*.addon` files or proxy DLLs.
- Switching methods no longer leaves a game with nothing when the new method fails: the previous install is put back.
- The game's own `d3dcompiler_47.dll` and `dxgi.dll` are no longer overwritten without a backup.
- The plugin no longer edits Steam's `localconfig.vdf` (Steam overwrote those edits). Launch options are only changed through Steam's own API.
- Downloads always verify TLS; the unverified fallback is gone. Plugin updates are checked against the release's SHA-256 digest, and the updater runs in its own systemd unit, so stopping Decky Loader cannot kill it halfway.
- The Proton prefix is never created or chowned by the plugin; Special K Delayed refuses to run until the game has been launched once.
- Resetting a Proton prefix is refused while the game is running.

### Fixes
- Unreal Engine games: ReShade, the RenoDX addon and the status check all use the `*-Shipping.exe` folder (previously ReShade could land next to the launcher stub, so RenoDX never loaded).
- Launch options now put the HDR variables first, so `gamemoderun`, `mangohud`, `~/lsfg` and other wrappers keep working. Your own `WINEDLLOVERRIDES` entries and game arguments are kept. Old-style options from 0.0.x are cleaned up automatically.
- Games on an SD card use the Proton prefix in their own library.
- Anti-cheat detection scans the whole install, not just the executable's folder, case-insensitively.
- RenoDX matching no longer matches sequels or different games ("Doom" vs "DOOM Eternal"); it also uses the compatibility database's titles. The cached mod list is used when offline.
- Non-ASCII game names (e.g. "Pokémon") are read correctly.
- 32-bit games are no longer hard-blocked; 32-bit RenoDX mods and ReShade work.
- Vulkan games are no longer offered ReShade/Special K proxy DLLs that cannot hook them.
- The Special K Delayed launch wrapper is shipped in the release and exits with the game.
- Compatibility-database game arguments go after `%command%`.
- `PROTON_LOG=1` is no longer added to every HDR launch.
- All slow work (folder scans, downloads, PCGamingWiki) runs off Decky's event loop; PCGamingWiki results are cached.
- Only one change per game can run at a time; switching games mid-install can no longer mix up their state.

### New
- "Native HDR" applies the Proton HDR switches without injecting anything.
- Choose the game executable manually (Advanced) when detection picks the wrong one.
- "Apply launch options" appears if HDR files are installed but Steam's launch options are missing them.
- Release builds are produced by GitHub Actions from a version tag.

### Removed
- The 870-line LetMeReShade bash installer (replaced by Python), the duplicate-process "fix" tool, the non-functional Special K UI-scale setting, and template leftovers.
