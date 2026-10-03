// Merging Decky RenoDX's HDR launch options into whatever the user already has.
// Pure functions only, so they can be tested with plain Node.

export interface LaunchSpec {
  env: Record<string, string>;
  dll_overrides: Record<string, string>;
  args: string[];
  wrapper: string[];
  preview?: string;
}

const COMMAND = "%command%";
const PROXY_DLLS = ["dxgi", "d3d9", "d3d8", "d3d11", "d3d12", "ddraw", "dinput8", "opengl32"];
const HDR_ENV_KEYS = ["PROTON_ENABLE_HDR", "DXVK_HDR", "ENABLE_HDR_WSI", "ENABLE_GAMESCOPE_WSI"];
const WRAPPER_SCRIPT = "specialk-delayed-launch.sh";

/** Split on whitespace while keeping quoted sections (and the quotes) intact. */
export function tokenize(input: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let quote: string | null = null;
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (quote) {
      current += ch;
      if (ch === "\\" && quote === '"' && i + 1 < input.length) {
        current += input[++i];
      } else if (ch === quote) {
        quote = null;
      }
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      current += ch;
    } else if (/\s/.test(ch)) {
      if (current) tokens.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  if (current) tokens.push(current);
  return tokens;
}

/** Double-quote for Steam's shell; mirrors backend/launch.py:quote. */
export function quote(value: string): string {
  if (value && /^[A-Za-z0-9\-_./=:%+,]+$/.test(value)) return value;
  return `"${value.replace(/(["\\$`])/g, "\\$1")}"`;
}

function unquote(value: string): string {
  if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value[value.length - 1] === value[0]) {
    const inner = value.slice(1, -1);
    return value[0] === '"' ? inner.replace(/\\(["\\$`])/g, "$1") : inner;
  }
  return value;
}

function assignment(token: string): [string, string] | null {
  const match = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/s.exec(token);
  return match ? [match[1], unquote(match[2])] : null;
}

function parseOverrides(value: string): [string, string][] {
  return value
    .split(";")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const index = entry.indexOf("=");
      return (index < 0 ? [entry.toLowerCase(), ""] : [entry.slice(0, index).toLowerCase(), entry.slice(index + 1)]) as [string, string];
    });
}

function formatOverrides(entries: [string, string][]): string {
  return `WINEDLLOVERRIDES="${entries.map(([dll, mode]) => (mode ? `${dll}=${mode}` : dll)).join(";")}"`;
}

interface Split {
  prefix: string[];
  args: string[];
  hadCommand: boolean;
}

function split(options: string): Split {
  const tokens = tokenize(options || "");
  const index = tokens.indexOf(COMMAND);
  if (index < 0) return { prefix: [], args: tokens, hadCommand: false };
  return { prefix: tokens.slice(0, index), args: tokens.slice(index + 1), hadCommand: true };
}

function join(parts: Split): string {
  if (!parts.hadCommand && !parts.prefix.length) return parts.args.join(" ");
  if (!parts.prefix.length && !parts.args.length) return "";
  return [...parts.prefix, COMMAND, ...parts.args].join(" ");
}

/** Old plugin versions always added d3dcompiler_47=n or the bash wrapper; those are safe to strip. */
function hasLegacyTokens(prefix: string[]): boolean {
  return prefix.some((token) => {
    const parsed = assignment(token);
    if (parsed && parsed[0] === "WINEDLLOVERRIDES") {
      return parseOverrides(parsed[1]).some(([dll, mode]) => dll === "d3dcompiler_47" && mode === "n");
    }
    return token.includes(WRAPPER_SCRIPT) && !token.includes("/launch/");
  });
}

/**
 * Remove HDR launch options this plugin added: the given specs exactly, plus
 * anything an older plugin version left behind. The user's own options stay.
 */
