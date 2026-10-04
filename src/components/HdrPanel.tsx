import { useEffect, useRef, useState } from "react";
import { ButtonItem, ConfirmModal, DialogButton, DropdownItem, Focusable, Navigation, PanelSection, PanelSectionRow, showModal, useQuickAccessVisible } from "@decky/ui";
import { toaster } from "@decky/api";
import { api, type ChangeResult, type GameState, type ManualDownload, type MethodOption, type Recommendation } from "../backend";
import { EMPTY, gameRef, useGames } from "../state";
import { readLaunchOptions, updateLaunchOptions } from "../steam";
import { hasHdr, type LaunchSpec } from "../utils/launchOptions";
import { COLORS, Card, Notice, Small, Spin, Steps } from "./parts";
import { ImportModal, TextModal } from "./Modals";
import { StatusCard, methodName } from "./StatusCard";
import { forgetBadge } from "./LibraryBadge";
import { refreshGridBadges } from "../gridBadges";

type Run = (state: GameState, label: string, action: () => Promise<ChangeResult>, mode?: "apply" | "remove") => Promise<ChangeResult | null>;
type Simple = (label: string, action: () => Promise<{ status: string; message: string }>) => Promise<void>;

const toast = (title: string, body: string, duration = 5000) => toaster.toast({ title, body, duration });
const REPO = "https://github.com/Feelsrat/decky-renodx";

/** What to do in game to see whether HDR is working, per method. */
const CHECK_STEPS: Record<string, string[]> = {
  renodx: [
    "Launch the game; HDR should switch on by itself.",
    "To tweak it, open ReShade with the Home key (bind Home to a back button in Steam Input) and use the RenoDX tab.",
  ],
  reshade: [
    "Launch the game and open ReShade with the Home key (bind it to a back button in Steam Input).",
    "Make sure AutoHDR is enabled in the effect list.",
  ],
  special_k: [
    "Launch the game and open Special K with Ctrl+Shift+Backspace (bind it in Steam Input).",
    "In its HDR section, check that HDR is active and set the peak brightness.",
  ],
  native_hdr: ["Launch the game and turn on HDR in its own display or graphics settings."],
};

/** Opens .github/ISSUE_TEMPLATE/hdr-not-working.yml with its fields filled in (they're matched by id). */
function reportUrl(state: GameState, version: string) {
  const ctx = state.context;
  const params = new URLSearchParams({
    template: "hdr-not-working.yml",
    title: `HDR not working: ${state.title}`,
    game: `${state.title} (${state.kind === "shortcut" ? "non-Steam" : `AppID ${state.appid}`})`,
    method: methodName(state.install.method) || "Not sure",
    detected: `${ctx.api}, ${ctx.architecture}-bit, engine ${ctx.engine}, hook ${ctx.hook || "-"}`,
    version: version || "unknown",
  });
  return `${REPO}/issues/new?${params.toString()}`;
}

function openLink(url: string) {
  try {
    Navigation.NavigateToExternalWeb(url);
  } catch {
    api.openUrl(url).catch(() => undefined);
  }
}

function Buttons({ children }: { children: React.ReactNode }) {
  return (
    <PanelSectionRow>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }}>{children}</Focusable>
    </PanelSectionRow>
  );
}

