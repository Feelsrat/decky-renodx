import { useEffect, useState } from "react";
import {
  ButtonItem,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { IoMdColorPalette } from "react-icons/io";
import HdrManagementSection from "./HdrManagementSection";

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

type UpdateStatus = {
  ok: boolean;
  current?: string;
  latest?: string;
  elevated?: boolean;
  hasUpdate?: boolean;
  canInstall?: boolean;
  releaseUrl?: string;
  installedVersion?: string;
  requiresRestart?: boolean;
  restarted?: boolean;
  message: string;
};

const runInstallReShade = callable<[boolean, string, boolean, string[]], InstallResult>("run_install_reshade");
const runUninstallReShade = callable<[], InstallResult>("run_uninstall_reshade");
const checkReShadePath = callable<[], PathCheckResponse>("check_reshade_path");
const detectSteamDeckModel = callable<[], DeckModelResponse>("detect_steam_deck_model");
const logError = callable<[string], void>("log_error");
const saveAutoHdrPreference = callable<[boolean], InstallResult>("save_autohdr_preference");
const loadAutoHdrPreference = callable<[], any>("load_autohdr_preference");
const loadInstalledConfiguration = callable<[], any>("load_installed_configuration");
const getUpdateStatus = callable<[], UpdateStatus>("get_update_status");
const checkUpdate = callable<[force?: boolean], UpdateStatus>("check_update");
const installUpdate = callable<[], UpdateStatus>("install_update");

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error(message)), timeoutMs);
    promise
      .then(resolve)
      .catch(reject)
      .finally(() => window.clearTimeout(timeout));
  });
}

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
        await loadAutoHdrPreference();
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
      true !== installedConfig.with_addon ||
      selectedVersion.value !== installedConfig.version ||
      true !== installedConfig.with_autohdr
    );
  }, [installedConfig, pathExists, selectedVersion]);

  const installHdrRuntime = async () => {
    try {
      setInstalling(true);
      await saveAutoHdrPreference(true);
      const result = await withTimeout(
        runInstallReShade(true, selectedVersion.value, true, ["autohdr"]),
        390000,
        "HDR component install did not return after 6.5 minutes. Check the plugin log; a download or extraction may still be stuck."
      );
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

  const installButtonText = installing
    ? "Installing..."
    : `Install ${selectedVersion.label} with HDR only`;

  return (
    <PanelSection title="HDR Runtime Setup">
      {pathExists !== null && (
        <PanelSectionRow>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <div style={{ fontSize: "1.1em", fontWeight: "bold", color: pathExists ? "#2ecc71" : "#e74c3c" }}>
              {pathExists ? "Runtime Installed" : "Runtime Not Installed"}
            </div>
            {currentVersionInfo && (
              <div style={{ fontSize: "0.8em", opacity: 0.6 }}>
                v{currentVersionInfo.version} {currentVersionInfo.addon ? "(Addon Support)" : ""}
              </div>
            )}
          </div>
        </PanelSectionRow>
      )}

      {!modelLoading && deckModel?.status === "success" && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.9em", color: deckModel.is_oled ? "#2ecc71" : "#f39c12" }}>
            {deckModel.model === "Not Steam Deck" ? "⚠️ Non-Steam Deck device" : `Steam Deck ${deckModel.model} detected`}
          </div>
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <div style={{ fontSize: "0.9em", opacity: 0.8 }}>
          HDR runtime setup is automatic and isolated. Includes Special K, RenoDX, and HDR shader packs (Lilium, Pumbo).
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
        <Field focusable label="HDR Components" description="RenoDX is first priority; fallback components are selected automatically per game.">
          <div style={{ color: "#2ecc71", fontWeight: 700 }}>Automatic</div>
        </Field>
      </PanelSectionRow>

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

function UpdatesSection() {
  const [status, setStatus] = useState<UpdateStatus>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getUpdateStatus()
      .then(setStatus)
      .catch((error) => toaster.toast({ title: "Update status failed", body: String(error) }));
  }, []);

  const refresh = async () => {
    setBusy(true);
    try {
      const result = await checkUpdate(true);
      setStatus(result);
      toaster.toast({ title: "Update Check", body: result.message });
    } catch (error) {
      toaster.toast({ title: "Update Check Failed", body: String(error) });
    } finally {
      setBusy(false);
    }
  };

  const install = async () => {
    setBusy(true);
    try {
      const result = await withTimeout(
        installUpdate(),
        90000,
        "The updater did not return after 90 seconds. The install may still finish in the background; close and reopen Decky before trying again."
      );
      setStatus(result);
      toaster.toast({ title: result.ok ? "Update Installed" : "Update Failed", body: result.message });
      if (result.ok) {
        window.setTimeout(() => {
          getUpdateStatus().then(setStatus).catch(() => undefined);
        }, 2500);
      }
    } catch (error) {
      toaster.toast({ title: "Update Failed", body: String(error) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelSection title="Updates">
      <PanelSectionRow>
        <Field
          focusable
          label="Installed Version"
          description={status?.requiresRestart ? "Restart pending" : status?.hasUpdate ? "Update available" : "Ready"}
        >
          <div style={{ fontSize: "16px" }}>{status?.current || "Unknown"}</div>
        </Field>
      </PanelSectionRow>

      {status?.latest && (
        <PanelSectionRow>
          <Field focusable label="Latest Release" description={status.releaseUrl || ""}>
            <div style={{ color: status.hasUpdate ? "#2ecc71" : "rgba(255,255,255,0.75)", fontSize: "16px" }}>
              {status.latest}
            </div>
          </Field>
        </PanelSectionRow>
      )}

      {status?.elevated === false && (
        <PanelSectionRow>
          <Field focusable label="Update Permissions" description="Decky root permissions are required for self-update.">
            <div style={{ color: "#ff8a3d", fontWeight: 700 }}>Missing</div>
          </Field>
        </PanelSectionRow>
      )}

      {status?.message && (
        <PanelSectionRow>
          <Field focusable label="Status" description={status.message}>
            <div style={{ color: status.ok ? "#2ecc71" : "#ff8a3d", fontWeight: 700 }}>
              {status.ok ? "OK" : "Issue"}
            </div>
          </Field>
        </PanelSectionRow>
      )}

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={refresh}>
          {busy ? "Checking..." : "Check for Update"}
        </ButtonItem>
      </PanelSectionRow>

      {status?.canInstall && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={install}>
            {busy ? "Installing..." : status.hasUpdate ? "Install Update" : "Reinstall Current Release"}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "Decky RenoDX",
  titleView: <div className={staticClasses.Title}>Decky RenoDX HDR</div>,
  alwaysRender: true,
  content: (
    <>
      <HdrRuntimeSection />
      <HdrManagementSection />
      <UpdatesSection />
    </>
  ),
  icon: <IoMdColorPalette />,
  onDismount() {
    console.log("Plugin unmounted");
  },
}));
