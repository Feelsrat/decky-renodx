#!/usr/bin/env node
import { existsSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { execFileSync } from "child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const rootDir = join(__dirname, "..");
let failed = false;

function run(command, args) {
  execFileSync(command, args, { cwd: rootDir, stdio: "pipe" });
}

function check(condition, message) {
  if (condition) {
    console.log(`OK: ${message}`);
  } else {
    console.error(`FAIL: ${message}`);
    failed = true;
  }
}

function checkJson() {
  const plugin = JSON.parse(readFileSync(join(rootDir, "plugin.json"), "utf-8"));
  const pkg = JSON.parse(readFileSync(join(rootDir, "package.json"), "utf-8"));
  check(plugin.name === "Decky RenoDX", "plugin name");
  check(pkg.name === "decky-renodx", "package name");
  check(Boolean(pkg.version), "package version");
}

function checkRequiredFiles() {
  for (const file of ["plugin.json", "package.json", "main.py", "README.md", "dist/index.js"]) {
    check(existsSync(join(rootDir, file)), `${file} exists`);
  }
  for (const file of ["defaults/assets/reshade-install.sh", "defaults/assets/reshade-game-manager.sh", "defaults/assets/reshade-uninstall.sh"]) {
    check(existsSync(join(rootDir, file)), `${file} exists`);
  }
}

function main() {
  try {
    checkJson();
    run("python", ["-m", "py_compile", "main.py"]);
    console.log("OK: Python syntax");
    run("python", ["scripts/compat_db.py", "validate"]);
    console.log("OK: compatibility.json schema");
    run("python", ["-m", "unittest", "tests.test_backend_mocks"]);
    console.log("OK: backend tests");
    run(process.execPath, ["--experimental-strip-types", "scripts/test_hdr_logic.ts"]);
    console.log("OK: frontend logic tests");
    run(process.execPath, [join(rootDir, "node_modules", "typescript", "bin", "tsc"), "--noEmit", "--skipLibCheck"]);
    console.log("OK: TypeScript types");
    run(process.execPath, [join(rootDir, "node_modules", "rollup", "dist", "bin", "rollup"), "-c"]);
    console.log("OK: frontend build");
    checkRequiredFiles();
  } catch (error) {
    console.error(error.stdout?.toString() || "");
    console.error(error.stderr?.toString() || error.message);
    failed = true;
  }

  if (failed) {
    process.exit(1);
  }
}

main();
