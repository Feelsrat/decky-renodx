# Changelog

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
