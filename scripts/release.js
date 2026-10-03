#!/usr/bin/env node
// Bump the version, run the tests, then commit, tag and push.
// The "Release" GitHub Actions workflow builds decky-renodx.zip from the tag and publishes it.
import { execFileSync } from "child_process";
import { readFileSync, writeFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const rootDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const packagePath = join(rootDir, "package.json");

function usage() {
  console.log("Usage: pnpm run release -- [patch|minor|major|<x.y.z>] [--no-push]");
}

function git(...args) {
  return execFileSync("git", args, { cwd: rootDir, encoding: "utf-8" }).trim();
}

function nextVersion(current, bump) {
  if (/^\d+\.\d+\.\d+$/.test(bump)) return bump;
  const [major, minor, patch] = current.split("-")[0].split(".").map(Number);
  if (bump === "major") return `${major + 1}.0.0`;
  if (bump === "minor") return `${major}.${minor + 1}.0`;
  if (bump === "patch") return `${major}.${minor}.${patch + 1}`;
  throw new Error(`Unknown version bump: ${bump}`);
}

const args = process.argv.slice(2);
if (args.includes("--help") || args.includes("-h")) {
  usage();
  process.exit(0);
}
const push = !args.includes("--no-push");
const bump = args.find((arg) => !arg.startsWith("--")) || "patch";

if (git("status", "--porcelain")) {
  console.error("Working tree is not clean; commit or stash first.");
  process.exit(1);
}

const pkg = JSON.parse(readFileSync(packagePath, "utf-8"));
const version = nextVersion(pkg.version, bump);
const tag = `v${version}`;
if (git("tag", "--list", tag)) {
  console.error(`Tag ${tag} already exists.`);
  process.exit(1);
}

pkg.version = version;
writeFileSync(packagePath, `${JSON.stringify(pkg, null, 2)}\n`, "utf-8");
try {
  execFileSync(process.execPath, [join(rootDir, "scripts", "test.js")], { cwd: rootDir, stdio: "inherit" });
} catch {
  git("checkout", "--", "package.json");
  console.error("Tests failed; version bump reverted.");
  process.exit(1);
}

git("add", "package.json");
git("commit", "-m", `Release ${tag}`);
git("tag", "-a", tag, "-m", `Decky RenoDX ${tag}`);
if (push) {
  const branch = git("rev-parse", "--abbrev-ref", "HEAD");
  execFileSync("git", ["push", "origin", branch, "--follow-tags"], { cwd: rootDir, stdio: "inherit" });
  console.log(`Pushed ${tag}; the Release workflow will publish decky-renodx.zip.`);
} else {
  console.log(`Tagged ${tag} locally. Push with: git push origin HEAD --follow-tags`);
}
