# Changelog

## 0.5.1

### Fixes
- **Steam Deck OLED peak brightness was set to 604 nits instead of 1000.** The panel's EDID reports about 604 nits, but SteamOS's compositor (gamescope) ignores that and uses its own profile for the panel: 1000 nits peak, 800 full-frame. Games see gamescope's values, so the plugin now uses them for the Deck OLED, the Zotac Zone and the OneXPlayer F1. Games set up with 0.5.0 show **Repair**. It replaces the old automatic value, and anything you set yourself is kept.

## 0.5.0

### New
- **Brightness matched to your screen.** On install, the plugin reads the active screen's HDR peak brightness from its EDID. Docked, that's the external screen. A Steam Deck OLED that reports none counts as 1000 nits. The value goes in:
  - **RenoDX**: as the mod's peak brightness (`ToneMapPeakNits`) in `ReShade.ini`.
  - **Special K**: as its HDR luminance.

  The screen and its peak are shown under Plugin → "Match brightness to the screen", which turns this off.

### Fixes
- **Reinstalling or Repair no longer resets your RenoDX settings.** Changes made in the RenoDX tab (presets, sliders) are carried over, and they win over the automatic brightness.

## 0.4.6

### Fixes
- **Unity games got ReShade as `opengl32.dll`, so it never showed up.** `UnityPlayer.dll` always links OpenGL, but Unity renders with Direct3D 11/12 on Windows, loading it at runtime. The plugin saw only the OpenGL import and hooked the wrong API: ReShade loaded, but never saw a frame. There was no banner, Home did nothing, and RenoDX never ran (Against the Storm, Wobbly Life). Unity games now get `dxgi.dll`, as in 0.0.x. Affected installs show **Repair**.
- When the graphics API is only a guess from a game DLL's imports, PCGamingWiki's API list wins if it has one.

## 0.4.5

### Fixes
- **ReShade didn't load (no banner, Home did nothing).** The plugin used to install the newest ReShade from reshade.me. Since ReShade 6.8.0 (Aug 2026), fresh installs got 6.8.0, while the setups that worked used 6.7.3. ReShade is now pinned to 6.7.3. Games installed with another version show **Repair**, which switches them. Newer versions will be adopted after testing on a Deck.
- The ReShade installer is unpacked keeping its folders, so a same-named DLL in a subfolder can never be picked by mistake.

### New
- The Check tab (Advanced → View logs) shows the installed ReShade version and the size of its DLL.

## 0.4.4

### Fixes
- After changing a game's launch options, the plugin reads them back from Steam. If Steam didn't save them, it says so and shows the options to set by hand. Before, a silent failure meant ReShade never loaded: no banner at launch, and Home did nothing.

### New
- **Report on GitHub** now includes the Check tab's diagnostics in the report.

## 0.4.3

### Fixes
- **HDR didn't turn on (0.2.0–0.4.2).** 0.2.0 dropped `ENABLE_HDR_WSI=1` and `ENABLE_GAMESCOPE_WSI=1` from the launch options, on reasoning never tested on a Deck. Games then launched without HDR even with RenoDX installed (reported in Wobbly Life and Against the Storm). Both are back, matching the launch options that worked in 0.0.x. Games set up with 0.2.0–0.4.2 show **Repair**, which updates their launch options.
- If you set one of these variables yourself (for example `ENABLE_GAMESCOPE_WSI=0` to work around a crash), your value is kept instead of being overwritten.

### New
- **Advanced → View logs → Check** explains why HDR might not be working: whether the files are in place, whether HDR is on in SteamOS, the game's current Steam launch options, whether ReShade has ever run in the game, and whether the RenoDX add-on loaded. A new **ReShade** tab shows the game's `ReShade.log`.

## 0.4.2

### Changed
- **Library tiles fill in by themselves.** Games the plugin knows nothing about yet are looked up on PCGamingWiki in the background, about 50 games per request and cached for a week. ◆ (native HDR) and ◇ (Unreal/Unity, including games you haven't installed) appear on tiles a few seconds later, without opening each game first.
- Tile icons ignore edition suffixes when matching RenoDX mods ("… Game of the Year Edition", "… Remastered"), like the game page already did.

## 0.4.1

### New
- **Badges in the library grid.** Cover art tiles in the library (and Home's recent games) get a small corner icon: ● HDR set up, ★ RenoDX mod, ◆ native HDR, ◇ Unreal/Unity game where the generic addon may work. Tiles only use data the plugin already has, so ◆ appears once you've opened the game's page or panel (PCGamingWiki isn't asked about every tile). The "Library badges" switch under Plugin turns off both the tile icons and the game page badge.

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
