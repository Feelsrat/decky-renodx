import { useState, useEffect } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  DropdownItem,
  Field,
  ModalRoot,
} from "@decky/ui";
import { callable, toaster } from "@decky/api";

// New Callables
const getHdrRecommendation = callable<[string, string, string], any>("get_hdr_recommendation");
const runSurgicalUninstall = callable<[string, string], any>("run_surgical_uninstall");
const getPerGameLog = callable<[string], any>("get_per_game_log");
const updateSkConfigValue = callable<[string, string, string, string, string], any>("update_sk_config_value");
const setSpecialKVerified = callable<[string, boolean], any>("set_special_k_verified");
const listInstalledGames = callable<[], any>("list_installed_games");
const findGameExecutablePath = callable<[string], any>("find_game_executable_path");
const forceSpecialKSetup = callable<[string, string, string], any>("force_special_k_setup");
const resetPluginCaches = callable<[], any>("reset_plugin_caches");
const getPluginProcessHealth = callable<[], any>("get_plugin_process_health");
const fixPluginProcesses = callable<[], any>("fix_plugin_processes");
const executeSetupFlow = callable<[string, string, string, boolean?], any>("execute_setup_flow");
const verifyHdrInstallation = callable<[string, string], any>("verify_hdr_installation");
const getGameHdrStatus = callable<[string, string], any>("get_game_hdr_status");

async function getSteamLaunchOptions(appid: string): Promise<string> {
  const apps = (SteamClient.Apps as any);
  if (typeof apps.GetAppLaunchOptions !== "function") {
    return "";
  }
  const value = await apps.GetAppLaunchOptions(parseInt(appid));
  return typeof value === "string" ? value : value?.strLaunchOptions || value?.launchOptions || "";
}