export default function HdrPanel() {
  const { games, appid, setAppid, entries, patch, refresh, loadGames, display, refreshDisplay, running, opened } = useGames();
  const visible = useQuickAccessVisible();
  const [section, setSection] = useState<"" | "methods" | "advanced">("");
  const [version, setVersion] = useState("");
  const entry = entries[appid] || EMPTY;
  const state = entry.state;
  const busy = Boolean(entry.busy);
  const inFlight = useRef(new Set<string>());

  useEffect(() => {
    if (visible) opened().catch((error) => toast("Couldn't list games", String(error)));
  }, [visible]);

  useEffect(() => {
    api.updateStatus().then((status) => setVersion(status.current || "")).catch(() => undefined);
  }, []);

  useEffect(() => setSection(""), [appid]);

  /** One change per game at a time; then sync Steam's launch options and refresh. */
  const run: Run = async (target, label, action, mode = "apply") => {
    const id = target.appid;
    if (inFlight.current.has(id)) return null;
    inFlight.current.add(id);
    patch(id, { busy: label });
    try {
      const result = await action();
      if (result.status === "manual_required") {
        openImport(target, result as unknown as ManualDownload);
      } else if (result.status === "success") {
        const ref = gameRef(target);
        const launch = mode === "remove"
          ? await updateLaunchOptions(ref, null, [result.launch])
          : await updateLaunchOptions(ref, result.launch ?? null, [result.previous_launch]);
        const keep = launch.keep;
        if (keep && (keep.args.length || keep.dlls.length || keep.env.length)) {
          await api.setLaunchKeep(id, keep).catch(() => undefined);
        }
        let body = result.message || "Done.";
        if (!launch.ok && launch.message) body += ` ${launch.message}`;
        toast(label, body, launch.ok ? 5000 : 12000);
        if (result.failed_attempts?.length && !result.renodx_manual) {
          toast(
            `Installed ${methodName(result.method || "")} instead`,
            `${result.failed_attempts.join(" ")} Check Advanced → View logs, or try again.`,
            12000,
          );
        }
        if (result.renodx_manual) {
          toast("RenoDX", `${result.renodx_manual.mod_name} needs a manual download, so a fallback was installed. Import the mod to switch.`, 9000);
        }
      } else {
        toast(`${label} failed`, result.message || "Unknown error.", 9000);
      }
      return result;
    } catch (error) {
      toast(`${label} failed`, String(error), 9000);
      api.logError(`${label}: ${error}`).catch(() => undefined);
      return null;
    } finally {
      inFlight.current.delete(id);
      patch(id, { busy: undefined });
      refresh(id);
      forgetBadge(id);
      refreshGridBadges(id);
    }
  };

  const openImport = (target: GameState, manual: ManualDownload | null) => {
    showModal(
      <ImportModal
        title={target.title}
        manual={manual}
        onImport={async (file) => (await run(target, "Installing RenoDX", () => api.importRenodx(target.appid, file)))?.status === "success"}
      />,
    );
  };

  const simple: Simple = async (label, action) => {
    try {
      const result = await action();
      toast(label, result.message || (result.status === "success" ? "Done." : "Failed."));
    } catch (error) {
      toast(`${label} failed`, String(error));
    }
    if (appid) refresh(appid);
  };

  const applyLaunch = async (target: GameState, spec: LaunchSpec) => {
    const result = await updateLaunchOptions(gameRef(target), spec, []);
    if (result.keep && (result.keep.args.length || result.keep.dlls.length || result.keep.env.length)) {
      await api.setLaunchKeep(target.appid, result.keep).catch(() => undefined);
    }
    toast("Launch options", result.ok ? "HDR launch options added." : result.message || "Could not update launch options.", result.ok ? 4000 : 12000);
    refresh(target.appid);
  };

  const gameOptions = games.map((game) => ({
    data: game.appid,
    label: `${game.appid === running ? "▶ " : ""}${game.name}${game.kind === "shortcut" && !/non-steam/i.test(game.name) ? " (non-Steam)" : ""}${game.renodx ? "  ★" : ""}`,
  }));

  return (
    <PanelSection title="HDR for">
      <PanelSectionRow>
        <DropdownItem
          rgOptions={gameOptions}
          selectedOption={appid}
          strDefaultLabel={games.length ? "Choose a game…" : "No games found"}
          onChange={(option) => setAppid(String(option.data))}
          onMenuWillOpen={(show) => {
            loadGames().catch(() => undefined);
            show();
          }}
        />
      </PanelSectionRow>

      <DisplayWarning supported={display?.supported ?? null} enabled={display?.enabled ?? null} onRecheck={refreshDisplay} />

      {!appid && games.length > 0 && (
        <Card><Small>Pick a game to see whether HDR can be added and how. The game you're playing is listed first.</Small></Card>
      )}

      {appid && !state && (
        <Card>
          {entry.error
            ? <div style={{ color: COLORS.bad, fontSize: 13 }}>{entry.error}</div>
            : <Small><Spin />Checking the game: executable, graphics API and available mods…</Small>}
        </Card>
      )}

      {state && (
        <>
          <StatusCard state={state} launchApplied={entry.launchApplied} busy={entry.busy && `${entry.busy}…`} />
          {busy && (
            <PanelSectionRow>
              <Small><Spin />Downloads can take a minute the first time. You can close this menu; it keeps going.</Small>
            </PanelSectionRow>
          )}

          <Problems
            state={state}
            launchApplied={entry.launchApplied}
            busy={busy}
            onRepair={() => run(state, "Repairing", () => api.repair(state.appid))}
            onApplyLaunch={(spec) => applyLaunch(state, spec)}
            onChooseExe={() => setSection("advanced")}
          />

          {!busy && <MainAction state={state} run={run} onImport={(manual) => openImport(state, manual)} />}

          {state.install.installed && !state.install.legacy && !busy && (
            <Feedback
              state={state}
              onResult={(result) => simple("Feedback", () => api.setResult(state.appid, result))}
              onReport={() => openLink(reportUrl(state, version))}
              onTryOther={() => setSection("methods")}
            />
          )}

          <SectionToggle open={section === "methods"} disabled={busy} onClick={() => setSection(section === "methods" ? "" : "methods")}>
            {state.install.installed ? "Switch method" : "Other methods"}
          </SectionToggle>
          {section === "methods" && (
            <MethodList
              state={state}
              busy={busy}
              onPick={(option) => run(state, option.method === "sdr" ? "Removing HDR" : `Installing ${option.label}`, () => api.install(state.appid, option.method))}
              onImport={() => openImport(state, manualFor(state))}
            />
          )}

          <SectionToggle open={section === "advanced"} onClick={() => setSection(section === "advanced" ? "" : "advanced")}>
            Advanced
          </SectionToggle>
          {section === "advanced" && <Advanced state={state} busy={busy} simple={simple} run={run} />}
        </>
      )}
    </PanelSection>
  );
}

