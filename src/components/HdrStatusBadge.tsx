import { PanelSectionRow } from "@decky/ui";

interface HdrStatusBadgeProps {
  hdrStatus: any;
  hdrInstalled: boolean;
}

export const HdrStatusBadge = ({ hdrStatus, hdrInstalled }: HdrStatusBadgeProps) => {
  if (!hdrStatus || hdrStatus.status !== "success") return null;

  return (
    <PanelSectionRow>
      <div style={{
        padding: "10px",
        borderRadius: "4px",
        border: `1px solid ${hdrInstalled ? "#4CAF50" : "rgba(255,255,255,0.16)"}`,
        background: hdrInstalled ? "rgba(76,175,80,0.12)" : "rgba(255,255,255,0.04)",
        fontSize: "0.84em",
        lineHeight: 1.25,
        overflowWrap: "anywhere"
      }}>
        <div style={{ fontWeight: 700 }}>{hdrInstalled ? "HDR Files Present" : "No HDR Injection Found"}</div>
        <div>{hdrStatus.message}</div>
        {hdrStatus.method && <div style={{ opacity: 0.7 }}>Method: {hdrStatus.method}</div>}
        {hdrInstalled && <div style={{ opacity: 0.62, marginTop: "3px" }}>In-game HDR still needs verification unless this method is marked verified.</div>}
      </div>
    </PanelSectionRow>
  );
};
