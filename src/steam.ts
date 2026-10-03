import { mergeHdr, preexisting, stripHdr, type LaunchKeep, type LaunchSpec } from "./utils/launchOptions";

export interface GameRef {
  appid: string;
  kind: "steam" | "shortcut";
  /** Launch options from shortcuts.vdf, used when Steam's API will not say. */
  hint?: string | null;
}

/** Current launch options, or null when Steam will not tell us (then we never write blindly). */
export async function readLaunchOptions(game: GameRef): Promise<string | null> {
  const id = parseInt(game.appid, 10);
  try {
    const details = (window as any).appDetailsStore?.GetAppDetails?.(id);
    if (details && typeof details.strLaunchOptions === "string") return details.strLaunchOptions;
  } catch {
    // fall through to the async API
  }
  const fromApi = await new Promise<string | null>((resolve) => {
    let done = false;
    let registration: { unregister(): void } | null = null;
    const finish = (value: string | null) => {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      // Unregister after the callback returns; some Steam builds dislike re-entrant unregister.
      window.setTimeout(() => registration?.unregister(), 0);
      resolve(value);
    };
    const timer = window.setTimeout(() => finish(null), 3000);
    try {
      registration = SteamClient.Apps.RegisterForAppDetails(id, (data) => {
        finish(typeof data?.strLaunchOptions === "string" ? data.strLaunchOptions : null);
      });
    } catch {
      finish(null);
    }
  });
  if (fromApi !== null) return fromApi;
  return game.kind === "shortcut" && typeof game.hint === "string" ? game.hint : null;
}

function writeLaunchOptions(game: GameRef, value: string) {
  const id = parseInt(game.appid, 10);
  if (game.kind === "shortcut") SteamClient.Apps.SetShortcutLaunchOptions(id, value);
  else SteamClient.Apps.SetAppLaunchOptions(id, value);
}

export interface LaunchUpdate {
  ok: boolean;
  value: string;
  message?: string;
  /** Parts of the new spec the user already had (store them so removal keeps them). */
  keep?: LaunchKeep;
}

/** Apply ``spec`` (or just remove ``previous``) in Steam's launch options. */
export async function updateLaunchOptions(game: GameRef, spec: LaunchSpec | null, previous: (LaunchSpec | null | undefined)[]): Promise<LaunchUpdate> {
  const current = await readLaunchOptions(game);
  if (current === null) {
    const value = spec ? mergeHdr("", spec) : "";
    return {
      ok: false,
      value,
      message: spec
        ? `Steam did not return this game's launch options. Set them by hand: ${value}`
        : "Steam did not return this game's launch options; remove the HDR options by hand.",
    };
  }
  let keep: LaunchKeep | undefined;
  if (spec) {
    // What the user had before any HDR options of ours were added.
    keep = preexisting(stripHdr(current, previous), spec);
  }
  const value = spec ? mergeHdr(current, spec, previous) : stripHdr(current, previous);
  if (value !== current) writeLaunchOptions(game, value);
  return { ok: true, value, keep };
}
