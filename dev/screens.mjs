// Screenshot the harness for each scenario: node dev/screens.mjs [baseUrl]
// Requires dev/server.py running. Uses the system Chromium that Playwright can find.
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const out = join(dirname(fileURLToPath(import.meta.url)), "screens");
mkdirSync(out, { recursive: true });
const base = process.argv[2] || "http://127.0.0.1:8787/";
const candidates = [process.env.CHROMIUM_PATH, "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", "/usr/bin/chromium"].filter(Boolean);
const executablePath = candidates.find((path) => existsSync(path));

const browser = await chromium.launch({ executablePath });
const page = await browser.newPage({ viewport: { width: 1100, height: 860 } });
page.on("pageerror", (error) => console.error("page error:", error.message));
page.on("console", (message) => message.type() === "error" && console.error("console:", message.text()));

async function shot(name) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(out, `${name}.png`) });
  console.log(`saved ${name}.png`);
}

async function pick(label) {
  const select = page.locator(".qam select").first();
  const value = await select.evaluate((el, text) => [...el.options].find((o) => o.textContent.includes(text))?.value, label);
  await select.selectOption(value);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(700);
}

const scenarios = (process.env.SCENARIOS || "").split(",").filter(Boolean);
const run = (name) => !scenarios.length || scenarios.includes(name);

await fetch(new URL("/dev/reset", base), { method: "POST" });
await page.goto(base);
await page.evaluate(() => localStorage.clear());
await page.goto(base);
await page.waitForTimeout(800);
if (run("start")) await shot("01-start");
for (const [name, label] of [["unreal", "Shippy"], ["dx9-32bit", "Retro Racer"], ["anticheat", "Arena"], ["linux", "Penguin"], ["native", "Bright Lights"], ["shortcut", "Indie Darling"], ["vulkan", "Vulkan Quest"]]) {
  if (!run(name)) continue;
  await pick(label);
  await shot(`02-${name}`);
}
async function click(text) {
  await page.locator(".qam button, .dk-modal button, button", { hasText: text }).first().click();
}

if (run("install")) {
  await pick("Shippy");
  await click("Enable HDR");
  await page.waitForTimeout(300);
  await shot("03-installing");
  await page.waitForTimeout(2500);
  await shot("04-installed");
  await click("Yes");
  await page.waitForTimeout(800);
  await shot("05-confirmed");
  await click("Switch method");
  await shot("06-switch-methods");
}
if (run("manual")) {
  await pick("Manual Game");
  await shot("07-manual");
  await click("Get the RenoDX mod");
  await page.waitForTimeout(800);
  await shot("08-import-modal");
  await click("Install selected file");
  await page.waitForTimeout(2500);
  await shot("08b-imported");
}
if (run("failed")) {
  await pick("Retro Racer");
  await click("Enable HDR");
  await page.waitForTimeout(3000);
  await click("No");
  await page.waitForTimeout(800);
  await shot("09-failed");
}
await browser.close();