function stripHdrLaunchTokens(options: string): string {
  return (options || "")
    .replace(/\bPROTON_ENABLE_HDR=\S+\s*/g, "")
    .replace(/\bDXVK_HDR=\S+\s*/g, "")
    .replace(/\bENABLE_HDR_WSI=\S+\s*/g, "")
    .replace(/\bENABLE_GAMESCOPE_WSI=\S+\s*/g, "")
    .replace(/\bWINEDLLOVERRIDES="[^"]*(?:d3dcompiler_47|dxgi|d3d11|d3d12|d3d9|d3d8|ddraw|dinput8|opengl32)[^"]*"\s*/g, "")
    .replace(/\bWINEDLLOVERRIDES=[^\s]*?(?:d3dcompiler_47|dxgi|d3d11|d3d12|d3d9|d3d8|ddraw|dinput8|opengl32)[^\s]*\s*/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function mergeHdrLaunchOptions(existing: string, hdrOptions: string): string {
  const cleanExisting = stripHdrLaunchTokens(existing);
  const hdrPrefix = (hdrOptions || "").replace(/\s*%command%\s*/g, " ").replace(/\s+/g, " ").trim();
  if (!hdrPrefix) {
    return cleanExisting;
  }
  if (!cleanExisting) {
    return `${hdrPrefix} %command%`.trim();
  }
  if (cleanExisting.includes("%command%")) {
    return cleanExisting.replace("%command%", `${hdrPrefix} %command%`).replace(/\s+/g, " ").trim();
  }
  return `${hdrPrefix} ${cleanExisting}`.replace(/\s+/g, " ").trim();
}

async function setMergedHdrLaunchOptions(appid: string, hdrOptions: string) {
  const apps = (SteamClient.Apps as any);
  if (typeof apps.SetAppLaunchOptions !== "function") {
    throw new Error("Steam launch option API is unavailable.");
  }
  const existing = await getSteamLaunchOptions(appid);
  await apps.SetAppLaunchOptions(parseInt(appid), mergeHdrLaunchOptions(existing, hdrOptions));
}

async function removeHdrLaunchOptions(appid: string) {
  const apps = (SteamClient.Apps as any);
  if (typeof apps.SetAppLaunchOptions !== "function") {
    return;
  }
  const existing = await getSteamLaunchOptions(appid);
  await apps.SetAppLaunchOptions(parseInt(appid), stripHdrLaunchTokens(existing));
}

interface Recommendation {
  method: string;
  score: number;
  reason: string;
  confidence: string;
  blocked?: string[];
  notes?: string[];
  requires_verification?: boolean;
}

interface GameContext {
  appid: string;
  title: string;
  graphics_api: string;
  architecture?: string;
  injection_dll?: string;
  engine?: string;
  anti_cheat: string[];
  is_multiplayer: boolean;
  native_hdr: string;
  special_k_wiki: boolean;
  special_k_notes?: string[];
  special_k_delay_seconds?: string;
}

const HdrManagementSection = () => {
  const [selectedGame, setSelectedGame] = useState<any>(null);
  const [games, setGames] = useState<any[]>([]);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [context, setContext] = useState<GameContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [logContent, setLogContent] = useState<string>("");
  const [showLog, setShowLog] = useState(false);

  const [showSkEditor, setShowSkEditor] = useState(false);
  const [exePath, setExePath] = useState("");
  const [processHealth, setProcessHealth] = useState<any>(null);
  const [hdrStatus, setHdrStatus] = useState<any>(null);

  useEffect(() => {
    // Intentionally no Steam focus tracking:
    // HDR setup requires the game to be closed (launch options / injections),
    // and SteamClient event availability varies across Decky/Steam builds.
  }, [games]);

  useEffect(() => {
    const fetchGames = async () => {
      const response = await listInstalledGames();
      if (response.status === "success") {
        const sortedGames = response.games.sort((a: any, b: any) => a.name.localeCompare(b.name));
        setGames(sortedGames);
      }
    };
    fetchGames();
  }, []);

  useEffect(() => {
    if (selectedGame) {
      setRecommendation(null);
      setContext(null);
      setExePath("");
      setHdrStatus(null);
      setLogContent("");
      setShowLog(false);
      refreshState();
    } else {
      setRecommendation(null);
      setContext(null);
      setExePath("");
      setHdrStatus(null);
    }
  }, [selectedGame]);

  const refreshState = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      const detection = await findGameExecutablePath(selectedGame.appid);
      let resolvedExePath = "";
      if (detection.status === "success") {
        resolvedExePath = detection.steam_logs_result?.executable_path || detection.enhanced_detection_result?.executable_path || "";
        setExePath(resolvedExePath);
      }

      const recResponse = await getHdrRecommendation(selectedGame.appid, selectedGame.name, resolvedExePath);
      if (recResponse.status === "success") {
        setRecommendation(recResponse.recommendations[0]);
        setContext(recResponse.context);
      } else {
        setRecommendation({
          method: "sdr",
          score: 0,
          reason: recResponse.message || "Could not build HDR recommendation.",
          confidence: "high",
        });
        toaster.toast({ title: "HDR recommendation failed", body: recResponse.message || "See logs for details." });
      }
      const status = await getGameHdrStatus(selectedGame.appid, resolvedExePath);
      setHdrStatus(status);
      const health = await getPluginProcessHealth();
      setProcessHealth(health);
    } catch (e) {
      console.error(e);
      setRecommendation({
        method: "sdr",
        score: 0,
        reason: String(e),
        confidence: "high",
      });
      setHdrStatus({ status: "error", installed: false, message: String(e) });
      toaster.toast({ title: "HDR recommendation failed", body: String(e) });
    } finally {
      setLoading(false);
    }
  };

  const handleSetup = async () => {
    if (!selectedGame) return;
    if (hdrStatus?.status === "success" && hdrStatus.installed) {
      await handleUninstall();
      return;
    }
    setLoading(true);
    try {
      const response = await executeSetupFlow(selectedGame.appid, selectedGame.name, exePath);
      if (response.status === "success" && response.launch_options) {
        await setMergedHdrLaunchOptions(selectedGame.appid, response.launch_options);
      }
      toaster.toast({ title: "HDR Setup", body: response.message });
      await refreshState();
    } catch (e) {
      toaster.toast({ title: "HDR Setup Failed", body: String(e), duration: 7000 });
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateSkValue = async (section: string, key: string, value: string) => {
    const response = await updateSkConfigValue(selectedGame.appid, exePath, section, key, value);
    toaster.toast({ title: "Special K Config", body: response.message });
  };

  const getConfidenceColor = (confidence: string) => {
    switch (confidence.toLowerCase()) {
      case "high": return "#2ecc71";
      case "medium": return "#f1c40f";
      case "low": return "#e67e22";
      default: return "#3498db";
    }
  };

  const handleVerify = async () => {
    if (!selectedGame || !exePath) return;
    setLoading(true);
    try {
      const response = await verifyHdrInstallation(selectedGame.appid, exePath);
      toaster.toast({ 
        title: "Verification", 
        body: response.message,
        duration: 5000 
      });
      await refreshState();
    } finally {
      setLoading(false);
    }
  };

  const handleTryNext = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      await runSurgicalUninstall(selectedGame.appid, exePath);
      await removeHdrLaunchOptions(selectedGame.appid);
      const response = await executeSetupFlow(selectedGame.appid, selectedGame.name, exePath, true);
      if (response.status === "success" && response.launch_options) {
        await setMergedHdrLaunchOptions(selectedGame.appid, response.launch_options);
      }
      toaster.toast({ title: "HDR Setup", body: response.message });
      await refreshState();
    } catch (e) {
      toaster.toast({ title: "Try Next Failed", body: String(e), duration: 7000 });
    } finally {
      setLoading(false);
    }
  };

  const handleUninstall = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      const response = await runSurgicalUninstall(selectedGame.appid, exePath);
      await removeHdrLaunchOptions(selectedGame.appid);
      toaster.toast({ title: "HDR Removal", body: response.message });
      await refreshState();
    } catch (e) {
      toaster.toast({ title: "HDR Removal Failed", body: String(e), duration: 7000 });
    } finally {
      setLoading(false);
    }
  };

  const handleSpecialKOverride = async (verified: boolean) => {
    if (!selectedGame) return;
    setLoading(true);
    const response = await setSpecialKVerified(selectedGame.appid, verified);
    toaster.toast({ title: "Special K", body: response.message });
    await refreshState();
    setLoading(false);
  };

  const handleForceSpecialK = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      const response = await forceSpecialKSetup(selectedGame.appid, selectedGame.name, exePath);
      if (response.status === "success" && response.launch_options) {
        await setMergedHdrLaunchOptions(selectedGame.appid, response.launch_options);
      }
      toaster.toast({ title: "Special K Override", body: response.message });
      await refreshState();
    } catch (e) {
      toaster.toast({ title: "Special K Override Failed", body: String(e), duration: 7000 });
    } finally {
      setLoading(false);
    }
  };

  const viewLog = async () => {
    if (!selectedGame) return;
    const response = await getPerGameLog(selectedGame.appid);
    if (response.status === "success") {
      setLogContent(response.log);
      setShowLog(true);
    } else {
      toaster.toast({ title: "Log Unavailable", body: response.message || "No log found." });
    }
  };

  const handleResetCaches = async () => {
    setLoading(true);
    const response = await resetPluginCaches();
    toaster.toast({ title: "Caches Reset", body: response.message });
    if (selectedGame) {
      await refreshState();
    }
    setLoading(false);
  };

  const handleFixProcesses = async () => {
    const response = await fixPluginProcesses();
    toaster.toast({ title: "Plugin Process Fix", body: response.message });
    setProcessHealth(await getPluginProcessHealth());
  };

  const methodLabel = recommendation?.method === "renodx_disabled" ? "RENODX DISABLED" : recommendation?.method.toUpperCase();
  const hdrInstalled = hdrStatus?.status === "success" && hdrStatus.installed;
  const setupDisabled = !hdrInstalled && (!!context?.anti_cheat.length || recommendation?.method === "renodx_disabled" || recommendation?.score === 0);

  return (
    <PanelSection title="Per-Game HDR Management">
      <PanelSectionRow>
        <DropdownItem
          rgOptions={games.map(g => ({ data: g, label: g.name }))}
          selectedOption={selectedGame}
          onChange={(opt) => setSelectedGame(opt.data)}
          strDefaultLabel="Select Game..."
        />
      </PanelSectionRow>

      {selectedGame && (
        <>
          {loading ? (
            <PanelSectionRow><div>Processing...</div></PanelSectionRow>
          ) : (
            <>
              {recommendation && (
                <PanelSectionRow>
                  <div style={{
                    padding: "12px",
                    borderRadius: "4px",
                    backgroundColor: "rgba(255, 255, 255, 0.05)",
                    borderLeft: `4px solid ${getConfidenceColor(recommendation.confidence)}`,
                    width: "100%",
                    boxSizing: "border-box",
                    overflowWrap: "anywhere"
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", minWidth: 0 }}>
                      <div style={{ fontWeight: "bold", color: getConfidenceColor(recommendation.confidence), minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                        {methodLabel}
                      </div>
                      <div style={{ fontSize: "0.8em", opacity: 0.5, flexShrink: 0 }}>Score: {recommendation.score}</div>
                    </div>
                    {context && (
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 8px", fontSize: "0.78em", opacity: 0.7, marginTop: "6px" }}>
                        <div>API: {context.graphics_api || "unknown"}</div>
                        <div>Hook: {context.injection_dll || "auto"}</div>
                        <div>Engine: {context.engine || "unknown"}</div>
                        <div>Arch: {context.architecture || "unknown"}</div>
                      </div>
                    )}
                    <div style={{ fontSize: "0.9em", marginTop: "4px", color: "#eee" }}>
                      {recommendation.reason}
                    </div>
                    {recommendation.notes?.map((note: string, i: number) => (
                      <div key={i} style={{ fontSize: "0.8em", opacity: 0.6, marginTop: "2px", fontStyle: "italic" }}>
                        - {note}
                      </div>
                    ))}
                  </div>
                </PanelSectionRow>
              )}

              {context?.anti_cheat.length ? (
                <PanelSectionRow>
                  <div style={{
                    padding: "10px",
                    backgroundColor: "rgba(231, 76, 60, 0.2)",
                    borderRadius: "4px",
                    border: "1px solid #e74c3c",
                    fontSize: "0.9em",
                    color: "#ff6b6b",
                    overflowWrap: "anywhere"
                  }}>
                    Warning: Anti-cheat: {context.anti_cheat.join(", ")}. Injection tools are blocked for your safety.
                  </div>
                </PanelSectionRow>
              ) : null}

              {context?.special_k_notes?.length ? (
                <PanelSectionRow>
                  <div style={{ padding: "10px", border: "1px solid rgba(255,255,255,0.18)", borderRadius: "4px", fontSize: "0.82em", lineHeight: 1.25, overflowWrap: "anywhere" }}>
                    <div style={{ fontWeight: 700, marginBottom: "4px" }}>Special K notes from PCGamingWiki</div>
                    {context.special_k_delay_seconds && context.special_k_delay_seconds !== "0" && (
                      <div>Injection delay: {context.special_k_delay_seconds}s will be preconfigured.</div>
                    )}
                    {context.special_k_notes.slice(0, 3).map((note, i) => (
                      <div key={i} style={{ opacity: 0.72, marginTop: "3px" }}>{note}</div>
                    ))}
                  </div>
                </PanelSectionRow>
              ) : null}

              {processHealth?.duplicate ? (
                <PanelSectionRow>
                  <div style={{ padding: "10px", borderRadius: "4px", border: "1px solid #e67e22", background: "rgba(230,126,34,0.16)", fontSize: "0.86em", overflowWrap: "anywhere" }}>
                    <div style={{ marginBottom: "6px" }}>Multiple Decky RenoDX backend processes are running. This can cause stale UI state or stuck installs.</div>
                    <ButtonItem layout="below" onClick={handleFixProcesses}>
                      Fix Duplicate Plugin Processes
                    </ButtonItem>
                  </div>
                </PanelSectionRow>
              ) : null}

              {hdrStatus?.status === "success" && (
                <PanelSectionRow>
                  <div style={{
                    padding: "10px",
                    borderRadius: "4px",
                    border: `1px solid ${hdrInstalled ? "#4CAF50" : "rgba(255,255,255,0.16)"}`,
                    background: hdrInstalled ? "rgba(76,175,80,0.12)" : "rgba(255,255,255,0.04)",
                    fontSize: "0.84em",
                    lineHeight: 1.25,
                    overflowWrap: "anywhere"
                  }}>
                    <div style={{ fontWeight: 700 }}>{hdrInstalled ? "HDR Installed" : "HDR Not Installed"}</div>
                    <div>{hdrStatus.message}</div>
                    {hdrStatus.method && <div style={{ opacity: 0.7 }}>Method: {hdrStatus.method}</div>}
                  </div>
                </PanelSectionRow>
              )}

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleSetup} disabled={setupDisabled}>
                  {hdrInstalled
                    ? "Remove HDR"
                    : recommendation?.method === "renodx_disabled"
                    ? "RenoDX Temporarily Disabled"
                    : recommendation?.score === 0
                      ? "No safe HDR method"
                      : `Install Recommended (${methodLabel})`}
                </ButtonItem>
              </PanelSectionRow>

              {context && !context.anti_cheat.length && recommendation?.method !== "special_k" && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => handleSpecialKOverride(true)}>
                    Mark Special K Verified
                  </ButtonItem>
                </PanelSectionRow>
              )}

              {context && !context.anti_cheat.length && ["renodx_disabled", "renodx", "luma", "native_hdr"].includes(recommendation?.method || "") && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={handleForceSpecialK}>
                    Use Special K Instead
                  </ButtonItem>
                  <div style={{ fontSize: "0.78em", opacity: 0.62, padding: "4px 8px 0", lineHeight: 1.25, overflowWrap: "anywhere" }}>
                    Installs Special K even though a higher-priority HDR method was recommended. HDR still needs in-game verification.
                  </div>
                </PanelSectionRow>
              )}

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleVerify}>
                  Check Installed HDR
                </ButtonItem>
                <div style={{ fontSize: "0.78em", opacity: 0.62, padding: "4px 8px 0", lineHeight: 1.25, overflowWrap: "anywhere" }}>
                  Verifies the current install from its manifest and game files. For Special K, this does not prove HDR is working until the in-game HDR menu/setup is available.
                </div>
              </PanelSectionRow>

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleTryNext} disabled={!!context?.anti_cheat.length}>
                  Remove and Try Next Method
                </ButtonItem>
                <div style={{ fontSize: "0.78em", opacity: 0.62, padding: "4px 8px 0", lineHeight: 1.25, overflowWrap: "anywhere" }}>
                  Removes the current HDR method, skips this recommendation, then attempts the next fallback in the priority list.
                </div>
              </PanelSectionRow>

              <PanelSectionRow>
                <div style={{ color: "#e74c3c" }}>
                  <ButtonItem layout="below" onClick={handleUninstall}>
                    Remove HDR
                  </ButtonItem>
                </div>
              </PanelSectionRow>

              {recommendation?.method === "special_k" && (
                <>
                  <PanelSectionRow>
                    <ButtonItem layout="below" onClick={() => setShowSkEditor(true)}>
                      Special K Settings
                    </ButtonItem>
                  </PanelSectionRow>
                  <PanelSectionRow>
                    <ButtonItem layout="below" onClick={() => handleSpecialKOverride(false)}>
                      Clear Special K Verification
                    </ButtonItem>
                  </PanelSectionRow>
                </>
              )}

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={viewLog}>
                  View Logs
                </ButtonItem>
              </PanelSectionRow>

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleResetCaches}>
                  Reset Detection Caches
                </ButtonItem>
                <div style={{ fontSize: "0.78em", opacity: 0.62, padding: "4px 8px 0", lineHeight: 1.25, overflowWrap: "anywhere" }}>
                  Clears cached game paths, API detection, RenoDX support data, and update status, then re-runs detection for the selected game.
                </div>
              </PanelSectionRow>
            </>
          )}
        </>
      )}

      {showLog && (
        <ModalRoot closeModal={() => setShowLog(false)}>
          <div style={{ position: "fixed", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.42)", zIndex: 9999 }}>
            <div style={{ width: "min(760px, 86vw)", maxHeight: "72vh", backgroundColor: "#1e1e1e", color: "#d4d4d4", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.14)", boxShadow: "0 18px 60px rgba(0,0,0,0.45)", overflow: "hidden" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid #333", fontWeight: 700 }}>
                <span>HDR Plugin Log: {selectedGame?.name}</span>
                <button style={{ width: "32px", height: "32px", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.18)", background: "rgba(255,255,255,0.08)", color: "white", fontSize: "18px" }} onClick={() => setShowLog(false)}>x</button>
              </div>
              <pre style={{ margin: 0, padding: "12px", maxHeight: "calc(72vh - 54px)", overflowY: "auto", whiteSpace: "pre-wrap", fontSize: "12px", fontFamily: "monospace" }}>{logContent}</pre>
            </div>
          </div>
        </ModalRoot>
      )}

      {showSkEditor && (
        <ModalRoot closeModal={() => setShowSkEditor(false)}>
          <div style={{ padding: "16px" }}>
            <div style={{ marginBottom: "16px", fontSize: "1.2em", fontWeight: "bold" }}>Special K Configuration</div>
            
            <PanelSection title="Display & UI">
              <PanelSectionRow>
                <Field label="UI Scale (Deck Screen)">
                  <DropdownItem
                    rgOptions={[
                      { data: "0.5", label: "Small (0.5)" },
                      { data: "0.8", label: "Default (0.8)" },
                      { data: "1.0", label: "Large (1.0)" }
                    ]}
                    selectedOption={{ data: "0.8", label: "Default (0.8)" }}
                    onChange={(opt) => handleUpdateSkValue("Display.Output", "UIScale", opt.data)}
                  />
                </Field>
              </PanelSectionRow>
            </PanelSection>

            <PanelSection title="Injection">
              <PanelSectionRow>
                <Field label="Injection Delay (Seconds)">
                  <DropdownItem
                    rgOptions={[
                      { data: "0", label: "None" },
                      { data: "5", label: "5s" },
                      { data: "10", label: "10s" },
                      { data: "15", label: "15s" }
                    ]}
                    selectedOption={{ data: context?.special_k_delay_seconds || "0", label: context?.special_k_delay_seconds && context.special_k_delay_seconds !== "0" ? `${context.special_k_delay_seconds}s` : "None" }}
                    onChange={(opt) => handleUpdateSkValue("SpecialK.System", "InjectionDelay", opt.data)}
                  />
                </Field>
              </PanelSectionRow>
            </PanelSection>
          </div>
        </ModalRoot>
      )}
    </PanelSection>
  );
};

export default HdrManagementSection;

