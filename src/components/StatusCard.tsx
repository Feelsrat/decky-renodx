import type { GameState } from "../backend";
import { COLORS, Card, Pill, Small } from "./parts";

export const METHOD_NAMES: Record<string, string> = {
  renodx: "RenoDX",
  special_k: "Special K",
  special_k_delayed: "Special K (delayed)",
  reshade: "ReShade AutoHDR",
  native_hdr: "Native HDR",
  sdr: "SDR",
  recommended: "Recommended",
};

export const methodName = (method?: string) => METHOD_NAMES[method || ""] || method || "";

const API_NAMES: Record<string, string> = {
  d3d12: "DX12", d3d11: "DX11", d3d10: "DX10", dxgi: "DX10-12", dx11_dx12: "DX11/12",
  d3d9: "DX9", d3d8: "DX8", ddraw: "DirectDraw", opengl: "OpenGL", vulkan: "Vulkan", unknown: "API unknown",
};

function ago(iso?: string) {
  if (!iso) return "";
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000);
  if (Number.isNaN(days)) return "";
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

export type Health = "on" | "attention" | "off" | "blocked";

export function health(state: GameState, launchApplied: boolean | null): Health {
  const install = state.install;
  if (!install.installed) return state.context.anti_cheat.length || !state.exe_path ? "blocked" : "off";
  if (install.legacy || install.needs_repair || install.game_updated || launchApplied === false || state.user_result === "failed") return "attention";
  return "on";
}

/** The headline: is HDR set up for this game, how, and what kind of game it is. */
export function StatusCard({ state, launchApplied, busy }: { state: GameState; launchApplied: boolean | null; busy?: string }) {
  const install = state.install;
  const ctx = state.context;
  const status = health(state, launchApplied);
  const color = { on: COLORS.good, attention: COLORS.warn, off: "rgba(255,255,255,0.35)", blocked: COLORS.bad }[status];
  const headline = busy
    ? busy
    : {
        on: `HDR set up with ${methodName(install.method)}`,
        attention: state.user_result === "failed" ? `${methodName(install.method)}: not working for you` : install.installed ? `${methodName(install.method)} needs attention` : "Needs attention",
        off: "HDR not set up",
        blocked: ctx.anti_cheat.length ? "Blocked: anti-cheat" : "No Windows executable found",
      }[status];
  const facts = [
    API_NAMES[ctx.api] || ctx.api,
    ctx.architecture !== "unknown" && `${ctx.architecture}-bit`,
    ctx.engine !== "unknown" && ctx.engine.charAt(0).toUpperCase() + ctx.engine.slice(1),
    state.kind === "shortcut" && "Non-Steam",
  ].filter(Boolean);

  return (
    <Card accent={busy ? COLORS.info : color}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 10, height: 10, borderRadius: 5, background: busy ? COLORS.info : color, flexShrink: 0 }} />
        <div style={{ fontWeight: 700, fontSize: 15, lineHeight: 1.2 }}>{headline}</div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
        {facts.map((fact) => <Pill key={String(fact)} color="rgba(255,255,255,0.6)">{fact}</Pill>)}
        {state.user_result === "worked" && <Pill color={COLORS.good}>✓ You confirmed it works</Pill>}
      </div>
      {install.installed && !install.legacy && (
        <Small style={{ marginTop: 6 }}>
          Installed {ago(install.installed_at)}{install.dll ? ` as ${install.dll}.dll` : ""}.
        </Small>
      )}
    </Card>
  );
}