// ---------------------------------------------------------------- sections

function DisplayWarning({ supported, enabled, onRecheck }: { supported: boolean | null; enabled: boolean | null; onRecheck: () => void }) {
  if (supported === false) {
    return (
      <PanelSectionRow>
        <Notice tone="warn" title="This screen doesn't support HDR">
          Mods still install, but you'll only see HDR on an HDR display (Steam Deck OLED or an HDR TV or monitor).
        </Notice>
      </PanelSectionRow>
    );
  }
  if (enabled === false) {
    return (
      <>
        <PanelSectionRow>
          <Notice tone="warn" title="HDR is turned off in SteamOS">
            Turn it on in Settings → Display → Enable HDR. Until then games stay SDR whatever you install here.
          </Notice>
        </PanelSectionRow>
        <Buttons>
          <DialogButton onClick={onRecheck}>I turned it on, check again</DialogButton>
        </Buttons>
      </>
    );
  }
  return null;
}

function Problems({ state, launchApplied, busy, onRepair, onApplyLaunch, onChooseExe }: {
  state: GameState; launchApplied: boolean | null; busy: boolean;
  onRepair: () => void; onApplyLaunch: (spec: LaunchSpec) => void; onChooseExe: () => void;
}) {
  const install = state.install;
  const ctx = state.context;
  const repairable = Boolean(install.installed && (install.legacy || install.needs_repair || install.game_updated));
  const missingLaunch = Boolean(install.installed && install.launch && launchApplied === false && !repairable);
  const notices: React.ReactNode[] = [];
  if (ctx.anti_cheat.length) {
    notices.push(
      <Notice tone="bad" title={`${ctx.anti_cheat.join(", ")} detected`}>
        Injecting into games with anti-cheat can get your account banned, so it's blocked. If the game has its own HDR, use Native HDR under Other methods.
      </Notice>,
    );
  }
  if (!state.exe_path) {
    notices.push(
      <Notice tone="warn" title={ctx.linux_build ? "This is the Linux version of the game" : "Couldn't find the game's .exe"}>
        {ctx.linux_build
          ? "HDR mods need the Windows version. In the game's Properties → Compatibility, force a Proton version, launch it once, then come back."
          : "Pick the game's executable under Advanced."}
      </Notice>,
    );
  }
  if (install.legacy) {
    notices.push(<Notice tone="warn" title="Installed by an older plugin version">Repair to reinstall it in the new format, which can be removed cleanly.</Notice>);
  } else if (repairable) {
    notices.push(
      <Notice tone="warn" title={install.files_ok === false ? "Some HDR files are missing" : install.launch_outdated ? "Uses old launch options" : "The game was updated"}>
        {install.files_ok === false
          ? "A game update or Steam's file check removed them. Repair puts them back."
          : install.launch_outdated
            ? "This was set up by an older plugin version. Repair brings it up to date."
            : "Updates sometimes undo mods. If HDR stopped working, repair it."}
      </Notice>,
    );
  } else if (missingLaunch) {
    notices.push(<Notice tone="warn" title="Launch options are missing">The HDR files are installed, but this game's Steam launch options don't include the HDR settings.</Notice>);
  }
  if (!notices.length) return null;
  return (
    <>
      {notices.map((notice, index) => <PanelSectionRow key={index}>{notice}</PanelSectionRow>)}
      {(repairable || missingLaunch || (!state.exe_path && !ctx.linux_build)) && (
        <Buttons>
          {repairable && <DialogButton disabled={busy} onClick={onRepair}>Repair</DialogButton>}
          {missingLaunch && <DialogButton disabled={busy} onClick={() => onApplyLaunch(install.launch!)}>Add launch options</DialogButton>}
          {!state.exe_path && !ctx.linux_build && <DialogButton onClick={onChooseExe}>Choose executable</DialogButton>}
        </Buttons>
      )}
    </>
  );
}

