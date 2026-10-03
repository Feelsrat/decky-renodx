import { useCallback, useEffect, useRef, useState } from "react";
import { Router } from "@decky/ui";
import { api, type DisplayStatus, type Game, type GameState } from "./backend";
import { readLaunchOptions, type GameRef } from "./steam";
import { hasHdr } from "./utils/launchOptions";

export interface Entry {
  state?: GameState;
  error?: string;
  loading: boolean;
  busy?: string;
  launchApplied: boolean | null;
}

export const EMPTY: Entry = { loading: false, launchApplied: null };
const LAST_GAME = "decky-renodx:last-game";

export const gameRef = (state: GameState): GameRef => ({ appid: state.appid, kind: state.kind, hint: state.launch_options_hint });

function storage(key: string, value?: string): string {
  try {
    if (value !== undefined) localStorage.setItem(key, value);
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function lastPlayed(appid: string): number {
  try {
    return (window as any).appStore?.GetAppOverviewByAppID?.(parseInt(appid, 10))?.rt_last_time_played || 0;
  } catch {
    return 0;
  }
}

export function runningAppid(): string {
  try {
    const app = Router.MainRunningApp as { appid?: number | string } | undefined;
    return app?.appid ? String(app.appid) : "";
  } catch {
    return "";
  }
}

/** Running game first, then most recently played, then A-Z. */
export function sortGames(games: Game[], running: string): Game[] {
  return [...games].sort((a, b) =>
    Number(b.appid === running) - Number(a.appid === running) || lastPlayed(b.appid) - lastPlayed(a.appid) || a.name.localeCompare(b.name),
  );
}

/** Per-game state lives under its appid, so a slow response can never land on another game. */
export function useGames() {
  const [games, setGames] = useState<Game[]>([]);
  const [appid, setAppidState] = useState("");
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [display, setDisplay] = useState<DisplayStatus | null>(null);
  const [running, setRunning] = useState(runningAppid());
  const appidRef = useRef(appid);
  appidRef.current = appid;

  const patch = useCallback((id: string, change: Partial<Entry>) => {
    setEntries((current) => ({ ...current, [id]: { ...EMPTY, ...current[id], ...change } }));
  }, []);

  const refresh = useCallback(async (id: string) => {
    patch(id, { loading: true });
    try {
      const result = await api.gameState(id);
      if (result.status !== "success") {
        patch(id, { loading: false, error: result.message });
        return;
      }
      let launchApplied: boolean | null = null;
      if (result.install.installed && result.install.launch) {
        const current = await readLaunchOptions(gameRef(result));
        launchApplied = current === null ? null : hasHdr(current, result.install.launch);
      }
      patch(id, { loading: false, error: undefined, state: result, launchApplied });
    } catch (error) {
      patch(id, { loading: false, error: String(error) });
    }
  }, [patch]);

  const setAppid = useCallback((id: string) => {
    storage(LAST_GAME, id);
    setAppidState(id);
  }, []);

  const loadGames = useCallback(async () => {
    const nowRunning = runningAppid();
    setRunning(nowRunning);
    const result = await api.listGames();
    if (result.status !== "success") throw new Error(result.message || "Could not list games.");
    const sorted = sortGames(result.games, nowRunning);
    setGames(sorted);
    if (!appidRef.current) {
      const preferred = [nowRunning, storage(LAST_GAME)].find((id) => id && sorted.some((game) => game.appid === id));
      if (preferred) setAppid(preferred);
    }
    return sorted;
  }, [setAppid]);

  const refreshDisplay = useCallback(() => {
    api.displayStatus().then((result) => setDisplay(result.status === "success" ? result : null)).catch(() => setDisplay(null));
  }, []);

  /**
   * The plugin stays mounted (alwaysRender), so re-check everything each time the
   * Quick Access Menu opens: games may have started, updated, or had their launch
   * options edited since. Jump to a newly started game, but not when the user picked
   * another one while the same game kept running.
   */
  const lastRunning = useRef<string | null>(null);
  const opened = useCallback(async () => {
    refreshDisplay();
    const sorted = await loadGames();
    const nowRunning = runningAppid();
    const started = lastRunning.current !== null && nowRunning && nowRunning !== lastRunning.current;
    lastRunning.current = nowRunning;
    if (started && sorted.some((game) => game.appid === nowRunning) && nowRunning !== appidRef.current) {
      setAppid(nowRunning);
    } else if (appidRef.current) {
      refresh(appidRef.current);
    }
  }, [loadGames, refresh, refreshDisplay, setAppid]);

  useEffect(() => {
    if (appid) refresh(appid);
  }, [appid, refresh]);

  return { games, appid, setAppid, entries, patch, refresh, loadGames, display, refreshDisplay, running, opened };
}
