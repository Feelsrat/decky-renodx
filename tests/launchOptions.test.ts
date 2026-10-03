import assert from "node:assert/strict";
import { hasHdr, mergeHdr, stripHdr, tokenize, type LaunchSpec } from "../src/utils/launchOptions.ts";

const ENV = { PROTON_ENABLE_HDR: "1", DXVK_HDR: "1", ENABLE_HDR_WSI: "1", ENABLE_GAMESCOPE_WSI: "1" };
const HDR_PREFIX = "PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1";
const reshade: LaunchSpec = { env: ENV, dll_overrides: { dxgi: "n,b" }, args: [], wrapper: [] };
const withArgs: LaunchSpec = { env: ENV, dll_overrides: { d3d9: "n,b" }, args: ["-dx11"], wrapper: [] };
const delayed: LaunchSpec = {
  env: ENV,
  dll_overrides: {},
  args: [],
  wrapper: ["bash", "/home/deck/.local/share/decky-renodx/launch/specialk-delayed-launch.sh", "123", "5", "/x/My Mods/SpecialK/SKIF.exe", "--"],
};

let count = 0;
function test(name: string, fn: () => void) {
  fn();
  count++;
  console.log(`ok - ${name}`);
}

test("tokenize keeps quoted values", () => {
  assert.deepEqual(tokenize('A="x y" gamemoderun %command% -foo'), ['A="x y"', "gamemoderun", "%command%", "-foo"]);
});

test("empty options get HDR env and %command%", () => {
  assert.equal(mergeHdr("", reshade), `${HDR_PREFIX} WINEDLLOVERRIDES="dxgi=n,b" %command%`);
});

test("wrappers like gamemoderun/mangohud stay after the env", () => {
  assert.equal(
    mergeHdr("gamemoderun mangohud %command% -vulkan", reshade),
    `${HDR_PREFIX} WINEDLLOVERRIDES="dxgi=n,b" gamemoderun mangohud %command% -vulkan`,
  );
});

test("lsfg wrapper is preserved", () => {
  assert.equal(mergeHdr("~/lsfg %command%", reshade), `${HDR_PREFIX} WINEDLLOVERRIDES="dxgi=n,b" ~/lsfg %command%`);
});

test("options without %command% are game arguments", () => {
  assert.equal(mergeHdr("-skipintro", reshade), `${HDR_PREFIX} WINEDLLOVERRIDES="dxgi=n,b" %command% -skipintro`);
  assert.equal(stripHdr(mergeHdr("-skipintro", reshade), [reshade]), "%command% -skipintro");
});

test("user dll overrides are merged, not replaced, and survive removal", () => {
  const merged = mergeHdr('WINEDLLOVERRIDES="winmm=n,b" %command%', reshade);
  assert.equal(merged, `${HDR_PREFIX} WINEDLLOVERRIDES="winmm=n,b;dxgi=n,b" %command%`);
  assert.equal(stripHdr(merged, [reshade]), 'WINEDLLOVERRIDES="winmm=n,b" %command%');
});

test("user's own dinput8 override is not touched", () => {
  assert.equal(stripHdr('WINEDLLOVERRIDES="dinput8=n,b" %command%', [reshade]), 'WINEDLLOVERRIDES="dinput8=n,b" %command%');
});

test("game args are appended and removed", () => {
  const merged = mergeHdr("%command% -windowed", withArgs);
  assert.equal(merged, `${HDR_PREFIX} WINEDLLOVERRIDES="d3d9=n,b" %command% -windowed -dx11`);
  assert.equal(stripHdr(merged, [withArgs]), "%command% -windowed");
});

test("switching methods replaces the previous spec", () => {
  const first = mergeHdr("gamemoderun %command%", reshade);
  const second = mergeHdr(first, withArgs, [reshade]);
  assert.equal(second, `${HDR_PREFIX} WINEDLLOVERRIDES="d3d9=n,b" gamemoderun %command% -dx11`);
});

test("delayed Special K wrapper sits right before %command% and strips cleanly", () => {
  const merged = mergeHdr("gamemoderun %command%", delayed);
  assert.ok(merged.endsWith('specialk-delayed-launch.sh 123 5 "/x/My Mods/SpecialK/SKIF.exe" -- %command%'), merged);
  assert.ok(hasHdr(merged, delayed));
  assert.equal(stripHdr(merged, [delayed]), "gamemoderun %command%");
});

test("removing everything leaves an empty string", () => {
  assert.equal(stripHdr(mergeHdr("", reshade), [reshade]), "");
});

test("legacy 0.0.x options are cleaned up", () => {
  const legacy = 'PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 WINEDLLOVERRIDES="d3dcompiler_47=n;dxgi=n,b" ~/lsfg %command%';
  assert.equal(stripHdr(legacy), "~/lsfg %command%");
  const legacyDelayed = 'STEAM_COMPAT_DATA_PATH="/c/123" PROTON_LOG=1 PROTON_ENABLE_HDR=1 bash "/plugins/decky-renodx/assets/specialk-delayed-launch.sh" "123" "5" "/x/SKIF.exe" %command%';
  assert.equal(stripHdr(legacyDelayed), "");
});

test("PROTON_LOG set by the user is kept", () => {
  assert.equal(stripHdr("PROTON_LOG=1 %command%", [reshade]), "PROTON_LOG=1 %command%");
});

test("hasHdr detects missing options", () => {
  assert.ok(hasHdr(mergeHdr("", reshade), reshade));
  assert.ok(!hasHdr("%command%", reshade));
  assert.ok(!hasHdr(`${HDR_PREFIX} %command%`, reshade));
});

console.log(`${count} launch option tests passed`);