function manualFor(state: GameState): ManualDownload | null {
  const match = state.context.renodx_match;
  if (!match?.manual_url) return null;
  return { manual_download: true, url: match.manual_url, mod_name: match.name, message: `The ${match.name} RenoDX mod is hosted on ${new URL(match.manual_url).hostname.replace("www.", "")}.` };
}

function MainAction({ state, run, onImport }: { state: GameState; run: Run; onImport: (manual: ManualDownload | null) => void }) {
  const install = state.install;
  const top = state.recommendations[0];
  if (install.installed) {
    return (
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => run(state, "Removing HDR", () => api.remove(state.appid), "remove")}
          description="Puts the game's files and launch options back the way they were.">
          Remove HDR
        </ButtonItem>
      </PanelSectionRow>
    );
  }
  if (state.context.anti_cheat.length || !state.exe_path) return null;
  if (!top || top.method === "sdr") {
    return <Card><Small>No automatic HDR method fits this game. If you have a RenoDX mod for it, import it under Other methods.</Small></Card>;
  }
  const manual = top.method === "renodx" && top.manual_download;
  const label = top.method === "native_hdr" ? "Use the game's own HDR" : manual ? "Get the RenoDX mod" : "Enable HDR";
  const match = state.context.renodx_match;
  return (
    <>
      <Card accent={COLORS.info}>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: COLORS.dim }}>Recommended</div>
        <div style={{ fontWeight: 700, marginTop: 2 }}>
          {methodName(top.method)}{top.method === "renodx" && match ? `: ${match.name}` : ""}
        </div>
        <Small style={{ marginTop: 2 }}>{manual ? "This mod is only on Nexus/Discord: download it in Desktop Mode, then import it." : top.reason}</Small>
        {top.warnings?.length ? (
          <div style={{ marginTop: 6 }}>
            <Notice tone="warn" title="Known issues">{top.warnings.map((warning, index) => <div key={index}>• {warning}</div>)}</Notice>
          </div>
        ) : null}
        {top.manual_steps?.length ? <><Small style={{ marginTop: 6 }}>After installing:</Small><Steps items={top.manual_steps} /></> : null}
        <WikiNotes rec={top} />
      </Card>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => (manual ? onImport(manualFor(state)) : run(state, top.method === "native_hdr" ? "Setting up native HDR" : `Installing ${methodName(top.method)}`, () => api.install(state.appid, "recommended")))}
        >
          {label}
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
}

