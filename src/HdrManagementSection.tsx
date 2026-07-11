import { useState, useEffect, useRef } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  DropdownItem,
  ModalRoot,
  ToggleField,
  showModal,
} from "@decky/ui";
import { callable, toaster } from "@decky/api";

import { FullscreenLogModal, FullscreenTab } from "./components/FullscreenLogModal";
import { GameSelector } from "./components/GameSelector";
import { GameStatusCard } from "./components/GameStatusCard";
import { stripHdrLaunchTokens, mergeHdrLaunchOptions } from "./utils/hdr_logic";

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

async function getSteamLaunchOptions(appid: string): Promise<string> {
  const apps = (SteamClient.Apps as any);
  if (typeof apps.GetAppLaunchOptions !== "function") {
    return "";
  }
  const value = await apps.GetAppLaunchOptions(parseInt(appid));
  return typeof value === "string" ? value : value?.strLaunchOptions || value?.launchOptions || "";
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
  warnings?: string[];
  manual_steps?: string[];
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
  special_k_verified?: boolean;
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

const INSTALLABLE_METHODS = ["recommended", "renodx", "special_k", "special_k_delayed", "reshade", "native_hdr", "sdr"];

const HdrManagementSection = () => {
  const [selectedGame, setSelectedGame] = useState<any>(null);
  const [games, setGames] = useState<any[]>([]);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [context, setContext] = useState<GameContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualRenoDx, setManualRenoDx] = useState<any>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showSkEditor, setShowSkEditor] = useState(false);
  const [skDelay, setSkDelay] = useState("0");
  const [skUiScale, setSkUiScale] = useState("0.8");
  const [exePath, setExePath] = useState("");
  const [processHealth, setProcessHealth] = useState<any>(null);
  const [hdrStatus, setHdrStatus] = useState<any>(null);
  const [selectedMethod, setSelectedMethod] = useState("recommended");
  const refreshToken = useRef(0);

  useEffect(() => {
    const fetchGames = async () => {
      try {
        const response = await listInstalledGames();
        if (response.status === "success") {
          setGames(response.games.sort((a: any, b: any) => a.name.localeCompare(b.name)));
        }
      } catch (e) {
        toaster.toast({ title: "Game list failed", body: String(e) });
      }
    };
    fetchGames();
  }, []);

  useEffect(() => {
    setRecommendation(null);
    setContext(null);
    setExePath("");
    setHdrStatus(null);
    setSelectedMethod("recommended");
    if (selectedGame) {
      refreshState();
    }
  }, [selectedGame]);

  useEffect(() => {
    setSkDelay(context?.special_k_delay_seconds || "0");
  }, [context?.special_k_delay_seconds]);

  const refreshState = async () => {
    if (!selectedGame) return;
    const token = ++refreshToken.current;
    setLoading(true);
    try {
      const detection = await findGameExecutablePath(selectedGame.appid);
      let resolvedExePath = "";
      if (detection.status === "success") {
        resolvedExePath = detection.steam_logs_result?.executable_path || detection.enhanced_detection_result?.executable_path || "";
      }
      if (token !== refreshToken.current) return;
      setExePath(resolvedExePath);

      const recResponse = await getHdrRecommendation(selectedGame.appid, selectedGame.name, resolvedExePath);
      if (token !== refreshToken.current) return;
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
      if (token !== refreshToken.current) return;
      setHdrStatus(status);
      const health = await getPluginProcessHealth();
      if (token !== refreshToken.current) return;
      setProcessHealth(health);
    } catch (e) {
      if (token !== refreshToken.current) return;
      console.error(e);
      setRecommendation({ method: "sdr", score: 0, reason: String(e), confidence: "high" });
      setHdrStatus({ status: "error", installed: false, message: String(e) });
      toaster.toast({ title: "HDR recommendation failed", body: String(e) });
    } finally {
      if (token === refreshToken.current) {
        setLoading(false);
      }
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
    try {
      const response = await updateSkConfigValue(selectedGame.appid, exePath, section, key, value);
      toaster.toast({ title: "Special K Config", body: response.message });
    } catch (e) {
      toaster.toast({ title: "Special K Config Failed", body: String(e) });
    }
  };

  const handleVerify = async () => {
    if (!selectedGame || !exePath) return;
    setLoading(true);
    try {
      const response = await verifyHdrInstallation(selectedGame.appid, exePath);
      toaster.toast({ title: "Verification", body: response.message, duration: 5000 });
      await refreshState();
    } catch (e) {
      toaster.toast({ title: "Verification Failed", body: String(e) });
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

  const handleSpecialKVerifiedToggle = async (verified: boolean) => {
    if (!selectedGame) return;
    setContext((prev) => (prev ? { ...prev, special_k_verified: verified } : prev));
    const response = await setSpecialKVerified(selectedGame.appid, verified);
    toaster.toast({ title: "Special K", body: response.message });
    await refreshState();
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
    try {
      const response = await resetPluginCaches();
      toaster.toast({ title: "Caches Reset", body: response.message });
      if (selectedGame) {
        await refreshState();
      }
    } finally {
      setLoading(false);
    }
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

  let methodLabel = recommendation?.method === "renodx_disabled" ? "RenoDX unavailable" : (recommendation?.method || "").replace(/_/g, " ").toUpperCase();
  if (recommendation?.method === "renodx" && recommendation.renodx_status) {
    const isGeneric = recommendation.renodx_match_type === "generic_engine";
    if (isGeneric) {
      methodLabel = `Experimental RenoDX (${context?.engine || "Unknown Engine"})`;
    } else {
      const statusText = recommendation.renodx_status.replace("_", " ");
      methodLabel = `RenoDX Mod (${statusText})`;
    }
  }

  const hdrInstalled = hdrStatus?.status === "success" && hdrStatus.installed;
  const setupDisabled = !hdrInstalled && (!!context?.anti_cheat.length || recommendation?.method === "renodx_disabled" || recommendation?.score === 0);
  const backendMethodOptions = (context?.method_options || []).filter((item) => INSTALLABLE_METHODS.includes(item.method));
  const methodOptions: MethodOption[] = backendMethodOptions.length ? backendMethodOptions : [
    { method: "recommended", label: "Recommended", available: !setupDisabled, reason: recommendation?.reason || "Use the highest-scored safe method." },
    { method: "special_k", label: "Special K", available: true, reason: "Manual override. Requires in-game verification." },
    { method: "reshade", label: "ReShade AutoHDR", available: true, reason: "Fallback AutoHDR shader path." },
    { method: "sdr", label: "SDR / Remove Injection", available: true, reason: "Remove injected HDR files and launch options." },
  ];
  const selectedMethodOption = methodOptions.find((item) => item.method === selectedMethod) || methodOptions[0];
  const removalMethod = selectedMethod === "sdr" || selectedMethod === "native_hdr";
  const selectedInstallDisabled =
    loading ||
    !selectedMethodOption?.available ||
    (selectedMethod === "recommended" && setupDisabled) ||
    (!!context?.anti_cheat.length && !removalMethod);

  const installLabel = removalMethod
    ? selectedMethodOption.label
    : hdrInstalled
      ? `Replace with ${selectedMethodOption.label}`
      : `Install ${selectedMethodOption.label}`;

  return (
    <PanelSection title="Per-Game HDR">
      <GameSelector games={games} selectedGame={selectedGame} setSelectedGame={setSelectedGame} />

      {selectedGame && (
        <>
          <GameStatusCard
            loading={loading}
            recommendation={recommendation}
            context={context}
            hdrStatus={hdrStatus}
            hdrInstalled={hdrInstalled}
            methodLabel={methodLabel || ""}
          />

          {processHealth?.duplicate ? (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={handleFixProcesses}
                description="Multiple plugin backend processes detected. This can cause stale state or stuck installs."
              >
                Fix Duplicate Plugin Processes
              </ButtonItem>
            </PanelSectionRow>
          ) : null}

          <PanelSectionRow>
            <DropdownItem
              label="HDR Method"
              rgOptions={methodOptions.map((item) => ({
                data: item.method,
                label: item.available ? (item.badge ? `${item.label} · ${item.badge}` : item.label) : `${item.label} (blocked)`,
              }))}
              selectedOption={selectedMethod}
              onChange={(opt) => setSelectedMethod(String(opt.data))}
            />
          </PanelSectionRow>

          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleInstallSelected}
              disabled={selectedInstallDisabled}
              description={selectedMethodOption?.available
                ? selectedMethodOption.reason
                : selectedMethodOption?.reason || "This method is blocked for the selected game."}
            >
              {loading ? "Working…" : installLabel}
            </ButtonItem>
          </PanelSectionRow>

          {hdrInstalled && !removalMethod && (
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={handleUninstall} disabled={loading}>
                Remove HDR
              </ButtonItem>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <ToggleField
              label="Advanced Tools"
              checked={showAdvanced}
              onChange={setShowAdvanced}
            />
          </PanelSectionRow>

          {showAdvanced && (
            <>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={handleVerify}
                  disabled={loading || !exePath}
                  description="Re-checks installed files against the manifest. Special K HDR still needs the in-game menu to confirm."
                >
                  Check Installed HDR
                </ButtonItem>
              </PanelSectionRow>

              <PanelSectionRow>
                <ToggleField
                  label="Special K Verified"
                  description="Mark after confirming HDR works in the Special K in-game menu."
                  checked={!!context?.special_k_verified}
                  disabled={loading || !!context?.anti_cheat.length}
                  onChange={handleSpecialKVerifiedToggle}
                />
              </PanelSectionRow>

              {(recommendation?.method === "special_k" || hdrStatus?.method === "specialk") && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => setShowSkEditor(true)}>
                    Special K Settings
                  </ButtonItem>
                </PanelSectionRow>
              )}

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={viewLog}>
                  View Logs
                </ButtonItem>
              </PanelSectionRow>

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={viewWikiFixes} disabled={loading}>
                  PCGamingWiki Fixes
                </ButtonItem>
              </PanelSectionRow>

              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={handleResetCaches}
                  disabled={loading}
                  description="Clears cached paths, API detection, and RenoDX data, then re-runs detection."
                >
                  Reset Detection Caches
                </ButtonItem>
              </PanelSectionRow>
            </>
          )}
        </>
      )}

      {showSkEditor && (
        <ModalRoot closeModal={() => setShowSkEditor(false)}>
          <div style={{ padding: "8px 4px" }}>
            <div style={{ marginBottom: "12px", fontSize: "1.15em", fontWeight: "bold" }}>Special K Configuration</div>

            <DropdownItem
              label="UI Scale (Deck Screen)"
              rgOptions={[
                { data: "0.5", label: "Small (0.5)" },
                { data: "0.8", label: "Default (0.8)" },
                { data: "1.0", label: "Large (1.0)" },
              ]}
              selectedOption={skUiScale}
              onChange={(opt) => {
                setSkUiScale(String(opt.data));
                handleUpdateSkValue("Display.Output", "UIScale", String(opt.data));
              }}
            />

            <DropdownItem
              label="Injection Delay"
              rgOptions={[
                { data: "0", label: "None" },
                { data: "5", label: "5s" },
                { data: "10", label: "10s" },
                { data: "15", label: "15s" },
              ]}
              selectedOption={skDelay}
              onChange={(opt) => {
                setSkDelay(String(opt.data));
                handleUpdateSkValue("SpecialK.System", "InjectionDelay", String(opt.data));
              }}
            />
          </div>
        </ModalRoot>
      )}

      {manualRenoDx && (
        <ModalRoot closeModal={() => setManualRenoDx(null)}>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", minHeight: "220px" }}>
            <div style={{ fontWeight: 700 }}>Manual RenoDX Download</div>
            <div style={{ fontSize: "0.85em", opacity: 0.82 }}>{manualRenoDx.message}</div>
            <ButtonItem layout="below" onClick={handleOpenManualRenoDx}>
              Open Download Page In Browser
            </ButtonItem>
            <ButtonItem
              layout="below"
              onClick={handleImportManualRenoDx}
              disabled={loading}
              description="After downloading the addon file, press this to detect and import it for the selected game."
            >
              Detect Download And Import
            </ButtonItem>
          </div>
        </ModalRoot>
      )}
    </PanelSection>
  );
};

export default HdrManagementSection;
