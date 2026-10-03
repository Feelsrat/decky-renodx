import { useCallback, useEffect, useRef, useState } from "react";
import { ButtonItem, ConfirmModal, DropdownItem, PanelSection, PanelSectionRow, ToggleField, showModal } from "@decky/ui";
import { toaster } from "@decky/api";
import { api, type ChangeResult, type Game, type GameState, type ManualDownload } from "../backend";
import { readLaunchOptions, updateLaunchOptions } from "../steam";
import { hasHdr, type LaunchSpec } from "../utils/launchOptions";
import { GameStatusCard, methodName } from "./GameStatusCard";
import { ImportModal, TextModal } from "./Modals";

interface Entry {
  state?: GameState;
  error?: string;
  loading: boolean;
  busy?: string;
  launchApplied: boolean | null;
}

const EMPTY: Entry = { loading: false, launchApplied: null };
const toast = (title: string, body: string, duration = 5000) => toaster.toast({ title, body, duration });

/** Per-game state lives under its appid, so a slow response can never land on another game. */
function useGames() {
  const [entries, setEntries] = useState<Record<string, Entry>>({});

  const patch = useCallback((appid: string, change: Partial<Entry>) => {
    setEntries((current) => ({ ...current, [appid]: { ...EMPTY, ...current[appid], ...change } }));
  }, []);

  const refresh = useCallback(async (appid: string) => {
    patch(appid, { loading: true });
    try {
      const result = await api.gameState(appid);
      if (result.status !== "success") {
        patch(appid, { loading: false, error: result.message });
        return;
      }
      let launchApplied: boolean | null = null;
      if (result.install.installed && result.install.launch) {
        const current = await readLaunchOptions(parseInt(appid, 10));
        launchApplied = current === null ? null : hasHdr(current, result.install.launch);
      }
      patch(appid, { loading: false, error: undefined, state: result, launchApplied });
    } catch (error) {
      patch(appid, { loading: false, error: String(error) });
    }
  }, [patch]);

  return { entries, patch, refresh };
}

