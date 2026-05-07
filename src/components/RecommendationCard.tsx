import { PanelSectionRow } from "@decky/ui";

interface RecommendationCardProps {
  recommendation: any;
  context: any;
  methodLabel: string;
  getConfidenceColor: (confidence: string) => string;
}

export const RecommendationCard = ({ recommendation, context, methodLabel, getConfidenceColor }: RecommendationCardProps) => {
  if (!recommendation) return null;

  return (
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
            {methodLabel}
          </div>
          <div style={{ fontSize: "0.8em", opacity: 0.5, flexShrink: 0 }}>Score: {recommendation.score}</div>
        </div>
        {context && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 8px", fontSize: "0.78em", opacity: 0.7, marginTop: "6px" }}>
            <div>API: {context.graphics_api || "unknown"}</div>
            <div>Hook: {context.injection_dll || "auto"}</div>
            <div>Engine: {context.engine || "unknown"}</div>
            <div>Arch: {context.architecture || "unknown"}</div>
          </div>
        )}
        <div style={{ fontSize: "0.9em", marginTop: "4px", color: "#eee" }}>
          {recommendation.reason}
        </div>
        {recommendation.notes?.map((note: string, i: number) => (
          <div key={i} style={{ fontSize: "0.8em", opacity: 0.6, marginTop: "2px", fontStyle: "italic" }}>
            - {note}
          </div>
        ))}
      </div>
    </PanelSectionRow>
  );
};
