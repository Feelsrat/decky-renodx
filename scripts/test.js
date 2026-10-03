#!/usr/bin/env node
// Everything CI runs: Python tests, compat DB validation, launch option tests, types, build, package check.
import { execFileSync } from "child_process";
import { existsSync, readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const rootDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON || "python3";
let failed = false;

function step(name, command, args) {
  try {
    execFileSync(command, args, { cwd: rootDir, stdio: "pipe" });
    console.log(`OK: ${name}`);
  } catch (error) {
    failed = true;
    console.error(`FAIL: ${name}`);
    console.error(error.stdout?.toString() || "");
    console.error(error.stderr?.toString() || error.message);
  }
}

function check(condition, message) {
  console.log(`${condition ? "OK" : "FAIL"}: ${message}`);
  if (!condition) failed = true;
}

const plugin = JSON.parse(readFileSync(join(rootDir, "plugin.json"), "utf-8"));
const pkg = JSON.parse(readFileSync(join(rootDir, "package.json"), "utf-8"));
check(plugin.name === "Decky RenoDX", "plugin name");
check(pkg.name === "decky-renodx" && /^\d+\.\d+\.\d+/.test(pkg.version), "package name and version");
check(!plugin.flags.includes("debug"), "release build has no debug flag");

step("Python syntax", python, ["-m", "compileall", "-q", "main.py", "backend", "scripts", "tests"]);
step("compatibility.json schema", python, ["scripts/compat_db.py", "validate"]);
step("backend tests", python, ["-m", "unittest", "discover", "-s", "tests", "-t", "."]);
step("launch option tests", process.execPath, ["--experimental-strip-types", "--no-warnings", "tests/launchOptions.test.ts"]);
step("TypeScript types", process.execPath, [join(rootDir, "node_modules", "typescript", "bin", "tsc"), "--noEmit", "--skipLibCheck"]);
step("frontend build", process.execPath, [join(rootDir, "node_modules", "rollup", "dist", "bin", "rollup"), "-c"]);
step("dev harness build", process.execPath, ["dev/build.mjs"]);
for (const file of ["dist/index.js", "LICENSE", "defaults/assets/specialk-delayed-launch.sh"]) {
  check(existsSync(join(rootDir, file)), `${file} exists`);
}

process.exit(failed ? 1 : 0);