export function stripHdr(options: string, specs: (LaunchSpec | null | undefined)[] = []): string {
  const parts = split(options);
  const known = specs.filter(Boolean) as LaunchSpec[];
  const legacy = hasLegacyTokens(parts.prefix);
  const ourDlls = new Set(known.flatMap((spec) => Object.keys(spec.dll_overrides || {}).map((dll) => dll.toLowerCase())));
  if (legacy) {
    ourDlls.add("d3dcompiler_47");
    PROXY_DLLS.forEach((dll) => ourDlls.add(dll));
  }
  const ourEnv = new Set([...HDR_ENV_KEYS, ...known.flatMap((spec) => Object.keys(spec.env || {}))]);
  if (legacy) ourEnv.add("PROTON_LOG");

  const prefix: string[] = [];
  for (let i = 0; i < parts.prefix.length; i++) {
    const token = parts.prefix[i];
    if (token === "bash" && parts.prefix[i + 1]?.includes(WRAPPER_SCRIPT)) {
      // Wrapper: "bash <script> <appid> <delay> <injector> [--]" runs up to %command%.
      break;
    }
    const parsed = assignment(token);
    if (parsed) {
      const [key, value] = parsed;
      if (ourEnv.has(key)) continue;
      if (key === "STEAM_COMPAT_DATA_PATH" && legacy) continue;
      if (key === "WINEDLLOVERRIDES") {
        const remaining = parseOverrides(value).filter(([dll, mode]) => !(ourDlls.has(dll) && (dll === "d3dcompiler_47" || mode === "n,b")));
        if (remaining.length) prefix.push(formatOverrides(remaining));
        continue;
      }
    }
    prefix.push(token);
  }

  let args = parts.args;
  for (const spec of known) {
    for (const arg of spec.args || []) {
      const index = args.indexOf(arg);
      if (index >= 0) args = [...args.slice(0, index), ...args.slice(index + 1)];
    }
  }
  return join({ prefix, args, hadCommand: parts.hadCommand });
}

/** Strip previous HDR options, then put the new ones first (env must lead the command line). */
export function mergeHdr(options: string, spec: LaunchSpec, previous: (LaunchSpec | null | undefined)[] = []): string {
  const parts = split(stripHdr(options, [...previous, spec]));
  const userPrefix: string[] = [];
  const userOverrides: [string, string][] = [];
  for (const token of parts.prefix) {
    const parsed = assignment(token);
    if (parsed && parsed[0] === "WINEDLLOVERRIDES") {
      userOverrides.push(...parseOverrides(parsed[1]));
    } else if (!(parsed && parsed[0] in spec.env)) {
      userPrefix.push(token);
    }
  }
  const overrides = new Map<string, string>(userOverrides);
  for (const [dll, mode] of Object.entries(spec.dll_overrides || {})) overrides.set(dll.toLowerCase(), mode);

  const prefix = [
    ...Object.entries(spec.env || {}).map(([key, value]) => `${key}=${value}`),
    ...(overrides.size ? [formatOverrides([...overrides.entries()])] : []),
    ...userPrefix,
    ...(spec.wrapper || []).map(quote),
  ];
  const args = [...parts.args];
  for (const arg of spec.args || []) if (!args.includes(arg)) args.push(arg);
  return join({ prefix, args, hadCommand: true });
}

/** True when every part of the spec is present in the current launch options. */
export function hasHdr(options: string, spec: LaunchSpec): boolean {
  const parts = split(options);
  const env = new Map<string, string>();
  let overrides = new Map<string, string>();
  for (const token of parts.prefix) {
    const parsed = assignment(token);
    if (!parsed) continue;
    if (parsed[0] === "WINEDLLOVERRIDES") overrides = new Map(parseOverrides(parsed[1]));
    else env.set(parsed[0], parsed[1]);
  }
  const envOk = Object.entries(spec.env || {}).every(([key, value]) => env.get(key) === value);
  const dllOk = Object.entries(spec.dll_overrides || {}).every(([dll, mode]) => overrides.get(dll.toLowerCase()) === mode);
  const wrapperOk = !(spec.wrapper || []).length || parts.prefix.join(" ").includes((spec.wrapper || []).map(quote).join(" "));
  const argsOk = (spec.args || []).every((arg) => parts.args.includes(arg));
  return parts.hadCommand && envOk && dllOk && wrapperOk && argsOk;
}
