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
const runSurgicalUninstall = callable<[string], any>("run_surgical_uninstall");
const getPerGameLog = callable<[string], any>("get_per_game_log");
const updateSkConfigValue = callable<[string, string, string, string, string], any>("update_sk_config_value");
const listInstalledGames = callable<[], any>("list_installed_games");
const findGameExecutablePath = callable<[string], any>("find_game_executable_path");

interface Recommendation {
  method: string;
  score: number;
  reason: string;
  confidence: string;
  blocked?: string[];
  notes?: string[];
}

interface GameContext {
  appid: string;
  title: string;
  graphics_api: string;
  injection_dll?: string;
  engine?: string;
  anti_cheat: string[];
  is_multiplayer: boolean;
  native_hdr: string;
  special_k_wiki: boolean;
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
      setLogContent("");
      setShowLog(false);
      refreshState();
    } else {
      setRecommendation(null);
      setContext(null);
      setExePath("");
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
    } catch (e) {
      console.error(e);
      setRecommendation({
        method: "sdr",
        score: 0,
        reason: String(e),
        confidence: "high",
      });
      toaster.toast({ title: "HDR recommendation failed", body: String(e) });
    } finally {
      setLoading(false);
    }
  };

  const handleSetup = async () => {
    if (!selectedGame) return;
    setLoading(true);
    const response = await callable<[string, string, string], any>("execute_setup_flow")(
      selectedGame.appid, 
      selectedGame.name, 
      exePath
    );
    toaster.toast({ title: "HDR Setup", body: response.message });
    refreshState();
    setLoading(false);
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
    const response = await callable<[string, string], any>("verify_hdr_installation")(
      selectedGame.appid, 
      exePath
    );
    toaster.toast({ 
      title: "Verification", 
      body: response.message,
      duration: 5000 
    });
    refreshState();
    setLoading(false);
  };

  const handleTryNext = async () => {
    if (!selectedGame) return;
    setLoading(true);
    // 1. Remove current
    await runSurgicalUninstall(selectedGame.appid);
    // 2. Mark current as failed (TBD in backend, for now just rerun setup)
    // In a real implementation, we'd pass 'skip_current: true' to the backend
    const response = await callable<[string, string, string, boolean], any>("execute_setup_flow")(
      selectedGame.appid, 
      selectedGame.name, 
      exePath,
      true // skip_current
    );
    toaster.toast({ title: "HDR Setup", body: response.message });
    refreshState();
    setLoading(false);
  };

  const handleUninstall = async () => {
    if (!selectedGame) return;
    const response = await runSurgicalUninstall(selectedGame.appid);
    toaster.toast({ title: "HDR Removal", body: response.message });
    refreshState();
  };

  const viewLog = async () => {
    if (!selectedGame) return;
    const response = await getPerGameLog(selectedGame.appid);
    if (response.status === "success") {
      setLogContent(response.log);
      setShowLog(true);
    }
  };

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
                        {recommendation.method.toUpperCase()}
                      </div>
                      <div style={{ fontSize: "0.8em", opacity: 0.5, flexShrink: 0 }}>Score: {recommendation.score}</div>
                    </div>
                    {context && (
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 8px", fontSize: "0.78em", opacity: 0.7, marginTop: "6px" }}>
                        <div>API: {context.graphics_api || "unknown"}</div>
                        <div>Hook: {context.injection_dll || "auto"}</div>
                        <div>Engine: {context.engine || "unknown"}</div>
                        <div>Confidence: {recommendation.confidence}</div>
                      </div>
                    )}
                    <div style={{ fontSize: "0.9em", marginTop: "4px", color: "#eee" }}>
                      {recommendation.reason}
                    </div>
                    {recommendation.notes?.map((note: string, i: number) => (
                      <div key={i} style={{ fontSize: "0.8em", opacity: 0.6, marginTop: "2px", fontStyle: "italic" }}>
                        • {note}
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
                    ⚠️ Anti-cheat: {context.anti_cheat.join(", ")}. 
                    Injection tools are blocked for your safety.
                  </div>
                </PanelSectionRow>
              ) : null}

              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleSetup} disabled={!!context?.anti_cheat.length}>
                  {recommendation?.score === 0 ? "No safe HDR method" : `Install Recommended (${recommendation?.method.toUpperCase()})`}
                </ButtonItem>
              </PanelSectionRow>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", padding: "0 8px" }}>
                <div style={{ minWidth: 0 }}>
                  <ButtonItem layout="below" onClick={handleVerify}>
                    Verify
                  </ButtonItem>
                </div>
                <div style={{ minWidth: 0 }}>
                  <ButtonItem layout="below" onClick={handleTryNext} disabled={!!context?.anti_cheat.length}>
                    Try Next
                  </ButtonItem>
                </div>
              </div>

              <PanelSectionRow>
                <div style={{ color: "#e74c3c" }}>
                  <ButtonItem layout="below" onClick={handleUninstall}>
                    Surgical Removal
                  </ButtonItem>
                </div>
              </PanelSectionRow>

              {recommendation?.method === "special_k" && (
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
            </>
          )}
        </>
      )}

      {showLog && (
        <ModalRoot closeModal={() => setShowLog(false)}>
          <div style={{ padding: "16px", maxHeight: "60vh", overflowY: "auto", fontSize: "12px", fontFamily: "monospace", backgroundColor: "#1e1e1e", color: "#d4d4d4" }}>
            <div style={{ marginBottom: "8px", fontWeight: "bold", borderBottom: "1px solid #333" }}>HDR Plugin Log: {selectedGame?.name}</div>
            <pre style={{ whiteSpace: "pre-wrap" }}>{logContent}</pre>
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
                      { data: "10", label: "10s" }
                    ]}
                    selectedOption={{ data: "0", label: "None" }}
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
