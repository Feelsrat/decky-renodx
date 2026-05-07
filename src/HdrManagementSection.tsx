import { useState, useEffect } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  DropdownItem,
  Field,
  ModalRoot,
  showModal,
} from "@decky/ui";
import { callable, toaster } from "@decky/api";

// New Callables
const getHdrRecommendation = callable<[string, string, string], any>("get_hdr_recommendation");
const runSurgicalUninstall = callable<[string, string], any>("run_surgical_uninstall");
const getPerGameLog = callable<[string], any>("get_per_game_log");
const getPcgwImprovementsIssues = callable<[string], any>("get_pcgw_improvements_issues");
const updateSkConfigValue = callable<[string, string, string, string, string], any>("update_sk_config_value");
const setSpecialKVerified = callable<[string, boolean], any>("set_special_k_verified");
const listInstalledGames = callable<[], any>("list_installed_games");
const findGameExecutablePath = callable<[string], any>("find_game_executable_path");
const resetPluginCaches = callable<[], any>("reset_plugin_caches");
const getPluginProcessHealth = callable<[], any>("get_plugin_process_health");
const fixPluginProcesses = callable<[], any>("fix_plugin_processes");
const installSelectedHdrMethod = callable<[string, string, string, string], any>("install_selected_hdr_method");
const verifyHdrInstallation = callable<[string, string], any>("verify_hdr_installation");
const getGameHdrStatus = callable<[string, string], any>("get_game_hdr_status");
const openRenoDxSearch = callable<[string], any>("open_renodx_search");
const importRenoDxForGame = callable<[string, string, string], any>("import_renodx_for_game");

import { FullscreenLogModal, FullscreenTab } from "./components/FullscreenLogModal";
import { GameSelector } from "./components/GameSelector";
import { RecommendationCard } from "./components/RecommendationCard";
import { HdrStatusBadge } from "./components/HdrStatusBadge";

async function getSteamLaunchOptions(appid: string): Promise<string> {
  const apps = (SteamClient.Apps as any);
  if (typeof apps.GetAppLaunchOptions !== "function") {
    return "";
  }
  const value = await apps.GetAppLaunchOptions(parseInt(appid));
  return typeof value === "string" ? value : value?.strLaunchOptions || value?.launchOptions || "";
}

import { stripHdrLaunchTokens, mergeHdrLaunchOptions } from "./utils/hdr_logic";

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
  renodx_status?: string;
  renodx_match_type?: string;
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
  method_options?: MethodOption[];
}

interface MethodOption {
  method: string;
  label: string;
  available: boolean;
  reason: string;
  badge?: string;
  score?: number;
  confidence?: string;
}

