import { PanelSectionRow } from "@decky/ui";

interface GameStatusCardProps {
  loading: boolean;
  recommendation: any;
  context: any;
  hdrStatus: any;
  hdrInstalled: boolean;
  methodLabel: string;
}

const confidenceColor = (confidence: string) => {
  switch ((confidence || "").toLowerCase()) {
    case "high": return "#2ecc71";
    case "medium": return "#f1c40f";
    case "low": return "#e67e22";
    default: return "#3498db";
  }
};

/**
 * Single stable card combining recommendation, detection context, install
 * status, and safety warnings. Always keeps the same structure so the panel
 * does not jump around while data loads or refreshes.
 */
export const GameStatusCard = ({ loading, recommendation, context, hdrStatus, hdrInstalled, methodLabel }: GameStatusCardProps) => {
  const accent = recommendation ? confidenceColor(recommendation.confidence) : "rgba(255,255,255,0.25)";

  return (
    <PanelSectionRow>
      <div style={{
        padding: "10px 12px",
        borderRadius: "6px",
        backgroundColor: "rgba(255, 255, 255, 0.05)",
        borderLeft: `4px solid ${accent}`,
        width: "100%",
        boxSizing: "border-box",
        overflowWrap: "anywhere",
        minHeight: "108px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", minWidth: 0 }}>
          <div style={{ fontWeight: "bold", color: accent, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", fontSize: "0.92em" }}>
            {loading && !recommendation ? "Analyzing game…" : methodLabel || "No recommendation"}
          </div>
          <div style={{
            flexShrink: 0,
            fontSize: "0.72em",
            fontWeight: 700,
            padding: "2px 8px",
            borderRadius: "10px",
            border: `1px solid ${hdrInstalled ? "rgba(76,175,80,0.6)" : "rgba(255,255,255,0.25)"}`,
            color: hdrInstalled ? "#2ecc71" : "rgba(255,255,255,0.6)",
          }}>
            {loading ? "Refreshing…" : hdrInstalled ? "HDR installed" : "Not installed"}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "2px 8px", fontSize: "0.76em", opacity: 0.7, marginTop: "6px" }}>
          <div>API: {context?.graphics_api || "…"}</div>
          <div>Hook: {context?.injection_dll || "…"}</div>
          <div>Engine: {context?.engine || "…"}</div>
          <div>Arch: {context?.architecture || "…"}</div>
        </div>

        <div style={{ fontSize: "0.84em", marginTop: "6px", color: "#eee", lineHeight: 1.3 }}>
          {recommendation?.reason || (loading ? "Detecting graphics API, engine, and HDR support…" : "Select a game to analyze.")}
        </div>

        {hdrStatus?.status === "success" && hdrStatus.message && (
          <div style={{ fontSize: "0.76em", opacity: 0.62, marginTop: "4px" }}>
            {hdrStatus.message}{hdrStatus.method ? ` (${hdrStatus.method})` : ""}
          </div>
        )}

        {context?.anti_cheat?.length ? (
          <div style={{
            marginTop: "8px",
            padding: "8px",
            backgroundColor: "rgba(231, 76, 60, 0.16)",
            borderRadius: "4px",
            border: "1px solid rgba(231,76,60,0.6)",
            fontSize: "0.8em",
            color: "#ff6b6b",
          }}>
            Anti-cheat detected: {context.anti_cheat.join(", ")}. Injection is blocked for your safety.
          </div>
        ) : null}

        {recommendation?.notes?.slice(0, 3).map((note: string, i: number) => (
          <div key={i} style={{ fontSize: "0.74em", opacity: 0.55, marginTop: "3px", fontStyle: "italic" }}>
            {note}
          </div>
        ))}
      </div>
    </PanelSectionRow>
  );
};
