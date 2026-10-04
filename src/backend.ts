import { callable } from "@decky/api";
import type { LaunchSpec } from "./utils/launchOptions";

export interface Game {
  appid: string;
  name: string;
  kind: "steam" | "shortcut";
  /** The RenoDX wiki lists a mod for this game. */
  renodx?: boolean;
}

export interface ScreenStatus {
  status: string;
  name?: string;
  connector?: string;
  peak_nits?: number | null;
  avg_nits?: number | null;
  source?: string;
  auto_brightness?: boolean;
  message?: string;
}

export interface Badge {
  status: string;
  level: "on" | "renodx" | "native" | "engine" | "none";
  label?: string;
  detail?: string;
  message?: string;
}

export interface Recommendation {
  method: string;
  score: number;
  reason: string;
  confidence: string;
  notes?: string[];
  warnings?: string[];
  manual_steps?: string[];
  /** The RenoDX wiki's notes for the matched mod (settings, known issues). */
  wiki_notes?: string[];
  renodx_status?: string;
  renodx_match_type?: string;
  manual_download?: boolean;
}

export interface MethodOption {
  method: string;
  label: string;
  available: boolean;
  reason: string;
  badge?: string;
  score?: number | null;
}

export interface GameContext {
  api: string;
  api_source: string;
  architecture: string;
  hook: string;
  engine: string;
  anti_cheat: string[];
  linux_build: boolean;
  native_hdr: string;
  pcgw_url: string;
  specialk_verified: boolean;
  renodx_match: { name: string; status: string; match_type: string; manual_url?: string } | null;
  renodx_error?: string;
  pcgw_error?: string;
  notes: string[];
}

export interface InstallStatus {
  installed: boolean;
  legacy?: boolean;
  method?: string;
  dll?: string;
  target_dir?: string;
  installed_version?: string;
  installed_at?: string;
  outdated?: boolean;
  files_ok?: boolean;
  missing?: string[];
  game_updated?: boolean;
  launch_outdated?: boolean;
  needs_repair?: boolean;
  launch: LaunchSpec | null;
  extra?: Record<string, any>;
  message: string;
}

export interface GameState {
  status: "success";
  appid: string;
  title: string;
  kind: "steam" | "shortcut";
  launch_options_hint: string | null;
  install_path: string;
  exe_path: string;
  target_dir: string;
  exe_override: boolean;
  exe_candidates: { path: string; label: string; arch: string }[];
  context: GameContext;
  recommendations: Recommendation[];
  method_options: MethodOption[];
  install: InstallStatus;
  user_result: "" | "worked" | "failed";
}

export interface DisplayStatus {
  supported: boolean | null;
  enabled: boolean | null;
  game_mode: boolean;
}

export interface ErrorResult {
  status: "error";
  message: string;
  busy?: boolean;
}

export interface ManualDownload {
  manual_download: true;
  url: string;
  mod_name: string;
  message: string;
}

export interface ChangeResult {
  status: "success" | "error" | "manual_required";
  message: string;
  method?: string;
  launch?: LaunchSpec | null;
  previous_launch?: LaunchSpec | null;
  launch_options?: string;
  warnings?: string[];
  manual_steps?: string[];
  renodx_manual?: ManualDownload | null;
  /** Methods tried before the one that got installed, with why they failed. */
  failed_attempts?: string[];
  url?: string;
  mod_name?: string;
  busy?: boolean;
}

export interface DownloadFile {
  path: string;
  name: string;
  size: number;
  modified: number;
}

export interface UpdateStatus {
  ok: boolean;
  current?: string;
  latest?: string;
  elevated?: boolean;
  hasUpdate?: boolean;
  canInstall?: boolean;
  requiresRestart?: boolean;
  message: string;
}

type Simple = { status: "success" | "error"; message: string };

export const api = {
  listGames: callable<[], { status: string; games: Game[]; message?: string }>("list_installed_games"),
  gameState: callable<[appid: string], GameState | ErrorResult>("get_game_state"),
  screenStatus: callable<[], ScreenStatus>("get_screen_status"),
  setAutoBrightness: callable<[enabled: boolean], ScreenStatus>("set_auto_brightness"),
  libraryBadge: callable<[appid: string, title: string], Badge>("get_library_badge"),
  libraryBadges: callable<[items: { appid: string; title: string }[]], { status: string; badges: Record<string, Pick<Badge, "level" | "label">>; pending?: string[]; message?: string }>("get_library_badges"),
  install: callable<[appid: string, method: string], ChangeResult>("install_hdr_method"),
  remove: callable<[appid: string], ChangeResult>("remove_hdr"),
  repair: callable<[appid: string], ChangeResult>("repair_hdr"),
  setLaunchKeep: callable<[appid: string, keep: { args: string[]; dlls: string[]; env: string[] }], Simple>("set_launch_keep"),
  setResult: callable<[appid: string, result: string], Simple>("set_game_result"),
  displayStatus: callable<[], DisplayStatus & { status: string }>("get_display_status"),
  importRenodx: callable<[appid: string, file: string], ChangeResult>("import_renodx_for_game"),
  downloads: callable<[], { status: string; files: DownloadFile[] }>("find_recent_renodx_downloads"),
  verify: callable<[appid: string], Simple>("verify_hdr_installation"),
  setExecutable: callable<[appid: string, path: string], Simple>("set_game_executable"),
  setSpecialKVerified: callable<[appid: string, verified: boolean], Simple>("set_special_k_verified"),
  resetPrefix: callable<[appid: string], ChangeResult>("reset_game_proton_prefix"),
  logs: callable<[appid: string], {
    status: string; plugin_log: string; proton_log: string; proton_log_path: string;
    reshade_log?: string; reshade_log_path?: string; checks?: string[]; message?: string;
  }>("get_per_game_log"),
  resetCaches: callable<[], Simple>("reset_plugin_caches"),
  openUrl: callable<[url: string], Simple>("open_url"),
  runtimeStatus: callable<[], { status: string; installed: boolean; components: Record<string, string | boolean> }>("get_runtime_status"),
  removeRuntime: callable<[], Simple>("remove_runtime"),
  updateStatus: callable<[], UpdateStatus>("get_update_status"),
  checkUpdate: callable<[force: boolean], UpdateStatus>("check_update"),
  installUpdate: callable<[], UpdateStatus>("install_update"),
  logError: callable<[message: string], void>("log_error"),
};