const HdrManagementSection = () => {
  const [selectedGame, setSelectedGame] = useState<any>(null);
  const [games, setGames] = useState<any[]>([]);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [context, setContext] = useState<GameContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualRenoDx, setManualRenoDx] = useState<any>(null);

  const [showSkEditor, setShowSkEditor] = useState(false);
  const [exePath, setExePath] = useState("");
  const [processHealth, setProcessHealth] = useState<any>(null);
  const [hdrStatus, setHdrStatus] = useState<any>(null);
  const [selectedMethod, setSelectedMethod] = useState("recommended");

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
      setSelectedMethod("recommended");
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

  const handleInstallSelected = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      const response = await installSelectedHdrMethod(selectedGame.appid, selectedGame.name, exePath, selectedMethod);
      if (response.status === "success" && response.launch_options) {
        await setMergedHdrLaunchOptions(selectedGame.appid, response.launch_options);
      }
      if (selectedMethod === "sdr" || selectedMethod === "native_hdr") {
        await removeHdrLaunchOptions(selectedGame.appid);
      }
      if (response.manual_download) {
        setManualRenoDx(response);
      }
      toaster.toast({ title: "HDR Setup", body: response.message || response.output || "Selected HDR method applied." });
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

  const handleUninstall = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      setHdrStatus({ status: "success", installed: false, message: "Removing HDR..." });
      const response = await runSurgicalUninstall(selectedGame.appid, exePath);
      await removeHdrLaunchOptions(selectedGame.appid);
      if (response?.status_after) {
        setHdrStatus(response.status_after);
      } else {
        setHdrStatus({ status: "success", installed: false, message: response.message || "HDR removed." });
      }
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

  const viewLog = async () => {
    if (!selectedGame) return;
    const response = await getPerGameLog(selectedGame.appid);
    if (response.status === "success") {
      const tabs = [
        { title: "Plugin", content: response.plugin_log || response.log || "" },
        { title: "Proton", content: response.proton_log || `No Proton log found yet. Launch the game once after setup.\nExpected path: ~/steam-${selectedGame.appid}.log` },
      ];
      showModal(<FullscreenLogModal title={`HDR Logs: ${selectedGame?.name || selectedGame.appid}`} tabs={tabs} />);
    } else {
      toaster.toast({ title: "Log Unavailable", body: response.message || "No log found." });
    }
  };

  const viewWikiFixes = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      const response = await getPcgwImprovementsIssues(selectedGame.appid);
      const format = (items: string[]) => items?.length ? items.map((item) => `- ${item}`).join("\n") : "No entries found.";
      
      const tabs: FullscreenTab[] = [];
      if (response.status === "success") {
        tabs.push({ title: "Essential", content: format(response.essential_improvements) });
        tabs.push({ title: "Issues Fixed", content: format(response.issues_fixed) });
      } else {
        tabs.push({ title: "Error", content: response.message || "Unknown error fetching PCGamingWiki fixes." });
      }

      showModal(<FullscreenLogModal title={`PCGamingWiki: ${response.page_name || selectedGame.name}`} tabs={tabs} />);
    } catch (e) {
      showModal(<FullscreenLogModal title={`PCGamingWiki Error`} tabs={[{ title: "Error", content: String(e) }]} />);
    } finally {
      setLoading(false);
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

  const handleOpenManualRenoDx = async () => {
    if (!manualRenoDx?.url) return;
    const response = await openRenoDxSearch(manualRenoDx.url);
    toaster.toast({ title: "RenoDX Download", body: response.message || "Opened browser." });
  };

  const handleImportManualRenoDx = async () => {
    if (!selectedGame) return;
    setLoading(true);
    try {
      const response = await importRenoDxForGame(selectedGame.appid, "", exePath);
      if (response.status === "success" && response.launch_options) {
        await setMergedHdrLaunchOptions(selectedGame.appid, response.launch_options);
        setManualRenoDx(null);
      }
      toaster.toast({ title: "RenoDX Import", body: response.message || response.output });
      await refreshState();
    } catch (e) {
      toaster.toast({ title: "RenoDX Import Failed", body: String(e), duration: 7000 });
    } finally {
      setLoading(false);
    }
  };

  let methodLabel = recommendation?.method === "renodx_disabled" ? "RENODX DISABLED" : recommendation?.method.toUpperCase();
  if (recommendation?.method === "renodx" && recommendation.renodx_status) {
    const isGeneric = recommendation.renodx_match_type === "generic_engine";
    let icon = "⚠️";
    if (isGeneric) icon = "🧪";
    else if (recommendation.renodx_status === "working") icon = "✅";
    else if (recommendation.renodx_status === "in_progress") icon = "🚧";
    
    if (isGeneric) {
      methodLabel = `${icon} Experimental RenoDX Mod for ${context?.engine || "Unknown Engine"}`;
    } else {
      methodLabel = `${icon} RenoDX Mod (${recommendation.renodx_status.replace("_", " ")})`;
    }
  }

  const hdrInstalled = hdrStatus?.status === "success" && hdrStatus.installed;
  const setupDisabled = !hdrInstalled && (!!context?.anti_cheat.length || recommendation?.method === "renodx_disabled" || recommendation?.score === 0);
  const backendMethodOptions = context?.method_options?.length ? context.method_options : [];
  const installableBackendOptions = backendMethodOptions.filter((item) => ["recommended", "renodx", "special_k", "special_k_delayed", "reshade"].includes(item.method));
  const methodOptions = (installableBackendOptions.length ? installableBackendOptions : [
    { method: "recommended", label: `Recommended${methodLabel ? ` (${methodLabel})` : ""}`, available: !setupDisabled, reason: recommendation?.reason || "Use the highest-scored safe method." },
    { method: "renodx", label: "RenoDX / Luma", available: false, reason: "No RenoDX/Luma status available yet." },
    { method: "special_k", label: "Special K", available: true, reason: "Manual override. Requires in-game verification." },
    { method: "special_k_delayed", label: "Special K Delayed", available: false, reason: "Only available for compatibility entries that require delayed/global injection." },
    { method: "reshade", label: "ReShade AutoHDR", available: true, reason: "Fallback AutoHDR shader path." },
  ]).map((item) => ({
    data: item.method,
    label: item.label,
    method: item.method,
    available: item.available,
    reason: item.reason,
    badge: item.badge,
  }));
  const selectedMethodOption = methodOptions.find((item) => item.data === selectedMethod) || methodOptions[0];
  const selectedInstallDisabled = !selectedMethodOption?.available || (!!context?.anti_cheat.length && !["native_hdr", "sdr"].includes(selectedMethod));

  return (
    <PanelSection title="Per-Game HDR Management">
      <GameSelector games={games} selectedGame={selectedGame} setSelectedGame={setSelectedGame} />

      {selectedGame && (
        <>
          {loading ? (
            <PanelSectionRow><div>Processing...</div></PanelSectionRow>
          ) : (
            <>
              <RecommendationCard 
                recommendation={recommendation}
                context={context}
                methodLabel={methodLabel || ""}
                getConfidenceColor={getConfidenceColor}
              />

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

              {recommendation?.method === "special_k" && context?.special_k_notes?.length ? (
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

              <HdrStatusBadge hdrStatus={hdrStatus} hdrInstalled={hdrInstalled} />

              <PanelSectionRow>
                <Field label="HDR Method">
                  <DropdownItem
                    rgOptions={methodOptions}
                    selectedOption={selectedMethodOption}
                    onChange={(opt) => setSelectedMethod(String(opt.data))}
                  />
                </Field>
              </PanelSectionRow>

              {context?.method_options?.length ? (
                <PanelSectionRow>
                  <div style={{ display: "grid", gap: "6px", width: "100%", maxWidth: "100%", boxSizing: "border-box", paddingRight: "2px" }}>
                    {context.method_options.filter((item) => ["renodx", "special_k", "special_k_delayed", "reshade"].includes(item.method)).map((item) => (
                      <div key={item.method} style={{
                        padding: "8px",
                        borderRadius: "4px",
                        border: `1px solid ${item.available ? "rgba(76,175,80,0.45)" : "rgba(231,76,60,0.45)"}`,
                        background: item.available ? "rgba(76,175,80,0.08)" : "rgba(231,76,60,0.08)",
                        fontSize: "0.8em",
                        lineHeight: 1.22,
                        overflowWrap: "anywhere",
                        boxSizing: "border-box",
                        maxWidth: "100%",
                        minWidth: 0
                      }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", fontWeight: 700, minWidth: 0 }}>
                          <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{item.label}</span>
                          <span style={{ opacity: 0.76, flexShrink: 0 }}>{item.available ? "Available" : "Blocked"}</span>
                        </div>
                        <div style={{ opacity: 0.72, marginTop: "3px" }}>{item.reason}</div>
                      </div>
                    ))}
                  </div>
                </PanelSectionRow>
              ) : null}

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleInstallSelected} disabled={selectedInstallDisabled || (selectedMethod === "recommended" && setupDisabled)}>
                  {hdrInstalled
                      ? `Replace With ${selectedMethodOption.label}`
                      : `Install ${selectedMethodOption.label}`}
                </ButtonItem>
                <div style={{ fontSize: "0.78em", opacity: 0.62, padding: "4px 8px 0", lineHeight: 1.25, overflowWrap: "anywhere" }}>
                  {selectedMethodOption?.available
                    ? "Removes the current HDR files, clears compat data, then installs the method selected above."
                    : selectedMethodOption?.reason || "This method is blocked for the selected game."}
                </div>
              </PanelSectionRow>

              {context && !context.anti_cheat.length && recommendation?.method !== "special_k" && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => handleSpecialKOverride(true)}>
                    Mark Special K Verified
                  </ButtonItem>
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
                <ButtonItem layout="below" onClick={viewWikiFixes}>
                  View PCGamingWiki Fixes
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

      {manualRenoDx && (
        <ModalRoot closeModal={() => setManualRenoDx(null)}>
          <div style={{ position: "fixed", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.42)", zIndex: 9999 }}>
            <div style={{ width: "min(920px, 92vw)", height: "min(680px, 86vh)", backgroundColor: "#1e1e1e", color: "#fff", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.14)", overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid #333", fontWeight: 700 }}>
                <span>Manual RenoDX Download</span>
                <button style={{ width: "32px", height: "32px", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.18)", background: "rgba(255,255,255,0.08)", color: "white", fontSize: "18px" }} onClick={() => setManualRenoDx(null)}>x</button>
              </div>
              <div style={{ padding: "10px 12px", fontSize: "0.82em", opacity: 0.82 }}>{manualRenoDx.message}</div>
              {manualRenoDx.url && (
                <iframe title="RenoDX manual download" src={manualRenoDx.url} style={{ flex: 1, width: "100%", border: 0, background: "#111" }} />
              )}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", padding: "10px 12px", borderTop: "1px solid #333" }}>
                <button onClick={handleOpenManualRenoDx}>Open In Browser</button>
                <button onClick={handleImportManualRenoDx} disabled={loading}>Detect Download And Import</button>
              </div>
            </div>
          </div>
        </ModalRoot>
      )}
    </PanelSection>
  );
};

export default HdrManagementSection;