export default function HdrPanel() {
  const [games, setGames] = useState<Game[]>([]);
  const [appid, setAppid] = useState("");
  const [method, setMethod] = useState("recommended");
  const [advanced, setAdvanced] = useState(false);
  const { entries, patch, refresh } = useGames();
  const entry = entries[appid] || EMPTY;
  const state = entry.state;
  const game = games.find((item) => item.appid === appid);
  const busy = Boolean(entry.busy);

  const loadGames = useCallback(async () => {
    try {
      const result = await api.listGames();
      if (result.status === "success") setGames(result.games);
      else toast("Game list failed", result.message || "");
    } catch (error) {
      toast("Game list failed", String(error));
    }
  }, []);

  useEffect(() => {
    loadGames();
  }, [loadGames]);

  useEffect(() => {
    setMethod("recommended");
    if (appid) refresh(appid);
  }, [appid]);

  /** Run one change for a game: one at a time per game, then sync launch options and refresh. */
  const running = useRef(new Set<string>());
  const run = async (target: string, label: string, action: () => Promise<ChangeResult>, mode: "apply" | "remove" = "apply"): Promise<ChangeResult | null> => {
    if (running.current.has(target)) return null;
    running.current.add(target);
    patch(target, { busy: label });
    try {
      const result = await action();
      if (result.status === "manual_required") {
        openImport(target, result as unknown as ManualDownload);
      } else if (result.status === "success") {
        const launch = mode === "remove"
          ? await updateLaunchOptions(target, null, [result.launch])
          : await updateLaunchOptions(target, result.launch ?? null, [result.previous_launch]);
        let body = result.message || "Done.";
        if (!launch.ok && launch.message) body += ` ${launch.message}`;
        toast(label, body, launch.ok ? 5000 : 12000);
        if (result.renodx_manual) toast("RenoDX", `${result.renodx_manual.mod_name} needs a manual download; a fallback was installed instead.`, 8000);
      } else {
        toast(`${label} failed`, result.message || "Unknown error.", 9000);
      }
      return result;
    } catch (error) {
      toast(`${label} failed`, String(error), 9000);
      api.logError(`${label}: ${error}`).catch(() => undefined);
      return null;
    } finally {
      running.current.delete(target);
      patch(target, { busy: undefined });
      refresh(target);
    }
  };

  const openImport = (target: string, manual: ManualDownload | null) => {
    const title = games.find((item) => item.appid === target)?.name || target;
    showModal(
      <ImportModal
        title={title}
        manual={manual}
        onImport={async (file) => {
          const result = await run(target, "RenoDX import", () => api.importRenodx(target, file));
          return result?.status === "success";
        }}
      />,
    );
  };

  const applyLaunch = async (spec: LaunchSpec) => {
    const result = await updateLaunchOptions(appid, spec, []);
    toast("Launch options", result.ok ? "HDR launch options applied." : result.message || "Could not update launch options.", result.ok ? 4000 : 12000);
    refresh(appid);
  };

  const simple = async (label: string, action: () => Promise<{ status: string; message: string }>) => {
    try {
      const result = await action();
      toast(label, result.message || (result.status === "success" ? "Done." : "Failed."));
    } catch (error) {
      toast(`${label} failed`, String(error));
    }
    refresh(appid);
  };

  const options = state?.method_options || [];
  const selected = options.find((item) => item.method === method) || options[0];
  const installed = Boolean(state?.install.installed);
  const removal = method === "sdr" || method === "native_hdr";
  const actionLabel = !selected ? "Install" : removal ? selected.label : installed ? `Switch to ${selected.label}` : `Install ${selected.label}`;
  const delay = state?.install.method === "special_k_delayed" ? Number(state.install.extra?.delay || 5) : 0;

  return (
    <PanelSection title="Per-Game HDR">
      <PanelSectionRow>
        <DropdownItem
          rgOptions={games.map((item) => ({ data: item.appid, label: item.name }))}
          selectedOption={appid}
          strDefaultLabel={games.length ? "Select a game…" : "No Steam games found"}
          onChange={(option) => setAppid(String(option.data))}
          onMenuWillOpen={() => loadGames()}
        />
      </PanelSectionRow>

      {appid && (
        <>
          {entry.error && !state ? (
            <PanelSectionRow>
              <div style={{ color: "#ff6b6b", fontSize: "0.85em" }}>{entry.error}</div>
            </PanelSectionRow>
          ) : (
            <GameStatusCard state={state} loading={entry.loading} launchApplied={entry.launchApplied} />
          )}

          {state && (
            <>
              <PanelSectionRow>
                <DropdownItem
                  label="HDR method"
                  disabled={busy}
                  rgOptions={options.map((item) => ({
                    data: item.method,
                    label: item.available ? (item.badge ? `${item.label} · ${item.badge}` : item.label) : `${item.label} (unavailable)`,
                  }))}
                  selectedOption={selected?.method}
                  onChange={(option) => setMethod(String(option.data))}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  disabled={busy || entry.loading || !selected?.available}
                  description={selected?.reason}
                  onClick={() => run(appid, removal ? methodName(method) : "HDR setup", () => api.install(appid, method))}
                >
                  {entry.busy || actionLabel}
                </ButtonItem>
              </PanelSectionRow>

              {installed && state.install.launch && entry.launchApplied === false && (
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={busy} onClick={() => applyLaunch(state.install.launch!)}>
                    Apply launch options
                  </ButtonItem>
                </PanelSectionRow>
              )}

              {installed && !removal && (
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={busy} onClick={() => run(appid, "Remove HDR", () => api.remove(appid), "remove")}>
                    Remove HDR
                  </ButtonItem>
                </PanelSectionRow>
              )}

              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy} onClick={() => openImport(appid, null)} description="Install a RenoDX mod you downloaded yourself (Nexus, Discord).">
                  Import downloaded RenoDX mod
                </ButtonItem>
              </PanelSectionRow>

              <PanelSectionRow>
                <ToggleField label="Advanced" checked={advanced} onChange={setAdvanced} />
              </PanelSectionRow>

              {advanced && (
                <>
                  <PanelSectionRow>
                    <DropdownItem
                      label="Game executable"
                      description={state.exe_path ? `HDR files go next to: ${state.target_dir}` : "No executable found"}
                      disabled={busy}
                      rgOptions={[
                        { data: "", label: state.exe_override ? "Automatic detection" : `Automatic (${state.exe_candidates[0]?.label || "none"})` },
                        ...state.exe_candidates.map((item) => ({ data: item.path, label: `${item.label} (${item.arch}-bit)` })),
                      ]}
                      selectedOption={state.exe_override ? state.exe_path : ""}
                      onChange={(option) => simple("Executable", () => api.setExecutable(appid, String(option.data)))}
                    />
                  </PanelSectionRow>
                  <PanelSectionRow>
                    <ButtonItem layout="below" disabled={busy || !installed} onClick={() => simple("Check install", () => api.verify(appid))}>
                      Check installed files
                    </ButtonItem>
                  </PanelSectionRow>
                  <PanelSectionRow>
                    <ToggleField
                      label="Special K HDR works"
                      description="Mark after confirming HDR in Special K's in-game menu; ranks Special K higher."
                      checked={Boolean(state.context.specialk_verified)}
                      disabled={busy}
                      onChange={(value) => simple("Special K", () => api.setSpecialKVerified(appid, value))}
                    />
                  </PanelSectionRow>
                  {delay > 0 && (
                    <PanelSectionRow>
                      <DropdownItem
                        label="Special K injection delay"
                        disabled={busy}
                        rgOptions={[3, 5, 8, 10, 15, 20, 30].map((seconds) => ({ data: seconds, label: `${seconds}s` }))}
                        selectedOption={delay}
                        onChange={(option) => run(appid, "Special K delay", () => api.setSpecialKDelay(appid, Number(option.data)))}
                      />
                    </PanelSectionRow>
                  )}
                  <PanelSectionRow>
                    <ButtonItem layout="below" onClick={() => viewLogs(appid, game?.name || appid)}>
                      View logs
                    </ButtonItem>
                  </PanelSectionRow>
                  <PanelSectionRow>
                    <ButtonItem layout="below" onClick={() => viewWiki(appid, game?.name || appid)}>
                      PCGamingWiki fixes
                    </ButtonItem>
                  </PanelSectionRow>
                  <PanelSectionRow>
                    <ButtonItem layout="below" disabled={busy} onClick={() => simple("Caches", () => api.resetCaches())} description="Re-download the RenoDX mod list and PCGamingWiki data.">
                      Reset caches
                    </ButtonItem>
                  </PanelSectionRow>
                  <PanelSectionRow>
                    <ButtonItem
                      layout="below"
                      disabled={busy}
                      description="Last resort. Deletes the game's Proton prefix; saves stored only in the prefix are lost."
                      onClick={() =>
                        showModal(
                          <ConfirmModal
                            strTitle="Reset Proton prefix?"
                            strDescription={`This deletes the Proton prefix for ${game?.name || appid}. Steam rebuilds it on the next launch, but settings or saves stored only in the prefix (not Steam Cloud) are lost.`}
                            strOKButtonText="Delete prefix"
                            onOK={() => run(appid, "Proton prefix", () => api.resetPrefix(appid), "remove")}
                          />,
                        )
                      }
                    >
                      Reset Proton prefix
                    </ButtonItem>
                  </PanelSectionRow>
                </>
              )}
            </>
          )}
        </>
      )}
    </PanelSection>
  );
}

async function viewLogs(appid: string, title: string) {
  try {
    const result = await api.logs(appid);
    showModal(
      <TextModal
        title={`Logs: ${title}`}
        tabs={[
          { title: "Plugin", content: result.plugin_log || "Nothing logged for this game yet." },
          { title: "Proton", content: result.proton_log || `No Proton log at ${result.proton_log_path}. Add PROTON_LOG=1 to the launch options and launch once to create one.` },
        ]}
      />,
    );
  } catch (error) {
    toast("Logs unavailable", String(error));
  }
}

async function viewWiki(appid: string, title: string) {
  try {
    const result = await api.pcgwFixes(appid);
    const list = (items?: string[]) => (items?.length ? items.map((item) => `• ${item}`).join("\n") : "No entries.");
    showModal(
      <TextModal
        title={`PCGamingWiki: ${result.page_name || title}`}
        tabs={
          result.status === "success"
            ? [
                { title: "Essential improvements", content: list(result.essential_improvements) },
                { title: "Issues fixed", content: list(result.issues_fixed) },
              ]
            : [{ title: "Error", content: result.message || "Unavailable." }]
        }
      />,
    );
  } catch (error) {
    toast("PCGamingWiki unavailable", String(error));
  }
}
