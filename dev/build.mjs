// Bundle the plugin UI with browser shims for @decky/ui and @decky/api (see dev/server.py).
import { build, context } from "esbuild";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const dev = dirname(fileURLToPath(import.meta.url));
const options = {
  entryPoints: [join(dev, "harness.tsx")],
  bundle: true,
  outfile: join(dev, "dist", "harness.js"),
  jsx: "automatic",
  sourcemap: true,
  alias: { "@decky/ui": join(dev, "shim", "ui.tsx"), "@decky/api": join(dev, "shim", "api.ts") },
  define: { "process.env.NODE_ENV": '"development"' },
  logLevel: "info",
};

if (process.argv.includes("--watch")) {
  await (await context(options)).watch();
} else {
  await build(options);
}
