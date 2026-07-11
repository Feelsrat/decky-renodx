import { stripHdrLaunchTokens, mergeHdrLaunchOptions, hdrLaunchOptionsForDll } from "../src/utils/hdr_logic.ts";

let failed = false;

function assertEqual(actual: string, expected: string, msg: string) {
  if (actual !== expected) {
    console.error(`FAIL: ${msg} | Expected '${expected}', got '${actual}'`);
    failed = true;
  } else {
    console.log(`OK: ${msg}`);
  }
}

// 1. stripHdrLaunchTokens Tests
assertEqual(
  stripHdrLaunchTokens("PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 %command% -dx11"),
  "%command% -dx11",
  "Strips standard HDR tokens"
);

assertEqual(
  stripHdrLaunchTokens(`WINEDLLOVERRIDES="dxgi=n,b" %command%`),
  "%command%",
  "Strips WINEDLLOVERRIDES with quotes"
);

assertEqual(
  stripHdrLaunchTokens(`WINEDLLOVERRIDES=dxgi=n,b %command%`),
  "%command%",
  "Strips WINEDLLOVERRIDES without quotes"
);

assertEqual(
  stripHdrLaunchTokens(`WINEDLLOVERRIDES="d3dcompiler_47,dxgi=n,b" ENABLE_GAMESCOPE_WSI=1 %command%`),
  "%command%",
  "Strips complex WINEDLLOVERRIDES and other WSI tokens"
);

assertEqual(
  stripHdrLaunchTokens("gamemoderun %command% -vulkan"),
  "gamemoderun %command% -vulkan",
  "Leaves unrelated launch options intact"
);

assertEqual(
  stripHdrLaunchTokens(`STEAM_COMPAT_DATA_PATH="/home/deck/.local/share/Steam/steamapps/compatdata/123" PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 bash "/home/deck/homebrew/plugins/decky-renodx/assets/specialk-delayed-launch.sh" "123" "5" "/path/SKIF.exe" %command%`),
  "%command%",
  "Strips Special K delayed wrapper and compat path"
);

// 2. mergeHdrLaunchOptions Tests
assertEqual(
  mergeHdrLaunchOptions("gamemoderun %command% -vulkan", "PROTON_ENABLE_HDR=1 DXVK_HDR=1"),
  "gamemoderun PROTON_ENABLE_HDR=1 DXVK_HDR=1 %command% -vulkan",
  "Merges with existing %command%"
);

assertEqual(
  mergeHdrLaunchOptions("", "PROTON_ENABLE_HDR=1 %command%"),
  "PROTON_ENABLE_HDR=1 %command%",
  "Merges with empty existing options"
);

assertEqual(
  mergeHdrLaunchOptions("DXVK_HDR=1 %command%", "PROTON_ENABLE_HDR=1"),
  "PROTON_ENABLE_HDR=1 %command%",
  "Strips existing HDR options before merging"
);

// 3. hdrLaunchOptionsForDll Tests
assertEqual(
  hdrLaunchOptionsForDll("dxgi"),
  `PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 WINEDLLOVERRIDES="d3dcompiler_47=n;dxgi=n,b" %command%`,
  "Generates correct options for DXGI"
);

assertEqual(
  hdrLaunchOptionsForDll("opengl32"),
  `PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 %command%`,
  "Generates correct options for OpenGL32 (no DLL override)"
);

if (failed) {
  process.exit(1);
}
