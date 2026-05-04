import { useEffect, useState } from "react";
import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  showModal,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { IoMdColorPalette } from "react-icons/io";
import HeroicGamesSection from "./HeroicGamesSection";
import SteamGamesSection from "./SteamGamesSection";

interface InstallResult {
  status: string;
  message?: string;
  output?: string;
}

interface PathCheckResponse {
  exists: boolean;
  is_addon: boolean;
  version_info?: {
    version: string;
    addon: boolean;
  };
}

interface VersionOption {
  label: string;
  value: string;
}

interface DeckModelResponse {
  status: string;
  model: string;
  is_oled: boolean;
  message?: string;
}

const runInstallReShade = callable<[boolean, string, boolean, string[]], InstallResult>("run_install_reshade");
const runUninstallReShade = callable<[], InstallResult>("run_uninstall_reshade");
const checkReShadePath = callable<[], PathCheckResponse>("check_reshade_path");
const detectSteamDeckModel = callable<[], DeckModelResponse>("detect_steam_deck_model");
const logError = callable<[string], void>("log_error");
const saveAutoHdrPreference = callable<[boolean], InstallResult>("save_autohdr_preference");
const loadAutoHdrPreference = callable<[], any>("load_autohdr_preference");
const loadInstalledConfiguration = callable<[], any>("load_installed_configuration");

const versionOptions: VersionOption[] = [
  { label: "ReShade Latest", value: "latest" },
  { label: "ReShade Last Version", value: "last" },
];

