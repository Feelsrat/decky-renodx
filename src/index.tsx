import { useEffect, useState } from "react";
import { ButtonItem, ConfirmModal, Field, PanelSection, PanelSectionRow, ToggleField, showModal, staticClasses } from "@decky/ui";
import { definePlugin, toaster } from "@decky/api";
import { IoMdColorPalette } from "react-icons/io";
import { api, type UpdateStatus } from "./backend";
import HdrPanel from "./components/HdrPanel";
import { BADGES_SETTING, badgesEnabled } from "./components/LibraryBadge";
import { patchLibrary, unpatchLibrary } from "./library";
import { startGridBadges, stopGridBadges } from "./gridBadges";

function PluginSection() {
  const [status, setStatus] = useState<UpdateStatus>();
  const [busy, setBusy] = useState(false);
  const [badges, setBadges] = useState(badgesEnabled());
  const [runtime, setRuntime] = useState<{ installed: boolean; components: Record<string, string | boolean> } | null>(null);

  const refreshRuntime = () =>
    api.runtimeStatus().then((result) => setRuntime(result.status === "success" ? result : null)).catch(() => undefined);

  useEffect(() => {
    api.updateStatus().then(setStatus).catch(() => undefined);
    refreshRuntime();
  }, []);

  const checkOrInstall = async () => {
    setBusy(true);
    try {
      const result = status?.hasUpdate && status.canInstall ? await api.installUpdate() : await api.checkUpdate(true);
      setStatus(result);
      toaster.toast({ title: "Decky RenoDX update", body: result.message, duration: 6000 });
    } catch (error) {
      toaster.toast({ title: "Update failed", body: String(error) });
    } finally {
      setBusy(false);
    }
  };

  const removeRuntime = () =>
    showModal(
      <ConfirmModal
        strTitle="Remove shared downloads?"
        strDescription="Deletes the cached ReShade, Special K and shader downloads. Games that already have HDR installed keep working; downloads come back the next time a game needs them."
        strOKButtonText="Remove"
        onOK={async () => {
          const result = await api.removeRuntime();
          toaster.toast({ title: "Shared downloads", body: result.message });
          refreshRuntime();
        }}
      />,
    );

  const components = runtime?.components || {};
  const runtimeText = runtime?.installed
    ? [components.reshade && `ReShade ${components.reshade}`, components.specialk && "Special K", components.autohdr && "AutoHDR shaders"].filter(Boolean).join(", ")
    : "Nothing downloaded yet";
  const canInstall = Boolean(status?.hasUpdate && status?.canInstall);

  return (
    <PanelSection title="Plugin">
      <PanelSectionRow>
        <Field focusable label="Version" description={status?.requiresRestart ? "Restarting to apply the update…" : status?.hasUpdate ? `Update available: ${status.latest}` : status?.message}>
          <div style={{ fontWeight: 700, color: canInstall ? "#2ecc71" : "inherit" }}>{status?.current || "…"}</div>
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || status?.requiresRestart} onClick={checkOrInstall}>
          {busy ? "Working…" : canInstall ? `Install update ${status?.latest}` : "Check for updates"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Library badges"
          description="Marks games in the library and on their pages: HDR set up (●), a RenoDX mod exists (★), the game has its own HDR (◆), or a generic Unreal/Unity addon may work (◇)."
          checked={badges}
          onChange={(value) => {
            try {
              localStorage.setItem(BADGES_SETTING, value ? "on" : "off");
            } catch {
              // storage unavailable: the setting just won't stick
            }
            setBadges(value);
            if (value) startGridBadges();
            else stopGridBadges();
          }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field focusable label="Shared downloads" description={runtimeText} />
      </PanelSectionRow>
      {runtime?.installed && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={removeRuntime}>
            Remove shared downloads
          </ButtonItem>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => {
  const libraryPatch = patchLibrary();
  if (badgesEnabled()) startGridBadges();
  return {
    name: "Decky RenoDX",
    titleView: <div className={staticClasses.Title}>Decky RenoDX</div>,
    alwaysRender: true,
    content: (
      <>
        <HdrPanel />
        <PluginSection />
      </>
    ),
    icon: <IoMdColorPalette />,
    onDismount() {
      unpatchLibrary(libraryPatch);
      stopGridBadges();
    },
  };
});