/** Per-game notes from the RenoDX wiki: upgrade settings, in-game options, known issues. */
function WikiNotes({ rec }: { rec: Recommendation }) {
  if (!rec.wiki_notes?.length) return null;
  return (
    <>
      <Small style={{ marginTop: 6 }}>From the RenoDX wiki:</Small>
      {rec.wiki_notes.map((note, index) => <Small key={index} style={{ marginTop: 2 }}>• {note}</Small>)}
    </>
  );
}

function Feedback({ state, onResult, onReport, onTryOther }: { state: GameState; onResult: (result: string) => void; onReport: () => void; onTryOther: () => void }) {
  const rec = state.recommendations.find((item) => item.method === state.install.method);
  const steps = CHECK_STEPS[state.install.method || ""] && [...CHECK_STEPS[state.install.method || ""], ...(rec?.manual_steps || [])];
  if (state.user_result === "worked") return null;
  if (state.user_result === "failed") {
    return (
      <>
        <PanelSectionRow>
          <Notice tone="warn" title="Not working?">
            Try another method, or look at Advanced → View logs. Reporting it helps improve the compatibility list for everyone.
          </Notice>
        </PanelSectionRow>
        <Buttons>
          <DialogButton onClick={onTryOther}>Try another method</DialogButton>
          <DialogButton onClick={onReport}>Report on GitHub</DialogButton>
          <DialogButton onClick={() => onResult("")}>Never mind</DialogButton>
        </Buttons>
      </>
    );
  }
  return (
    <>
      {steps && (
        <Card>
          <div style={{ fontWeight: 700, fontSize: 13 }}>Check it in game</div>
          <Steps items={steps} />
          {rec && <WikiNotes rec={rec} />}
        </Card>
      )}
      <PanelSectionRow>
        <div style={{ fontSize: 13, margin: "2px 0 6px" }}>Did HDR work?</div>
        <Focusable style={{ display: "flex", gap: 6 }} flow-children="horizontal">
          <DialogButton style={{ minWidth: 0, flex: 1 }} onClick={() => onResult("worked")}>Yes</DialogButton>
          <DialogButton style={{ minWidth: 0, flex: 1 }} onClick={() => onResult("failed")}>No</DialogButton>
        </Focusable>
      </PanelSectionRow>
    </>
  );
}

function SectionToggle({ open, onClick, disabled, children }: { open: boolean; onClick: () => void; disabled?: boolean; children: string }) {
  return (
    <PanelSectionRow>
      <ButtonItem layout="below" onClick={onClick} disabled={disabled}>
        {open ? "▾ " : "▸ "}{children}
      </ButtonItem>
    </PanelSectionRow>
  );
}

