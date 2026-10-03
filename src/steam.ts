import { mergeHdr, stripHdr, type LaunchSpec } from "./utils/launchOptions";

/** Current launch options, or null when Steam will not tell us (then we never write blindly). */
export async function readLaunchOptions(appid: number): Promise<string | null> {
  try {
    const details = (window as any).appDetailsStore?.GetAppDetails?.(appid);
    if (details && typeof details.strLaunchOptions === "string") return details.strLaunchOptions;
  } catch {
    // fall through to the async API
  }
  return new Promise((resolve) => {
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
      registration = SteamClient.Apps.RegisterForAppDetails(appid, (data) => {
        finish(typeof data?.strLaunchOptions === "string" ? data.strLaunchOptions : null);
      });
    } catch {
      finish(null);
    }
  });
}

export interface LaunchUpdate {
  ok: boolean;
  value: string;
  message?: string;
}

/** Apply ``spec`` (or just remove ``previous``) in Steam's launch options. */
export async function updateLaunchOptions(appid: string, spec: LaunchSpec | null, previous: (LaunchSpec | null | undefined)[]): Promise<LaunchUpdate> {
  const id = parseInt(appid, 10);
  const current = await readLaunchOptions(id);
  if (current === null) {
    const value = spec?.preview || "";
    return {
      ok: false,
      value,
      message: spec ? `Steam did not return this game's launch options. Set them manually: ${value}` : "Steam did not return this game's launch options; remove the HDR options manually.",
    };
  }
  const value = spec ? mergeHdr(current, spec, previous) : stripHdr(current, previous);
  if (value !== current) {
    SteamClient.Apps.SetAppLaunchOptions(id, value);
  }
  return { ok: true, value };
}
