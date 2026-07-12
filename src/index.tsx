import { useEffect, useState } from "react";
import {
  ButtonItem,
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

const runUninstallReShade = callable<[], InstallResult>("run_uninstall_reshade");
const checkReShadePath = callable<[], PathCheckResponse>("check_reshade_path");
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

function PluginSection() {
  const [status, setStatus] = useState<UpdateStatus>();
  const [busy, setBusy] = useState(false);
  const [runtimeInstalled, setRuntimeInstalled] = useState<boolean | null>(null);
  const [runtimeVersion, setRuntimeVersion] = useState("");
  const [uninstallingRuntime, setUninstallingRuntime] = useState(false);

  const refreshRuntime = async () => {
    try {
      const result = await checkReShadePath();
      setRuntimeInstalled(result.exists);
      setRuntimeVersion(result.version_info?.version || "");
    } catch {
      // Non-fatal; the runtime row just stays in its last known state.
    }
  };

  useEffect(() => {
    getUpdateStatus()
      .then(setStatus)
      .catch((error) => toaster.toast({ title: "Update status failed", body: String(error) }));
    refreshRuntime();
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

  const uninstallRuntime = async () => {
    setUninstallingRuntime(true);
    try {
      const result = await runUninstallReShade();
      toaster.toast({
        title: "HDR Runtime",
        body: result.status === "success" ? "Shared runtime removed. It reinstalls automatically on the next per-game setup." : result.message || "Uninstall failed.",
      });
      await refreshRuntime();
    } catch (error) {
      toaster.toast({ title: "HDR Runtime Uninstall Failed", body: String(error), duration: 7000 });
    } finally {
      setUninstallingRuntime(false);
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

      <PanelSectionRow>
        <Field
          focusable
          label="Shared HDR Runtime"
          description="Downloaded automatically the first time a game needs it."
        >
          <div style={{ fontWeight: 700, color: runtimeInstalled ? "#2ecc71" : "rgba(255,255,255,0.6)" }}>
            {runtimeInstalled === null ? "…" : runtimeInstalled ? `Installed${runtimeVersion ? ` (${runtimeVersion})` : ""}` : "Not installed"}
          </div>
        </Field>
      </PanelSectionRow>

      {runtimeInstalled && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={uninstallingRuntime} onClick={uninstallRuntime}>
            {uninstallingRuntime ? "Removing…" : "Remove Shared HDR Runtime"}
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
      <HdrManagementSection />
      <PluginSection />
    </>
  ),
  icon: <IoMdColorPalette />,
  onDismount() {
    console.log("Plugin unmounted");
  },
}));