function MethodList({ state, busy, onPick, onImport }: { state: GameState; busy: boolean; onPick: (option: MethodOption) => void; onImport: () => void }) {
  const installed = state.install.installed ? state.install.method : "";
  // "Remove HDR" already covers SDR.
  const options = state.method_options.filter((option) =>
    !["recommended", "sdr", installed].includes(option.method),
  );
  return (
    <>
      {options.map((option) => (
        <PanelSectionRow key={option.method}>
          <ButtonItem layout="below" disabled={busy || !option.available} onClick={() => onPick(option)} description={option.reason}>
            {option.label}{option.badge && option.available ? ` · ${option.badge}` : ""}
          </ButtonItem>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || !state.exe_path} onClick={onImport}
          description="For RenoDX mods from Nexus or Discord: download the .addon64/.addon32 (or a zip) to ~/Downloads first.">
          Import a downloaded RenoDX mod
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
}

function Advanced({ state, busy, simple, run }: { state: GameState; busy: boolean; simple: Simple; run: Run }) {
  const ctx = state.context;
  const short = (path: string) => (path ? path.replace(state.install_path, "…") || "…" : "-");
  return (
    <>
      <Card>
        <Small>
          <div>Executable: {state.exe_path ? short(state.exe_path) : "not found"}</div>
          <div>Graphics API: {ctx.api} ({ctx.api_source || "not detected"})</div>
          <div>HDR files go in: {short(state.target_dir)}</div>
          {ctx.notes.map((note, index) => <div key={index}>{note}</div>)}
        </Small>
      </Card>
      <PanelSectionRow>
        <DropdownItem
          label="Game executable"
          disabled={busy}
          rgOptions={[
            { data: "", label: state.exe_override ? "Automatic" : `Automatic (${state.exe_candidates[0]?.label || "none"})` },
            ...state.exe_candidates.map((item) => ({ data: item.path, label: `${item.label} (${item.arch}-bit)` })),
          ]}
          selectedOption={state.exe_override ? state.exe_path : ""}
          onChange={(option) => simple("Executable", () => api.setExecutable(state.appid, String(option.data)))}
        />
      </PanelSectionRow>
      <Buttons>
        {state.install.installed && <DialogButton disabled={busy} onClick={() => simple("Check files", () => api.verify(state.appid))}>Check installed files</DialogButton>}
        {state.install.installed && !state.install.legacy && <DialogButton disabled={busy} onClick={() => run(state, "Repairing", () => api.repair(state.appid))}>Reinstall (repair)</DialogButton>}
        <DialogButton onClick={() => viewLogs(state)}>View logs</DialogButton>
        {ctx.pcgw_url && <DialogButton onClick={() => openLink(ctx.pcgw_url)}>Open on PCGamingWiki</DialogButton>}
        <DialogButton disabled={busy} onClick={() => simple("Refreshed", () => api.resetCaches())}>Refresh mod list and wiki data</DialogButton>
        <DialogButton
          disabled={busy}
          onClick={() =>
            showModal(
              <ConfirmModal
                strTitle="Reset Proton prefix?"
                strDescription={`This deletes the Proton prefix for ${state.title}. Steam rebuilds it on the next launch, but settings or saves kept only in the prefix (not Steam Cloud) are lost.`}
                strOKButtonText="Delete prefix"
                onOK={() => run(state, "Resetting prefix", () => api.resetPrefix(state.appid), "remove")}
              />,
            )
          }
        >
          Reset Proton prefix (last resort)
        </DialogButton>
      </Buttons>
    </>
  );
}

async function viewLogs(state: GameState) {
  try {
    const [result, launchOptions] = await Promise.all([api.logs(state.appid), readLaunchOptions(gameRef(state)).catch(() => null)]);
    const check = [
      ...(result.checks || []),
      "",
      `Steam launch options: ${launchOptions === null ? "(couldn't read them)" : launchOptions || "(none)"}`,
      state.install.launch && launchOptions !== null && !hasHdr(launchOptions, state.install.launch)
        ? "✗ They don't contain this install's HDR options: use \"Add launch options\" on the status card."
        : "",
    ].filter((line, index, all) => line || all[index - 1]).join("\n");
    showModal(
      <TextModal
        title={`Logs: ${state.title}`}
        tabs={[
          { title: "Check", content: check },
          { title: "ReShade", content: result.reshade_log || (result.reshade_log_path ? `No ReShade.log at ${result.reshade_log_path} yet: ReShade hasn't run in this game.` : "This method doesn't use ReShade.") },
          { title: "Plugin", content: result.plugin_log || "Nothing logged for this game yet." },
          { title: "Proton", content: result.proton_log || `No Proton log at ${result.proton_log_path}. Add PROTON_LOG=1 to the launch options and launch once to create one.` },
        ]}
      />,
    );
  } catch (error) {
    toast("Logs unavailable", String(error));
  }
}
