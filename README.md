# Decky RenoDX

Decky RenoDX is an experimental HDR-focused fork/scaffold based on the LetMeReShade Decky plugin workflow.

It aims to automate as much Steam Deck OLED HDR setup as Decky can reasonably handle:

- Detect installed Steam and Heroic games.
- Detect the likely game executable and DirectX DLL target.
- Install an HDR-only runtime with RenoDX import support, Special K, AutoHDR, Pumbo AdvancedAutoHDR, and Lilium HDR shaders.
- Patch games with the correct ReShade DLL override.
- Apply Steam launch options through `SteamClient.Apps.SetAppLaunchOptions`.
- Open a browser search for game-specific RenoDX files.
- Detect a downloaded RenoDX `.addon64`, `.addon32`, or `.zip` in `~/Downloads`.
- Copy RenoDX addon files into the selected game executable folder and apply HDR launch options.
- Fall back through Special K first, then ReShade HDR shader/add-on setup when no downloaded RenoDX addon is found.
- Preserve existing Steam launch wrappers such as decky-lsfg-vk's `~/lsfg %command%` when applying or removing HDR launch options.

## Reality Check

This cannot make perfect native HDR universal. RenoDX is the preferred path when a game-specific mod exists. If RenoDX is unavailable, the plugin tries Special K next, then HDR-only ReShade fallbacks. ReShade shader-only fallbacks can improve or inverse-tone-map output, but they do not always make SteamOS/gamescope treat the game as native HDR.

The intended flow for Nexus-hosted RenoDX files is:

1. Select the game in Decky RenoDX.
2. Use `Open RenoDX download search` to open an embedded Decky browser attempt with a Steam web view fallback.
3. Download the RenoDX addon/archive in the browser.
4. Return to Decky RenoDX.
5. Use `Import downloaded RenoDX addon`.

Addon support and HDR runtime components are automatic. The UI intentionally does not expose toggles for them because the plugin chooses the fallback chain per game.

Decky RenoDX keeps its HDR runtime isolated at `~/.local/share/decky-renodx/reshade`. It does not use LetMeReShade's `~/.local/share/reshade` runtime, so both plugins can coexist without one reporting the other's runtime as installed.

Decky LSFG-VK compatibility is handled by merging launch options instead of replacing them. If a game already uses `~/lsfg %command%`, Decky RenoDX injects its HDR environment variables into that command chain and leaves the LSFG wrapper in place. Removing HDR injection strips only the HDR-related variables/DLL override and preserves unrelated launch options.

On first HDR runtime install, the plugin downloads the ReShade add-on setup tool and a private 7-Zip extractor into `~/.local/share/decky-renodx/bin`, then fetches Special K, AutoHDR, Pumbo AdvancedAutoHDR, and Lilium HDR shader payloads from GitHub. Release zips do not need to bundle LetMeReShade's `bin` payloads or depend on a system `7z` command.

## Current Limitations

- ReShade binary/shader archives must be supplied in the plugin `bin` folder the same way LetMeReShade releases do.
- `.zip` RenoDX imports are supported; `.7z`/`.rar` detection is listed but extraction is not implemented yet.
- Anti-cheat games may block addon injection.
- Native Linux builds need to be forced to Windows/Proton for ReShade/RenoDX injection.
- Real Steam Deck testing is still needed.
- Self-update depends on GitHub releases containing a `decky-renodx.zip` asset.

## Development

```bash
pnpm i
pnpm run test
pnpm run build
pnpm run package
```

## Releases

```bash
pnpm run release -- --draft
```

Use `--private` or `--draft` to create a draft/private-review GitHub release. The release script bumps `package.json`, runs validation, builds `dist`, creates `decky-renodx.zip`, and publishes it as a GitHub release asset for the in-plugin updater.

Repo: `https://github.com/Feelsrat/decky-renodx`