function HdrRuntimeSection() {
  const [installing, setInstalling] = useState(false);
  const [uninstalling, setUninstalling] = useState(false);
  const [installResult, setInstallResult] = useState<InstallResult | null>(null);
  const [uninstallResult, setUninstallResult] = useState<InstallResult | null>(null);
  const [pathExists, setPathExists] = useState<boolean | null>(null);
  const [addonEnabled, setAddonEnabled] = useState(true);
  const [autoHdrEnabled, setAutoHdrEnabled] = useState(true);
  const [selectedVersion, setSelectedVersion] = useState<VersionOption>(versionOptions[0]);
  const [currentVersionInfo, setCurrentVersionInfo] = useState<{ version: string; addon: boolean } | null>(null);
  const [deckModel, setDeckModel] = useState<DeckModelResponse | null>(null);
  const [modelLoading, setModelLoading] = useState(true);
  const [installedConfig, setInstalledConfig] = useState<any>(null);
  const [configChanged, setConfigChanged] = useState(false);

  useEffect(() => {
    const checkPath = async () => {
      try {
        const result = await checkReShadePath();
        setPathExists(result.exists);
        setCurrentVersionInfo(result.version_info ?? null);
        setAddonEnabled(result.exists ? result.is_addon : true);
        if (result.version_info?.version) {
          setSelectedVersion(versionOptions.find((version) => version.value === result.version_info?.version) ?? versionOptions[0]);
        }
      } catch (error) {
        await logError(`checkReShadePath: ${String(error)}`);
      }
    };

    checkPath();
    const intervalId = setInterval(checkPath, 3000);
    return () => clearInterval(intervalId);
  }, []);

  useEffect(() => {
    const loadModelAndPrefs = async () => {
      try {
        setModelLoading(true);
        setDeckModel(await detectSteamDeckModel());
        const autoHdrPref = await loadAutoHdrPreference();
        if (autoHdrPref.status === "success") {
          setAutoHdrEnabled(autoHdrPref.autohdr_enabled ?? true);
        }
      } catch (error) {
        await logError(`runtime init: ${String(error)}`);
      } finally {
        setModelLoading(false);
      }
    };

    loadModelAndPrefs();
  }, []);

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const result = await loadInstalledConfiguration();
        setInstalledConfig(result.status === "success" ? result.config : null);
      } catch (error) {
        await logError(`loadInstalledConfiguration: ${String(error)}`);
      }
    };

    loadConfig();
  }, [pathExists]);

  useEffect(() => {
    if (!installedConfig || !pathExists) {
      setConfigChanged(false);
      return;
    }

    setConfigChanged(
      addonEnabled !== installedConfig.with_addon ||
      selectedVersion.value !== installedConfig.version ||
      autoHdrEnabled !== installedConfig.with_autohdr
    );
  }, [addonEnabled, autoHdrEnabled, installedConfig, pathExists, selectedVersion]);

  const installHdrRuntime = async () => {
    try {
      setInstalling(true);
      const result = await runInstallReShade(addonEnabled, selectedVersion.value, autoHdrEnabled, ["autohdr"]);
      setInstallResult(result);
      if (result.status === "success") {
        const config = await loadInstalledConfiguration();
        setInstalledConfig(config.status === "success" ? config.config : null);
        setConfigChanged(false);
      }
    } catch (error) {
      setInstallResult({ status: "error", message: String(error) });
      await logError(`installHdrRuntime: ${String(error)}`);
    } finally {
      setInstalling(false);
    }
  };

  const uninstallHdrRuntime = async () => {
    try {
      setUninstalling(true);
      const result = await runUninstallReShade();
      setUninstallResult(result);
      if (result.status === "success") {
        setInstalledConfig(null);
        setConfigChanged(false);
      }
    } catch (error) {
      setUninstallResult({ status: "error", message: String(error) });
      await logError(`uninstallHdrRuntime: ${String(error)}`);
    } finally {
      setUninstalling(false);
    }
  };

  const toggleAddon = () => {
    if (addonEnabled) {
      setAddonEnabled(false);
      setAutoHdrEnabled(false);
      return;
    }

    showModal(
      <ConfirmModal
        strTitle="Enable Addon Support?"
        strDescription="AutoHDR and RenoDX require ReShade addon support. Avoid using addon injection in online anti-cheat games unless you understand the risk."
        strOKButtonText="Enable"
        strCancelButtonText="Cancel"
        onOK={() => setAddonEnabled(true)}
      />
    );
  };

  const toggleAutoHdr = async () => {
    const next = !autoHdrEnabled;
    setAutoHdrEnabled(next);
    await saveAutoHdrPreference(next);
  };

  const installButtonText = installing
    ? "Installing..."
    : `Install ${selectedVersion.label} with HDR only`;

  return (
    <PanelSection title="HDR Runtime">
      {pathExists !== null && (
        <PanelSectionRow>
          <div style={{ color: pathExists ? "green" : "red" }}>
            {pathExists ? "HDR runtime is installed" : "HDR runtime is not installed"}
            {currentVersionInfo && (
              <div style={{ fontSize: "0.9em", opacity: 0.8, marginTop: "4px" }}>
                Version: {currentVersionInfo.version}
                {currentVersionInfo.addon ? " with addon support" : ""}
              </div>
            )}
          </div>
        </PanelSectionRow>
      )}

      {!modelLoading && deckModel?.status === "success" && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.9em", color: deckModel.is_oled ? "green" : "orange" }}>
            {deckModel.model === "Not Steam Deck" ? "Non Steam Deck device detected" : `Steam Deck ${deckModel.model} detected`}
          </div>
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <div style={{ fontSize: "0.9em", opacity: 0.8 }}>
          This installer intentionally excludes extra visual shader packs. It installs ReShade addon support, AutoHDR files, and the minimal support files needed for HDR/RenoDX.
        </div>
      </PanelSectionRow>

      <PanelSectionRow>
        <DropdownItem
          rgOptions={versionOptions.map((version) => ({ data: version.value, label: version.label }))}
          selectedOption={selectedVersion.value}
          onChange={(option) => {
            setSelectedVersion(versionOptions.find((version) => version.value === option.data) ?? versionOptions[0]);
          }}
          strDefaultLabel="Select ReShade version..."
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField
          label="Addon Support"
          description="Required for AutoHDR and RenoDX."
          checked={addonEnabled}
          onChange={toggleAddon}
        />
      </PanelSectionRow>

      {addonEnabled && (
        <PanelSectionRow>
          <ToggleField
            label="AutoHDR Components"
            description="HDR fallback for DX10/11/12 games on Steam Deck OLED."
            checked={autoHdrEnabled}
            onChange={toggleAutoHdr}
          />
        </PanelSectionRow>
      )}

      {pathExists && configChanged && (
        <PanelSectionRow>
          <div style={{ padding: "12px", backgroundColor: "#ffa726", borderRadius: "4px", color: "white" }}>
            Configuration changed - reinstall to apply.
          </div>
        </PanelSectionRow>
      )}

      {selectedVersion && (!pathExists || configChanged) && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={installing} onClick={installHdrRuntime}>
            {installButtonText}
          </ButtonItem>
        </PanelSectionRow>
      )}

      {pathExists && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={uninstalling} onClick={uninstallHdrRuntime}>
            {uninstalling ? "Uninstalling..." : "Uninstall HDR Runtime"}
          </ButtonItem>
        </PanelSectionRow>
      )}

      {installResult && (
        <PanelSectionRow>
          <div style={{ padding: "12px", backgroundColor: "var(--decky-selected-ui-bg)", borderRadius: "4px", color: installResult.status === "success" ? "green" : "red" }}>
            {installResult.status === "success" ? installResult.output || "Installed." : installResult.message || "Install failed."}
          </div>
        </PanelSectionRow>
      )}

      {uninstallResult && (
        <PanelSectionRow>
          <div style={{ padding: "12px", backgroundColor: "var(--decky-selected-ui-bg)", borderRadius: "4px", color: uninstallResult.status === "success" ? "green" : "red" }}>
            {uninstallResult.status === "success" ? "HDR runtime uninstalled." : uninstallResult.message || "Uninstall failed."}
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "Decky RenoDX",
  titleView: <div>Decky RenoDX HDR</div>,
  alwaysRender: true,
  content: (
    <>
      <HdrRuntimeSection />
      <SteamGamesSection />
      <HeroicGamesSection />
    </>
  ),
  icon: <IoMdColorPalette />,
  onDismount() {
    console.log("Plugin unmounted");
  },
}));
