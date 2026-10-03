// Small presentational pieces. Anything interactive uses Decky components so the
// gamepad can reach it; these divs are display-only.
import type { CSSProperties, ReactNode } from "react";
import { PanelSectionRow } from "@decky/ui";

export const COLORS = {
  good: "#59bf40",
  warn: "#e8a33d",
  bad: "#e5534b",
  info: "#1a9fff",
  dim: "rgba(255,255,255,0.55)",
  faint: "rgba(255,255,255,0.08)",
};

export function Card({ accent, children, style }: { accent?: string; children: ReactNode; style?: CSSProperties }) {
  return (
    <PanelSectionRow>
      <div
        style={{
          width: "100%", boxSizing: "border-box", padding: "10px 12px", borderRadius: 6,
          background: "rgba(255,255,255,0.05)", borderLeft: accent ? `4px solid ${accent}` : undefined,
          overflowWrap: "anywhere", ...style,
        }}
      >
        {children}
      </div>
    </PanelSectionRow>
  );
}

export function Notice({ tone, title, children }: { tone: "warn" | "bad" | "info" | "good"; title?: ReactNode; children?: ReactNode }) {
  const color = COLORS[tone];
  return (
    <div style={{ padding: "8px 10px", borderRadius: 4, border: `1px solid ${color}88`, background: `${color}1f`, fontSize: 12, lineHeight: 1.35 }}>
      {title && <div style={{ fontWeight: 700, color, marginBottom: children ? 2 : 0 }}>{title}</div>}
      {children}
    </div>
  );
}

export function Pill({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span style={{ display: "inline-block", fontSize: 11, fontWeight: 700, padding: "1px 7px", borderRadius: 10, border: `1px solid ${color}99`, color, whiteSpace: "nowrap" }}>
      {children}
    </span>
  );
}

export function Small({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ fontSize: 12, color: COLORS.dim, lineHeight: 1.35, ...style }}>{children}</div>;
}

export function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12, lineHeight: 1.4 }}>
      {items.map((item, index) => <li key={index}>{item}</li>)}
    </ol>
  );
}

export function Spin() {
  return (
    <span style={{ display: "inline-block", width: 12, height: 12, marginRight: 8, verticalAlign: "-2px", border: "2px solid #ffffff44", borderTopColor: "#fff", borderRadius: "50%", animation: "dk-spin 0.8s linear infinite" }}>
      <style>{"@keyframes dk-spin { to { transform: rotate(360deg); } }"}</style>
    </span>
  );
}
