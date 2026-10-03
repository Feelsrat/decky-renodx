import { callable } from "@decky/api";
import type { LaunchSpec } from "./utils/launchOptions";

export interface Game {
  appid: string;
  name: string;
}

export interface Recommendation {
  method: string;
  score: number;
  reason: string;
  confidence: string;
  notes?: string[];
  warnings?: string[];
  manual_steps?: string[];
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
  outdated?: boolean;
  files_ok?: boolean;
  missing?: string[];
  launch: LaunchSpec | null;
  extra?: Record<string, any>;
  message: string;
}

export interface GameState {
  status: "success";
  appid: string;
  title: string;
  install_path: string;
  exe_path: string;
  target_dir: string;
  exe_override: boolean;
  exe_candidates: { path: string; label: string; arch: string }[];
  context: GameContext;
  recommendations: Recommendation[];
  method_options: MethodOption[];
  install: InstallStatus;
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
  install: callable<[appid: string, method: string], ChangeResult>("install_hdr_method"),
  remove: callable<[appid: string], ChangeResult>("remove_hdr"),
  importRenodx: callable<[appid: string, file: string], ChangeResult>("import_renodx_for_game"),
  downloads: callable<[], { status: string; files: DownloadFile[] }>("find_recent_renodx_downloads"),
  verify: callable<[appid: string], Simple>("verify_hdr_installation"),
  setExecutable: callable<[appid: string, path: string], Simple>("set_game_executable"),
  setSpecialKVerified: callable<[appid: string, verified: boolean], Simple>("set_special_k_verified"),
  setSpecialKDelay: callable<[appid: string, seconds: number], ChangeResult>("set_special_k_delay"),
  resetPrefix: callable<[appid: string], ChangeResult>("reset_game_proton_prefix"),
  logs: callable<[appid: string], { status: string; plugin_log: string; proton_log: string; proton_log_path: string; message?: string }>("get_per_game_log"),
  pcgwFixes: callable<[appid: string], { status: string; page_name?: string; essential_improvements?: string[]; issues_fixed?: string[]; message?: string }>("get_pcgw_improvements_issues"),
  resetCaches: callable<[], Simple>("reset_plugin_caches"),
  openUrl: callable<[url: string], Simple>("open_url"),
  runtimeStatus: callable<[], { status: string; installed: boolean; components: Record<string, string | boolean> }>("get_runtime_status"),
  removeRuntime: callable<[], Simple>("remove_runtime"),
  updateStatus: callable<[], UpdateStatus>("get_update_status"),
  checkUpdate: callable<[force: boolean], UpdateStatus>("check_update"),
  installUpdate: callable<[], UpdateStatus>("install_update"),
  logError: callable<[message: string], void>("log_error"),
};
