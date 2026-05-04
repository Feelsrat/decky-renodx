# Decky RenoDX

Decky RenoDX is an experimental HDR-focused fork/scaffold based on the LetMeReShade Decky plugin workflow.

It aims to automate as much Steam Deck OLED HDR setup as Decky can reasonably handle:

- Detect installed Steam and Heroic games.
- Detect the likely game executable and DirectX DLL target.
- Install ReShade with addon support and AutoHDR components.
- Patch games with the correct ReShade DLL override.
- Apply Steam launch options through `SteamClient.Apps.SetAppLaunchOptions`.
- Open a browser search for game-specific RenoDX files.
- Detect a downloaded RenoDX `.addon64`, `.addon32`, or `.zip` in `~/Downloads`.
- Copy RenoDX addon files into the selected game executable folder and apply HDR launch options.

## Reality Check

This cannot make perfect native HDR universal. AutoHDR is a fallback for DX10/11/12 games. RenoDX is usually better, but it depends on game-specific addon/profile files, many of which are hosted on Nexus Mods or other sites that require user interaction.

The intended flow for Nexus-hosted RenoDX files is:

1. Select the game in Decky RenoDX.
2. Use `Open RenoDX download search`.
3. Download the RenoDX addon/archive in the browser.
4. Return to Decky RenoDX.
5. Use `Import downloaded RenoDX addon`.

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

The release script bumps `package.json`, runs validation, builds `dist`, creates `decky-renodx.zip`, and publishes it as a GitHub release asset for the in-plugin updater.

Private repo: `https://github.com/Feelsrat/decky-renodx`
