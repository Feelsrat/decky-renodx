import { useEffect, useRef, useState } from "react";
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
  const [pathExists, setPathExists] = useState<boolean | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<VersionOption>(versionOptions[0]);
  const [currentVersionInfo, setCurrentVersionInfo] = useState<{ version: string; addon: boolean } | null>(null);
  const [deckModel, setDeckModel] = useState<DeckModelResponse | null>(null);
  const [installedConfig, setInstalledConfig] = useState<any>(null);
  const versionInitialized = useRef(false);

  const checkPath = async () => {
    try {
      const result = await checkReShadePath();
      setPathExists(result.exists);
      setCurrentVersionInfo(result.version_info ?? null);
      // Only sync the dropdown to the installed version once; afterwards the
      // user's selection must not be overridden by background refreshes.
      if (!versionInitialized.current && result.version_info?.version) {
        versionInitialized.current = true;
        setSelectedVersion(versionOptions.find((version) => version.value === result.version_info?.version) ?? versionOptions[0]);
      }
    } catch (error) {
      await logError(`checkReShadePath: ${String(error)}`);
    }
  };

  useEffect(() => {
    checkPath();
    const intervalId = setInterval(checkPath, 20000);
    return () => clearInterval(intervalId);
  }, []);

  useEffect(() => {
    const loadModelAndPrefs = async () => {
      try {
        setDeckModel(await detectSteamDeckModel());
        await loadAutoHdrPreference();
      } catch (error) {
        await logError(`runtime init: ${String(error)}`);
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

  const configChanged = Boolean(
    installedConfig && pathExists && (
      true !== installedConfig.with_addon ||
      selectedVersion.value !== installedConfig.version ||
      true !== installedConfig.with_autohdr
    )
  );

  const installHdrRuntime = async () => {
    try {
      setInstalling(true);
      await saveAutoHdrPreference(true);
      const result = await withTimeout(
        runInstallReShade(true, selectedVersion.value, true, ["autohdr"]),
        390000,
        "HDR component install did not return after 6.5 minutes. Check the plugin log; a download or extraction may still be stuck."
      );
      if (result.status === "success") {
        toaster.toast({ title: "HDR Runtime", body: "Runtime installed." });
        const config = await loadInstalledConfiguration();
        setInstalledConfig(config.status === "success" ? config.config : null);
        await checkPath();
      } else {
        toaster.toast({ title: "HDR Runtime Install Failed", body: result.message || "Install failed.", duration: 7000 });
      }
    } catch (error) {
      toaster.toast({ title: "HDR Runtime Install Failed", body: String(error), duration: 7000 });
      await logError(`installHdrRuntime: ${String(error)}`);
    } finally {
      setInstalling(false);
    }
  };

  const uninstallHdrRuntime = async () => {
    try {
      setUninstalling(true);
      const result = await runUninstallReShade();
      if (result.status === "success") {
        setInstalledConfig(null);
        toaster.toast({ title: "HDR Runtime", body: "Runtime uninstalled." });
        await checkPath();
      } else {
        toaster.toast({ title: "HDR Runtime Uninstall Failed", body: result.message || "Uninstall failed.", duration: 7000 });
      }
    } catch (error) {
      toaster.toast({ title: "HDR Runtime Uninstall Failed", body: String(error), duration: 7000 });
      await logError(`uninstallHdrRuntime: ${String(error)}`);
    } finally {
      setUninstalling(false);
    }
  };

  const busy = installing || uninstalling;
  const statusText = pathExists === null ? "Checking…" : pathExists ? "Installed" : "Not installed";
  const statusColor = pathExists === null ? "rgba(255,255,255,0.6)" : pathExists ? "#2ecc71" : "#e74c3c";
  const descriptionParts: string[] = [];
  if (currentVersionInfo) {
    descriptionParts.push(`v${currentVersionInfo.version}${currentVersionInfo.addon ? " (addon support)" : ""}`);
  }
  if (deckModel?.status === "success") {
    descriptionParts.push(deckModel.model === "Not Steam Deck" ? "Non-Steam Deck device" : `Steam Deck ${deckModel.model}`);
  }

  return (
    <PanelSection title="HDR Runtime">
      <PanelSectionRow>
        <Field focusable label="Runtime" description={descriptionParts.join(" • ") || "Shared ReShade runtime with AutoHDR shader packs."}>
          <div style={{ fontWeight: 700, color: statusColor }}>{statusText}</div>
        </Field>
      </PanelSectionRow>

      {(pathExists === false || configChanged) && (
        <PanelSectionRow>
          <DropdownItem
            label="ReShade Version"
            rgOptions={versionOptions.map((version) => ({ data: version.value, label: version.label }))}
            selectedOption={selectedVersion.value}
            onChange={(option) => {
              setSelectedVersion(versionOptions.find((version) => version.value === option.data) ?? versionOptions[0]);
            }}
          />
        </PanelSectionRow>
      )}

      {(pathExists === false || configChanged) && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy}
            onClick={installHdrRuntime}
            description={configChanged ? "Configuration changed — reinstall to apply." : "Downloads ReShade, Special K, and HDR shader packs. Runs once for all games."}
          >
            {installing ? "Installing…" : configChanged ? "Reinstall HDR Runtime" : "Install HDR Runtime"}
          </ButtonItem>
        </PanelSectionRow>
      )}

      {pathExists && !configChanged && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={uninstallHdrRuntime}>
            {uninstalling ? "Uninstalling…" : "Uninstall HDR Runtime"}
          </ButtonItem>
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

  const hasInstallableUpdate = Boolean(status?.hasUpdate && status?.canInstall);
  const versionText = status?.current || "Unknown";
  const description = status?.requiresRestart
    ? "Restart pending"
    : status?.elevated === false
      ? "Root permissions missing — self-update unavailable."
      : status?.hasUpdate
        ? `Update available: ${status?.latest}`
        : status?.message || "Up to date";

  return (
    <PanelSection title="Plugin">
      <PanelSectionRow>
        <Field focusable label="Version" description={description}>
          <div style={{ fontWeight: 700, color: hasInstallableUpdate ? "#2ecc71" : "inherit" }}>{versionText}</div>
        </Field>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={hasInstallableUpdate ? install : refresh}>
          {busy ? "Working…" : hasInstallableUpdate ? `Install Update ${status?.latest}` : "Check for Update"}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "Decky RenoDX",
  titleView: <div className={staticClasses.Title}>Decky RenoDX HDR</div>,
  alwaysRender: true,
  content: (
    <>
      <HdrManagementSection />
      <HdrRuntimeSection />
      <UpdatesSection />
    </>
  ),
  icon: <IoMdColorPalette />,
  onDismount() {
    console.log("Plugin unmounted");
  },
}));
