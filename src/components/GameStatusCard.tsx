import { PanelSectionRow } from "@decky/ui";
import type { GameState } from "../backend";

const METHOD_NAMES: Record<string, string> = {
  renodx: "RenoDX",
  special_k: "Special K",
  special_k_delayed: "Special K (delayed)",
  reshade: "ReShade AutoHDR",
  native_hdr: "Native HDR",
  sdr: "SDR",
};

const confidenceColor = (confidence?: string) =>
  ({ high: "#2ecc71", medium: "#f1c40f", low: "#e67e22" } as Record<string, string>)[(confidence || "").toLowerCase()] || "#3498db";

function Notice({ color, title, children }: { color: string; title?: string; children: any }) {
  return (
    <div style={{ marginTop: 8, padding: 8, borderRadius: 4, border: `1px solid ${color}`, background: `${color}22`, fontSize: "0.78em", lineHeight: 1.3 }}>
      {title && <div style={{ fontWeight: 700, marginBottom: 2 }}>{title}</div>}
      {children}
    </div>
  );
}

export function methodName(method?: string) {
  return METHOD_NAMES[method || ""] || method || "";
}

/** One stable card: recommendation, detection, install status and warnings. */
export function GameStatusCard({ state, loading, launchApplied }: { state?: GameState; loading: boolean; launchApplied: boolean | null }) {
  const top = state?.recommendations?.[0];
  const install = state?.install;
  const ctx = state?.context;
  const accent = top ? confidenceColor(top.confidence) : "rgba(255,255,255,0.25)";
  let headline = top ? methodName(top.method) : "";
  if (top?.method === "renodx" && ctx?.renodx_match) {
    headline = top.renodx_match_type === "generic_engine" ? `RenoDX (experimental ${ctx.engine})` : `RenoDX: ${ctx.renodx_match.name}`;
  }

  return (
    <PanelSectionRow>
      <div style={{ padding: "10px 12px", borderRadius: 6, background: "rgba(255,255,255,0.05)", borderLeft: `4px solid ${accent}`, width: "100%", boxSizing: "border-box", overflowWrap: "anywhere", minHeight: 100 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
          <div style={{ fontWeight: 700, color: accent, fontSize: "0.92em", minWidth: 0 }}>
            {loading && !state ? "Analyzing game…" : headline ? `Best: ${headline}` : "No recommendation"}
          </div>
          <div style={{ flexShrink: 0, fontSize: "0.72em", fontWeight: 700, padding: "2px 8px", borderRadius: 10, border: `1px solid ${install?.installed ? "#2ecc7199" : "#ffffff40"}`, color: install?.installed ? "#2ecc71" : "#ffffff99" }}>
            {loading ? "Refreshing…" : install?.installed ? methodName(install.method) || "Installed" : "Not installed"}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 8px", fontSize: "0.76em", opacity: 0.7, marginTop: 6 }}>
          <div>API: {ctx?.api || "…"}</div>
          <div>Hook: {ctx?.hook ? `${ctx.hook}.dll` : "…"}</div>
          <div>Engine: {ctx?.engine || "…"}</div>
          <div>Arch: {ctx?.architecture ? `${ctx.architecture}-bit` : "…"}</div>
        </div>

        <div style={{ fontSize: "0.84em", marginTop: 6, lineHeight: 1.3 }}>{top?.reason || (loading ? "Detecting the executable, graphics API and available mods…" : "")}</div>
        {install?.installed && <div style={{ fontSize: "0.76em", opacity: 0.65, marginTop: 4 }}>{install.message}</div>}

        {install?.installed && install.launch && launchApplied === false && (
          <Notice color="#e67e22" title="Launch options missing">
            HDR files are installed but Steam's launch options don't include them. Use "Apply launch options" below.
          </Notice>
        )}
        {install?.installed && install.files_ok === false && (
          <Notice color="#e67e22" title="Files missing">
            A game update or file verification removed part of the install. Reinstall to repair.
          </Notice>
        )}
        {ctx?.anti_cheat?.length ? (
          <Notice color="#e74c3c">Anti-cheat detected: {ctx.anti_cheat.join(", ")}. Injection is blocked to protect your account.</Notice>
        ) : null}
        {ctx?.linux_build && !state?.exe_path ? (
          <Notice color="#e67e22" title="Native Linux build">
            Force a Proton version in the game's Steam compatibility settings, launch it once, then refresh.
          </Notice>
        ) : null}
        {top?.warnings?.length ? (
          <Notice color="#e67e22" title="Known issues">
            {top.warnings.map((warning, index) => <div key={index}>• {warning}</div>)}
          </Notice>
        ) : null}
        {top?.manual_steps?.length ? (
          <Notice color="#3498db" title="After installing">
            {top.manual_steps.map((step, index) => <div key={index}>{index + 1}. {step}</div>)}
          </Notice>
        ) : null}
        {ctx?.renodx_error ? <div style={{ fontSize: "0.72em", opacity: 0.5, marginTop: 4 }}>{ctx.renodx_error}</div> : null}
        {top?.notes?.slice(0, 2).map((note, index) => (
          <div key={index} style={{ fontSize: "0.72em", opacity: 0.55, marginTop: 3, fontStyle: "italic" }}>{note}</div>
        ))}
      </div>
    </PanelSectionRow>
  );
}
