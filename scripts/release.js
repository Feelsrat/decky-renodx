#!/usr/bin/env node
import { readFileSync, writeFileSync, existsSync, rmSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { execSync, spawnSync } from "child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const rootDir = join(__dirname, "..");
const zipFilename = "decky-renodx.zip";

function parseArgs(argv) {
  return {
    draft: argv.includes("--private") || argv.includes("--draft"),
  };
}

function bumpVersion() {
  const packagePath = join(rootDir, "package.json");
  const packageJson = JSON.parse(readFileSync(packagePath, "utf-8"));
  const match = packageJson.version.match(/^(\d+)\.(\d+)\.(\d+)(?:-(.+)\.(\d+))?$/);
  if (!match) {
    throw new Error(`Invalid version: ${packageJson.version}`);
  }

  let [, major, minor, patch, preRelease, preReleaseNum] = match;
  if (preRelease && preReleaseNum) {
    packageJson.version = `${major}.${minor}.${patch}-${preRelease}.${Number(preReleaseNum) + 1}`;
  } else {
    packageJson.version = `${major}.${minor}.${Number(patch) + 1}`;
  }
  writeFileSync(packagePath, JSON.stringify(packageJson, null, 2) + "\n", "utf-8");
  return packageJson.version;
}

function cleanup() {
  for (const target of [join(rootDir, "dist"), join(rootDir, zipFilename)]) {
    if (existsSync(target)) {
      rmSync(target, { recursive: true, force: true });
    }
  }
}

function run(command) {
  execSync(command, { cwd: rootDir, stdio: "inherit" });
}

function publish(version, draft) {
  const tagName = `v${version}`;
  const args = [
    "release",
    "create",
    tagName,
    join(rootDir, zipFilename),
    "--title",
    `Decky RenoDX ${tagName}`,
    "--notes",
    `Release ${tagName}`,
  ];
  if (draft) args.push("--draft");
  if (version.includes("test") || version.includes("alpha") || version.includes("beta")) {
    args.push("--prerelease");
  }

  const result = spawnSync("gh", args, { cwd: rootDir, stdio: "inherit" });
  if (result.status !== 0) {
    throw new Error(`gh release create failed with exit code ${result.status}`);
  }
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const version = bumpVersion();
  run("pnpm run test");
  cleanup();
  run("pnpm run build");
  run("python scripts/create_zip.py");
  publish(version, options.draft);
  console.log(`Released v${version}`);
}

main();
